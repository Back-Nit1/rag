import { describe, expect, it, vi } from "vitest";
import {
  type CaptureDetail,
  type ExtractionProposal,
  ProposalDisposition,
  type Reminder,
  ReminderState,
  type SourceArtifact,
  SourceKind,
  SourceLifecycle,
} from "../contracts/metis";
import { ApiError, OfflineError } from "./api";
import { ErrorCode } from "../contracts/metis";
import {
  errorState,
  isTerminal,
  pendingProposal,
  pollCapture,
  resolveAndReadReminders,
  stateFromCapture,
  submitCapture,
  type FlowDeps,
} from "./captureFlow";

const source = (over: Partial<SourceArtifact> = {}): SourceArtifact => ({
  id: "src_1",
  user_id: "demo",
  created_at: "2026-10-05T12:00:00Z",
  updated_at: "2026-10-05T12:00:00Z",
  kind: SourceKind.TEXT,
  lifecycle: SourceLifecycle.READY,
  capture_timezone: "America/New_York",
  captured_at: "2026-10-05T12:00:00Z",
  current_version: 1,
  revision: 1,
  ...over,
});

const proposal = (over: Partial<ExtractionProposal> = {}): ExtractionProposal => ({
  id: "prop_1",
  user_id: "demo",
  created_at: "2026-10-05T12:00:00Z",
  updated_at: "2026-10-05T12:00:00Z",
  source_id: "src_1",
  source_version: 1,
  revision: 1,
  items: [],
  extractor_version: "extract-1",
  model_id: "fixture-inference",
  adapter_mode: "fixture" as ExtractionProposal["adapter_mode"],
  prompt_version: "extract/v1",
  disposition: ProposalDisposition.PENDING,
  ...over,
});

function deps(api: Partial<FlowDeps["api"]>): FlowDeps {
  return {
    api: api as FlowDeps["api"],
    now: () => new Date("2026-10-05T12:00:00Z"),
    sleep: () => Promise.resolve(),
    timezone: "America/New_York",
  };
}

describe("stateFromCapture", () => {
  it("does not call an uploaded recording transcribed", () => {
    const detail: CaptureDetail = { source: source({ kind: SourceKind.AUDIO, lifecycle: SourceLifecycle.UPLOADED }) };
    expect(stateFromCapture(detail)).toBe("queued");
  });

  it("reports transcribing only while audio has no transcript yet", () => {
    const audio = source({ kind: SourceKind.AUDIO, lifecycle: SourceLifecycle.PROCESSING });
    expect(stateFromCapture({ source: audio })).toBe("transcribing");
    expect(stateFromCapture({ source: audio, transcript_text: "hello" })).toBe("extraction_pending");
  });

  it("treats a pending proposal that needs clarification as a question", () => {
    const detail: CaptureDetail = {
      source: source(),
      transcript_text: "note",
      proposals: [proposal({ requires_clarification: true })],
    };
    expect(stateFromCapture(detail)).toBe("clarification_required");
  });

  it("is ready for review when a pending proposal needs nothing", () => {
    const detail: CaptureDetail = {
      source: source(),
      transcript_text: "note",
      proposals: [proposal({ requires_clarification: false })],
    };
    expect(stateFromCapture(detail)).toBe("transcript_ready");
    expect(pendingProposal(detail)?.id).toBe("prop_1");
  });

  it("is saved once every proposal was resolved", () => {
    const detail: CaptureDetail = {
      source: source(),
      transcript_text: "note",
      proposals: [proposal({ disposition: ProposalDisposition.ACCEPTED })],
    };
    expect(stateFromCapture(detail)).toBe("saved");
    expect(pendingProposal(detail)).toBeUndefined();
  });

  it("surfaces a failed source as failed, not as still working", () => {
    expect(stateFromCapture({ source: source({ lifecycle: SourceLifecycle.FAILED }) })).toBe("failed");
    expect(isTerminal("failed")).toBe(true);
    expect(isTerminal("transcribing")).toBe(false);
  });
});

