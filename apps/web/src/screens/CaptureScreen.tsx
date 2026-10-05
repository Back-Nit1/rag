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
import { Card, SectionHeading } from "../components/Card";
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

  return (
    <div className="flex flex-col gap-4">
      <LiveStatus status={status} />

      <Card>
        <SectionHeading hint={`${Math.floor(limits.max_audio_seconds / 60)} min max · ${bytes(limits.max_upload_bytes)}`}>
          Capture
        </SectionHeading>

        <div className="flex flex-wrap items-center gap-3">
          {recorder.state === "recording" || recorder.state === "paused" ? (
            <>
              <Button
                variant="primary"
                onClick={async () => {
                  const recording = await recorder.stop();
                  if (recording) await sendAudio(recording);
                  else setFlow("canceled");
                }}
              >
                Stop and send
              </Button>
              {recorder.state === "recording" ? (
                <Button onClick={recorder.pause}>Pause</Button>
              ) : (
                <Button onClick={recorder.resume}>Resume</Button>
              )}
              <Button
                variant="danger"
                onClick={() => {
                  recorder.cancel();
                  setFlow("canceled");
                }}
              >
                Cancel
              </Button>
              <span className="font-mono text-sm text-ink" aria-live="off">
                {clock(recorder.seconds)}
                <span className="text-muted"> / {clock(limits.max_audio_seconds)}</span>
              </span>
              {remaining <= 30 && (
                <span className="text-xs text-warn">{clock(remaining)} left before the limit stops it</span>
              )}
            </>
          ) : (
            <Button
              variant="primary"
              className="min-h-11 px-4"
              disabled={!recorder.supported}
              onClick={() => void recorder.start()}
            >
              <span aria-hidden="true">●</span> Record a thought
            </Button>
          )}
          <StatusPill status={status} />
        </div>

        {!recorder.supported && (
          <p className="mt-2 text-xs text-muted">
            This browser has no MediaRecorder support. Typed capture below does the same thing.
          </p>
        )}
        {recorder.error && <p className="mt-2 text-xs text-warn">{recorder.error}</p>}

        <div className="mt-4">
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Or type it</span>
            <textarea
              className="min-h-24 w-full resize-y rounded-lg border border-line bg-canvas p-3 text-sm text-ink"
              placeholder="Register for the half marathon by March 3rd, remind me at 9am on March 1…"
              value={text}
              maxLength={limits.max_text_chars}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter") void submitText();
              }}
            />
          </label>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Button variant="primary" disabled={!text.trim()} onClick={() => void submitText()}>
              Send note
            </Button>
            <span className="text-xs text-muted">⌘/Ctrl + Enter</span>
            {!online && <span className="text-xs text-warn">Offline — it will be held on this device.</span>}
          </div>
        </div>

        {heldAudio && (
          <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-warn/40 bg-warn-soft px-3 py-2">
            <span className="text-xs text-warn">
              A {clock(heldAudio.durationSeconds)} recording ({bytes(heldAudio.blob.size)}) is still unsent.
            </span>
            <Button variant="secondary" onClick={() => void sendAudio(heldAudio)}>
              Retry upload
            </Button>
            <Button variant="quiet" onClick={() => setHeldAudio(null)}>
              Discard
            </Button>
          </div>
        )}

        {error && <p className="mt-3 rounded-lg border border-bad/40 bg-bad-soft px-3 py-2 text-xs text-bad">{error}</p>}

        {notificationPermission !== "granted" && (
          <p className="mt-3 text-xs text-muted">
            {FLOW_STATUS.notifications_disabled.detail}{" "}
            <button type="button" className="font-medium text-accent underline" onClick={() => void session.requestNotificationPermission()}>
              Enable notifications
            </button>
          </p>
        )}
      </Card>

      {queued.length > 0 && (
        <Card>
          <SectionHeading hint="held on this device">Unsent notes</SectionHeading>
          <ul className="flex flex-col gap-2">
            {queued.map((entry) => (
              <li key={entry.client_capture_id} className="rounded-lg border border-line bg-canvas px-3 py-2">
                <p className="truncate text-sm text-ink">{entry.text}</p>
                <p className="text-xs text-muted">
                  {entry.attempts} attempt{entry.attempts === 1 ? "" : "s"}
                  {entry.last_error ? ` · ${entry.last_error}` : ""}
                </p>
              </li>
            ))}
          </ul>
          <div className="mt-2 flex gap-2">
            <Button variant="secondary" disabled={!online} onClick={() => void flush()}>
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

function describe(cause: unknown): string {
  if (cause instanceof ApiError) return `${cause.message} (${cause.code})`;
  if (cause instanceof Error) return cause.message;
  return "something went wrong; your input was not discarded";
}
