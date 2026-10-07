/**
 * Capture: the whole slice in one surface.
 *
 * text/audio capture -> processing state -> proposal review -> clarification
 * -> created task/event -> reminder state -> notification visibility.
 *
 * Rules this screen keeps: unsent input is never discarded, an uploaded
 * recording is never described as transcribed, a missing field is never
 * filled in, and the microphone is never the only way in.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  type CaptureDetail,
  type ClarificationRequest,
  type Event as MetisEvent,
  type ItemDecision,
  type Reminder,
  type Task,
} from "../contracts/metis";
import { Button } from "../components/Button";
import { Card, Notice, PageHeader, SectionHeading } from "../components/Card";
import { Icon } from "../components/Icon";
import { ClarificationPrompt } from "../components/ClarificationPrompt";
import { CreatedRecords } from "../components/CreatedRecords";
import { ProposalReview } from "../components/ProposalReview";
import { StatusPill, LiveStatus } from "../components/StatusPill";
import { TranscriptPanel } from "../components/TranscriptPanel";
import { useSession } from "../app/session";
import { ApiError, api } from "../lib/api";
import {
  defaultDeps,
  errorState,
  pendingProposal,
  pollCapture,
  resolveAndReadReminders,
  stateFromCapture,
  submitCapture,
} from "../lib/captureFlow";
import { bytes, clock } from "../lib/format";
import { Outbox, newClientCaptureId, type OutboxEntry } from "../lib/outbox";
import { FLOW_STATUS, type FlowState } from "../lib/status";
import { useRecorder, type Recording } from "../lib/useRecorder";

export function CaptureScreen() {
  const session = useSession();
  const { limits, timezone, online, notificationPermission } = session;

  const [flow, setFlow] = useState<FlowState>("idle");
  const [capture, setCapture] = useState<CaptureDetail | null>(null);
  const [text, setText] = useState("");
  const [heldAudio, setHeldAudio] = useState<Recording | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);
  const [highlightSpan, setHighlightSpan] = useState<string | null>(null);
  const [resolving, setResolving] = useState(false);
  const [correcting, setCorrecting] = useState(false);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [events, setEvents] = useState<MetisEvent[]>([]);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [clarifications, setClarifications] = useState<ClarificationRequest[]>([]);
  const [queued, setQueued] = useState<OutboxEntry[]>([]);

  const outbox = useRef(new Outbox());
  const deps = useRef(defaultDeps());
  const recorder = useRecorder(limits.max_audio_seconds);

  useEffect(() => {
    deps.current = { ...deps.current, timezone };
  }, [timezone]);

  useEffect(() => setQueued(outbox.current.list()), []);

  const progress = useCallback((state: FlowState, detail?: CaptureDetail) => {
    setFlow(state);
    if (detail) setCapture(detail);
  }, []);

  const reset = () => {
    setError(null);
    setConflict(null);
    setTasks([]);
    setEvents([]);
    setReminders([]);
    setClarifications([]);
    setHighlightSpan(null);
  };

  // ----- sending ---------------------------------------------------------- //

  const sendText = async (value: string, clientCaptureId: string) => {
    await submitCapture(deps.current, { kind: "text", text: value }, clientCaptureId, progress);
  };

  const submitText = async () => {
    const value = text.trim();
    if (!value) return;
    reset();
    const clientCaptureId = newClientCaptureId();

    if (!online) {
      // Held, not saved: localStorage can be evicted, so the wording stays honest.
      try {
        outbox.current.add({
          client_capture_id: clientCaptureId,
          text: value,
          capture_timezone: timezone,
          captured_at: new Date().toISOString(),
        });
        setQueued(outbox.current.list());
        setText("");
        setFlow("offline");
      } catch (cause) {
        setFlow("failed");
        setError(cause instanceof Error ? cause.message : "the outbox is full");
      }
      return;
    }

    try {
      await sendText(value, clientCaptureId);
      setText("");
    } catch (cause) {
      setError(describe(cause));
      if (errorState(cause) === "offline") {
        outbox.current.add({
          client_capture_id: clientCaptureId,
          text: value,
          capture_timezone: timezone,
          captured_at: new Date().toISOString(),
        });
        setQueued(outbox.current.list());
        setText("");
      }
    }
  };

  const sendAudio = async (recording: Recording) => {
    reset();
    setHeldAudio(recording);
    try {
      await submitCapture(
        deps.current,
        { kind: "audio", blob: recording.blob, durationSeconds: recording.durationSeconds },
        newClientCaptureId(),
        progress,
      );
      setHeldAudio(null);
    } catch (cause) {
      // The recording stays in memory so "retry" means the same bytes, not a
      // request to say it all again.
      setError(describe(cause));
    }
  };

  const flush = useCallback(async () => {
    const result = await outbox.current.flush(async (entry) => {
      await sendText(entry.text, entry.client_capture_id);
    });
    setQueued(outbox.current.list());
    if (result.kept > 0) setFlow("offline");
  }, []);

  useEffect(() => {
    if (online && outbox.current.list().length > 0) void flush();
  }, [online, flush]);

  // ----- review ----------------------------------------------------------- //

  const proposal = pendingProposal(capture ?? undefined);

  const resolve = async (decisions: ItemDecision[]) => {
    if (!proposal) return;
    setResolving(true);
    setError(null);
    try {
      const outcome = await resolveAndReadReminders(deps.current, proposal, decisions);
      setReminders(outcome.reminders);
      setClarifications(outcome.clarifications ?? []);
      const [loadedTasks, loadedEvents] = await Promise.all([
        Promise.all((outcome.created_task_ids ?? []).map((id) => api.getTask(id))),
        Promise.all((outcome.created_event_ids ?? []).map((id) => api.getEvent(id))),
      ]);
      setTasks(loadedTasks);
      setEvents(loadedEvents);
      const refreshed = await api.getCapture(proposal.source_id);
      setCapture(refreshed);
      setFlow((outcome.clarifications ?? []).length > 0 ? "clarification_required" : "saved");
      void session.refreshNotifications();
    } catch (cause) {
      setFlow(errorState(cause));
      setError(describe(cause));
    } finally {
      setResolving(false);
    }
  };

  const saveCorrection = async (corrected: string) => {
    if (!capture) return;
    setCorrecting(true);
    setConflict(null);
    try {
      const updated = await api.patchTranscript(capture.source.id, {
        expected_revision: capture.source.revision ?? 1,
        text: corrected,
      });
      setCapture(updated);
      setFlow(stateFromCapture(updated));
      // A correction supersedes the old proposal; follow the new one.
      await pollCapture(deps.current, updated.source.id, progress);
    } catch (cause) {
      if (cause instanceof ApiError && cause.currentRevision !== null) {
        setConflict(
          `This note changed elsewhere (now revision ${cause.currentRevision}). Your edit is still here — reload the note or save again to overwrite.`,
        );
      } else {
        setError(describe(cause));
      }
    } finally {
      setCorrecting(false);
    }
  };

  const refreshReminders = async () => {
    setReminders(await Promise.all(reminders.map((r) => api.getReminder(r.id))));
  };

  // ----- recording controls ----------------------------------------------- //

  const remaining = Math.max(0, limits.max_audio_seconds - recorder.seconds);
  const status = FLOW_STATUS[recorder.state === "recording" ? "recording" : recorder.state === "paused" ? "paused" : recorder.state === "denied" ? "mic_denied" : flow];
  const live = recorder.state === "recording" || recorder.state === "paused";
  const step = STEP_OF[flow] ?? -1;

  return (
    <div className="flex flex-col gap-5">
      <LiveStatus status={status} />
      <PageHeader
        title="Capture"
        sub="Speak or type. Nothing becomes a task until you have reviewed it."
      />

      <Card>
        <label className="flex flex-col gap-1.5">
          <span className="flex flex-wrap items-baseline justify-between gap-2 text-xs">
            <span className="font-medium text-ink">Type a note</span>
            <span className="text-muted">
              Up to {Math.floor(limits.max_audio_seconds / 60)} min of audio, {bytes(limits.max_upload_bytes)}
            </span>
          </span>
          <textarea
            className="note-field min-h-36"
            placeholder="Register for the half marathon by March 3rd, remind me at 9am on March 1…"
            value={text}
            maxLength={limits.max_text_chars}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") void submitText();
            }}
          />
        </label>

        {live ? (
          <div
            className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-3 rounded-lg border border-record/60 bg-record-soft px-4 py-3"
            role="group"
            aria-label="Recording controls"
          >
            <span className="flex items-center gap-2.5 text-record">
              <span
                className={`size-3 rounded-full bg-record ${recorder.state === "recording" ? "rec-ring" : ""}`}
                aria-hidden="true"
              />
              <span className="text-sm font-semibold">{recorder.state === "recording" ? "Recording" : "Paused"}</span>
            </span>
            <span className="font-mono text-lg text-ink" aria-live="off">
              {clock(recorder.seconds)}
              <span className="text-sm text-muted"> / {clock(limits.max_audio_seconds)}</span>
            </span>
            {remaining <= 30 && (
              <span className="text-xs font-medium text-warn">{clock(remaining)} left before the limit stops it</span>
            )}
            <span className="flex flex-wrap gap-2 sm:ml-auto">
              {recorder.state === "recording" ? (
                <Button onClick={recorder.pause}>
                  <Icon name="pause" size={16} /> Pause
                </Button>
              ) : (
                <Button onClick={recorder.resume}>Resume</Button>
              )}
              <Button
                variant="primary"
                onClick={async () => {
                  const recording = await recorder.stop();
                  if (recording) await sendAudio(recording);
                  else setFlow("canceled");
                }}
              >
                <Icon name="stop" size={16} /> Stop and send
              </Button>
              <Button
                variant="danger"
                onClick={() => {
                  recorder.cancel();
                  setFlow("canceled");
                }}
              >
                Cancel
              </Button>
            </span>
          </div>
        ) : (
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={!recorder.supported}
              onClick={() => void recorder.start()}
              className="inline-flex min-h-14 items-center gap-3 rounded-full border-2 border-accent bg-surface py-1.5 pl-1.5 pr-5 text-sm font-semibold text-accent-ink transition-colors hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-50"
            >
              <span className="grid size-11 place-items-center rounded-full bg-accent text-on-accent">
                <Icon name="mic" size={22} />
              </span>
              Record a thought
            </button>
            <Button variant="primary" className="ml-auto min-h-12 px-5 md:min-h-11" disabled={!text.trim()} onClick={() => void submitText()}>
              <Icon name="send" size={16} /> Send note
            </Button>
            <span className="hidden text-xs text-muted sm:inline">or press ⌘/Ctrl + Enter</span>
          </div>
        )}

        <div className="mt-4 border-t border-line pt-3">
          <StatusPill status={status} />
          {step >= 0 && (
            <ol className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs" aria-label="Progress">
              {STEPS.map((label, i) => (
                <li
                  key={label}
                  aria-current={i === step ? "step" : undefined}
                  className={i === step ? "font-semibold text-accent-ink" : i < step ? "text-ink" : "text-muted"}
                >
                  <span aria-hidden="true" className="mr-1 font-mono">
                    {i < step ? "✓" : i === step ? "●" : "○"}
                  </span>
                  {label}
                  {i < step && <span className="visually-hidden"> (done)</span>}
                </li>
              ))}
            </ol>
          )}
        </div>

        <div className="mt-3 flex flex-col gap-2 empty:hidden">
          {!recorder.supported && (
            <Notice tone="info">This browser has no MediaRecorder support, so voice capture is off. Typing does the same thing.</Notice>
          )}
          {recorder.state === "denied" && (
            <Notice tone="warn">
              Microphone access was blocked. Allow it in the browser&apos;s site settings to record; typed capture works
              meanwhile.
            </Notice>
          )}
          {recorder.error && recorder.state !== "denied" && <Notice tone="warn">{recorder.error}</Notice>}
          {!online && <Notice tone="warn">Offline: a note you send is held on this device, not saved.</Notice>}
          {flow === "budget_exceeded" && (
            <Notice tone="warn">The daily cost limit stopped the paid step. Typed notes and reminders still work.</Notice>
          )}
          {heldAudio && (
            <Notice
              tone="warn"
              action={
                <span className="flex flex-wrap gap-2">
                  <Button onClick={() => void sendAudio(heldAudio)}>Retry upload</Button>
                  <Button variant="quiet" onClick={() => setHeldAudio(null)}>
                    Discard recording
                  </Button>
                </span>
              }
            >
              A {clock(heldAudio.durationSeconds)} recording ({bytes(heldAudio.blob.size)}) is still unsent. It is held
              in memory only; closing this tab loses it.
            </Notice>
          )}
          {error && (
            <Notice tone="bad" live>
              {error}
            </Notice>
          )}
          {notificationPermission !== "granted" && (
            <p className="text-xs text-muted">
              {notificationPermission === "unsupported"
                ? "This browser cannot show system notifications; reminders will appear in the Alerts feed."
                : FLOW_STATUS.notifications_disabled.detail}{" "}
              {notificationPermission !== "unsupported" && (
                <button
                  type="button"
                  className="font-medium text-accent underline underline-offset-2"
                  onClick={() => void session.requestNotificationPermission()}
                >
                  Enable notifications
                </button>
              )}
            </p>
          )}
        </div>
      </Card>

      {queued.length > 0 && (
        <Card>
          <SectionHeading hint="held on this device, not saved">Unsent notes</SectionHeading>
          <ul className="flex flex-col gap-2">
            {queued.map((entry) => (
              <li key={entry.client_capture_id} className="rounded-md border border-warn/50 bg-warn-soft px-3 py-2 text-warn">
                <p className="truncate font-serif text-base text-ink">{entry.text}</p>
                <p className="text-xs">
                  Not sent: {entry.attempts} attempt{entry.attempts === 1 ? "" : "s"}
                  {entry.last_error ? ` · ${entry.last_error}` : ""}
                </p>
              </li>
            ))}
          </ul>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <Button disabled={!online} onClick={() => void flush()}>
              Sync now
            </Button>
            <span className="text-xs text-muted">
              Browser storage can be cleared by the device; these are unsent, not backed up.
            </span>
          </div>
        </Card>
      )}

      {capture && (
        <TranscriptPanel
          capture={capture}
          highlightSpan={highlightSpan}
          conflict={conflict}
          busy={correcting}
          onSave={(value) => void saveCorrection(value)}
        />
      )}

      {proposal && (
        <ProposalReview
          key={`${proposal.id}:${proposal.revision}`}
          proposal={proposal}
          timezone={timezone}
          busy={resolving}
          onResolve={(decisions) => void resolve(decisions)}
          onShowEvidence={(span) => setHighlightSpan(span ?? null)}
        />
      )}

      {clarifications.map((clarification) => (
        <ClarificationPrompt
          key={clarification.clarification_id}
          clarification={clarification}
          timezone={timezone}
          onAnswered={(reminder) => setReminders((current) => [...current, reminder])}
        />
      ))}

      <CreatedRecords
        tasks={tasks}
        events={events}
        reminders={reminders}
        timezone={timezone}
        onRefreshReminders={() => void refreshReminders()}
      />
    </div>
  );
}

// The pipeline is a real sequence, so a stepper is honest here. Uploaded is
// "Received", which is deliberately a different step from "Transcribed".
const STEPS = ["Received", "Transcribing", "Extracting", "Review", "Saved"];
const STEP_OF: Partial<Record<FlowState, number>> = {
  upload_pending: 0,
  queued: 0,
  transcribing: 1,
  extraction_pending: 2,
  transcript_ready: 3,
  clarification_required: 3,
  saved: 4,
};

function describe(cause: unknown): string {
  if (cause instanceof ApiError) return `${cause.message} (${cause.code})`;
  if (cause instanceof Error) return cause.message;
  return "something went wrong; your input was not discarded";
}