describe("submitCapture", () => {
  it("sends a text note with a stable idempotency key and follows it to review", async () => {
    const createCapture = vi.fn().mockResolvedValue({ capture_id: "src_1", revision: 1 });
    const getCapture = vi
      .fn()
      .mockResolvedValueOnce({ source: source({ lifecycle: SourceLifecycle.READY }), transcript_text: "note" })
      .mockResolvedValueOnce({
        source: source(),
        transcript_text: "note",
        proposals: [proposal({ requires_clarification: true })],
      });
    const seen: string[] = [];

    const detail = await submitCapture(
      deps({ createCapture, getCapture }),
      { kind: "text", text: "remind me" },
      "cc_42",
      (state) => seen.push(state),
    );

    expect(createCapture).toHaveBeenCalledWith(
      expect.objectContaining({ kind: SourceKind.TEXT, text: "remind me", client_capture_id: "cc_42" }),
      "capture:cc_42",
    );
    expect(seen).toEqual(["queued", "extraction_pending", "clarification_required"]);
    expect(pendingProposal(detail)?.id).toBe("prop_1");
  });

  it("uploads audio before completing it, and only then waits for a transcript", async () => {
    const order: string[] = [];
    const createCapture = vi.fn(async () => {
      order.push("create");
      return {
        capture_id: "src_1",
        revision: 1,
        upload: { url: "/v1/local-uploads/src_1", object_key: "k", expires_at: "", max_bytes: 10 },
      };
    });
    const uploadBytes = vi.fn(async () => void order.push("upload"));
    const completeCapture = vi.fn(async () => {
      order.push("complete");
      return { capture_id: "src_1", job_id: "job_1", revision: 2 };
    });
    const getCapture = vi.fn(async () => {
      order.push("poll");
      return {
        source: source({ kind: SourceKind.AUDIO }),
        transcript_text: "spoken",
        proposals: [proposal()],
      };
    });

    const seen: string[] = [];
    await submitCapture(
      deps({ createCapture, uploadBytes, completeCapture, getCapture }),
      { kind: "audio", blob: new Blob(["xx"], { type: "audio/webm" }), durationSeconds: 3.4 },
      "cc_audio",
      (state) => seen.push(state),
    );

    expect(order).toEqual(["create", "upload", "complete", "poll"]);
    expect(completeCapture).toHaveBeenCalledWith("src_1", expect.objectContaining({ expected_revision: 1 }), "complete:cc_audio");
    expect(seen.slice(0, 4)).toEqual(["upload_pending", "queued", "transcribing", "transcript_ready"]);
  });

  it("reports offline rather than failure when nothing reached the server", async () => {
    const createCapture = vi.fn().mockRejectedValue(new OfflineError());
    const seen: string[] = [];
    await expect(
      submitCapture(deps({ createCapture }), { kind: "text", text: "x" }, "cc_1", (s) => seen.push(s)),
    ).rejects.toBeInstanceOf(OfflineError);
    expect(seen.at(-1)).toBe("offline");
  });

  it("maps a budget refusal to its own state so the user is not told to retry", () => {
    const error = new ApiError(402, { code: ErrorCode.BUDGET_EXCEEDED, message: "limit reached" });
    expect(errorState(error)).toBe("budget_exceeded");
  });
});

describe("pollCapture", () => {
  it("stops at the timeout and still reports queued rather than lost", async () => {
    let calls = 0;
    const times = [0, 1_000, 60_000];
    const api = {
      getCapture: vi.fn(async () => {
        calls += 1;
        return { source: source({ lifecycle: SourceLifecycle.PROCESSING, kind: SourceKind.AUDIO }) };
      }),
    };
    const flowDeps: FlowDeps = {
      api: api as unknown as FlowDeps["api"],
      now: () => new Date(times[Math.min(calls, times.length - 1)] ?? 0),
      sleep: () => Promise.resolve(),
      timezone: "UTC",
    };
    const seen: string[] = [];
    await pollCapture(flowDeps, "src_1", (state) => seen.push(state), { timeoutMs: 10_000 });
    expect(seen.at(-1)).toBe("queued");
  });
});

describe("resolveAndReadReminders", () => {
  it("reads the real reminder state back instead of assuming it was scheduled", async () => {
    const reminder: Reminder = {
      id: "rem_1",
      user_id: "demo",
      created_at: "2026-10-05T12:00:00Z",
      updated_at: "2026-10-05T12:00:00Z",
      target_id: "task_1",
      target_kind: "task",
      target_revision: 1,
      trigger_at: "2026-10-06T13:00:00Z",
      timezone: "America/New_York",
      occurrence_key: "rem_1:1",
      state: ReminderState.PENDING_SCHEDULE,
    };
    const resolveProposal = vi.fn().mockResolvedValue({
      proposal: proposal({ disposition: ProposalDisposition.ACCEPTED }),
      created_task_ids: ["task_1"],
      created_reminder_ids: ["rem_1"],
      clarifications: [],
    });
    const getReminder = vi.fn().mockResolvedValue(reminder);

    const outcome = await resolveAndReadReminders(
      deps({ resolveProposal, getReminder }),
      proposal({ revision: 3 }),
      [{ item_index: 0, action: "accept" }],
    );

    expect(resolveProposal).toHaveBeenCalledWith(
      "prop_1",
      { expected_revision: 3, decisions: [{ item_index: 0, action: "accept" }] },
      "resolve:prop_1:3",
    );
    expect(outcome.reminders[0]?.state).toBe(ReminderState.PENDING_SCHEDULE);
  });
});
