/**
 * One status, shown as text + symbol + colour — never colour alone.
 *
 * The pill itself is not a live region (it re-renders often and would repeat).
 * Announcements go through `LiveStatus`, which only changes when the status
 * label does.
 */
import { useEffect, useState } from "react";
import type { StatusDescriptor, Tone } from "../lib/status";

const TONE: Record<Tone, string> = {
  neutral: "bg-raised text-muted border-line-strong",
  busy: "bg-accent-soft text-accent-ink border-accent/50",
  good: "bg-good-soft text-good border-good/50",
  warn: "bg-warn-soft text-warn border-warn/50",
  bad: "bg-bad-soft text-bad border-bad/50",
};

export function StatusPill({ status, detail = true }: { status: StatusDescriptor; detail?: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1" aria-busy={status.busy}>
      <span
        className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${TONE[status.tone]}`}
      >
        <span aria-hidden="true" className={`font-mono ${status.busy ? "pulse" : ""}`}>
          {status.cue}
        </span>
        {status.label}
      </span>
      {detail && <span className="text-xs text-muted">{status.detail}</span>}
    </div>
  );
}

/**
 * The single polite live region the capture flow announces through. The text
 * is only swapped when the label changes, so a re-render with the same status
 * is silent.
 */
export function LiveStatus({ status }: { status: StatusDescriptor }) {
  const [message, setMessage] = useState("");
  useEffect(() => {
    setMessage(`${status.label}. ${status.detail}`);
  }, [status.label, status.detail]);
  return (
    <p className="visually-hidden" role="status" aria-live="polite">
      {message}
    </p>
  );
}
