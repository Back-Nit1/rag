/**
 * One status, shown as text + symbol + colour — never colour alone.
 *
 * Busy states are announced politely through a live region so a screen reader
 * hears "Transcribing" without the view stealing focus.
 */
import type { StatusDescriptor, Tone } from "../lib/status";

const TONE: Record<Tone, string> = {
  neutral: "bg-raised text-muted border-line",
  busy: "bg-accent-soft text-accent border-accent/40",
  good: "bg-accent-soft text-accent border-accent/40",
  warn: "bg-warn-soft text-warn border-warn/40",
  bad: "bg-bad-soft text-bad border-bad/40",
};

export function StatusPill({ status, detail = true }: { status: StatusDescriptor; detail?: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-2" aria-busy={status.busy}>
      <span
        className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${TONE[status.tone]}`}
      >
        <span aria-hidden="true" className={status.busy ? "pulse" : undefined}>
          {status.cue}
        </span>
        {status.label}
      </span>
      {detail && <span className="text-xs text-muted">{status.detail}</span>}
    </div>
  );
}

/** The single polite live region the capture flow announces through. */
export function LiveStatus({ status }: { status: StatusDescriptor }) {
  return (
    <p className="visually-hidden" role="status" aria-live="polite">
      {status.label}. {status.detail}
    </p>
  );
}
