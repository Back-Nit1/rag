/**
 * The capture slice, as logic rather than components.
 *
 * text/audio capture -> processing -> proposal review -> clarification ->
 * created task/event -> reminder state.
 *
 * It lives apart from React so the whole flow is testable without a DOM, and
 * so every status the user sees comes from one derivation of server state
 * rather than from optimistic guesses scattered through the views.
 */
import {
  type CaptureDetail,
  type ExtractionProposal,
  type ItemDecision,
  ProposalDisposition,
  type Reminder,
  type ResolveProposalResponse,
  SourceKind,
  SourceLifecycle,
} from "../contracts/metis";
import { ApiError, OfflineError, api } from "./api";
import { type FlowState, flowStateForError } from "./status";

export type CaptureInput =
  | { kind: "text"; text: string }
  | { kind: "audio"; blob: Blob; durationSeconds: number };

export type FlowDeps = {
  api: Pick<
    typeof api,
    "createCapture" | "completeCapture" | "getCapture" | "uploadBytes" | "resolveProposal" | "getReminder"
  >;
  now: () => Date;
  sleep: (ms: number) => Promise<void>;
  timezone: string;
};

export const defaultDeps = (): FlowDeps => ({
  api,
  now: () => new Date(),
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
});

export type Progress = (state: FlowState, detail?: CaptureDetail) => void;

/** The proposal the user should be reviewing, if any. */
export function pendingProposal(detail: CaptureDetail | undefined): ExtractionProposal | undefined {
  return detail?.proposals?.find(
    (p) => (p.disposition ?? ProposalDisposition.PENDING) === ProposalDisposition.PENDING,
  );
}

/**
 * One honest status for a capture, derived only from server state.
 *
 * `uploaded` stays "queued" rather than "transcribed", and a proposal that
 * carries `requires_clarification` is a question, not a result.
 */
export function stateFromCapture(detail: CaptureDetail): FlowState {
  const source = detail.source;
  if (source.lifecycle === SourceLifecycle.FAILED) return "failed";
  if (source.lifecycle === SourceLifecycle.TOMBSTONED) return "canceled";

  const proposal = pendingProposal(detail);
  if (proposal) return proposal.requires_clarification ? "clarification_required" : "transcript_ready";

  const resolved = detail.proposals?.some(
    (p) => (p.disposition ?? ProposalDisposition.PENDING) !== ProposalDisposition.PENDING,
  );
  if (resolved) return "saved";

  if (source.lifecycle === SourceLifecycle.PENDING_UPLOAD) return "upload_pending";
  if (source.lifecycle === SourceLifecycle.UPLOADED) return "queued";
  if (source.lifecycle === SourceLifecycle.PROCESSING) {
    return source.kind === SourceKind.AUDIO && !detail.transcript_text ? "transcribing" : "extraction_pending";
  }
  // `ready` with no proposal yet: the transcript exists and extraction is queued.
  return detail.transcript_text ? "extraction_pending" : "queued";
}

/** True once the capture needs the user again (or has definitively stopped). */
export function isTerminal(state: FlowState): boolean {
  return (
    state === "transcript_ready" ||
    state === "clarification_required" ||
    state === "saved" ||
    state === "failed" ||
    state === "canceled"
  );
}

export type PollOptions = { intervalMs?: number; timeoutMs?: number };

export async function pollCapture(
  deps: FlowDeps,
  captureId: string,
  onProgress: Progress,
  { intervalMs = 900, timeoutMs = 45_000 }: PollOptions = {},
): Promise<CaptureDetail> {
  const deadline = deps.now().getTime() + timeoutMs;
  let last: CaptureDetail | undefined;
  for (;;) {
    last = await deps.api.getCapture(captureId);
    const state = stateFromCapture(last);
    onProgress(state, last);
    if (isTerminal(state)) return last;
    if (deps.now().getTime() >= deadline) {
      // Honest about what is known: the work is still queued, not lost.
      onProgress("queued", last);
      return last;
    }
    await deps.sleep(intervalMs);
  }
}

/**
 * Create a capture and follow it until it needs the user.
 *
 * `clientCaptureId` is the idempotency anchor for the whole attempt: the same
 * id replays the same capture, the same upload target and the same job.
 */
export async function submitCapture(
  deps: FlowDeps,
  input: CaptureInput,
  clientCaptureId: string,
  onProgress: Progress,
  pollOptions?: PollOptions,
): Promise<CaptureDetail> {
  const capturedAt = deps.now().toISOString();
  try {
    if (input.kind === "text") {
      onProgress("queued");
      const created = await deps.api.createCapture(
        {
          kind: SourceKind.TEXT,
          text: input.text,
          capture_timezone: deps.timezone,
          captured_at: capturedAt,
          client_capture_id: clientCaptureId,
        },
        `capture:${clientCaptureId}`,
      );
      return await pollCapture(deps, created.capture_id, onProgress, pollOptions);
    }

    onProgress("upload_pending");
    const created = await deps.api.createCapture(
      {
        kind: SourceKind.AUDIO,
        capture_timezone: deps.timezone,
        captured_at: capturedAt,
        content_type: input.blob.type || "audio/webm",
        expected_bytes: input.blob.size,
        duration_seconds: Math.round(input.durationSeconds),
        client_capture_id: clientCaptureId,
      },
      `capture:${clientCaptureId}`,
    );
    if (!created.upload) {
      throw new Error("the server accepted the recording but returned no upload target");
    }
    await deps.api.uploadBytes(created.upload, input.blob);
    onProgress("queued");
    await deps.api.completeCapture(
      created.capture_id,
      { expected_revision: created.revision, actual_bytes: input.blob.size },
      `complete:${clientCaptureId}`,
    );
    onProgress("transcribing");
    return await pollCapture(deps, created.capture_id, onProgress, pollOptions);
  } catch (error) {
    onProgress(errorState(error));
    throw error;
  }
}

export function errorState(error: unknown): FlowState {
  if (error instanceof OfflineError) return "offline";
  if (error instanceof ApiError) return flowStateForError(error.code);
  return "failed";
}

export type ResolveOutcome = ResolveProposalResponse & { reminders: Reminder[] };

/** Resolve a proposal, then read back the real state of any reminder it made. */
export async function resolveAndReadReminders(
  deps: FlowDeps,
  proposal: ExtractionProposal,
  decisions: ItemDecision[],
): Promise<ResolveOutcome> {
  const response = await deps.api.resolveProposal(
    proposal.id,
    { expected_revision: proposal.revision ?? 1, decisions },
    `resolve:${proposal.id}:${proposal.revision ?? 1}`,
  );
  const reminders = await Promise.all((response.created_reminder_ids ?? []).map((id) => deps.api.getReminder(id)));
  return { ...response, reminders };
}
