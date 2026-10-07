/**
 * Compact HUD surface. Presentational: state in, markup out, so it renders the
 * same in tests, screenshots and the Tauri window. It never takes focus by
 * itself; the live region is polite and only changes with the status label.
 */
import { LiveStatus, StatusPill } from "./StatusPill";
import { Button } from "./Button";
import type { HudSnapshot } from "../lib/hud";
import { OPEN_ERROR_COPY, describe } from "../lib/hud";

export function HudView({
  snapshot,
  onCancel,
  onOpen,
  openError = false,
}: {
  snapshot: HudSnapshot;
  onCancel: () => void;
  onOpen?: () => void;
  openError?: boolean;
}) {
  const v = describe(snapshot);
  const recording = snapshot.state === "recording";
  return (
    <main
      aria-label="Metis capture"
      className="flex min-h-dvh flex-col gap-3 border-2 border-line-strong bg-surface p-4 text-ink"
    >
      <LiveStatus status={v.status} />
      <div className="flex items-center gap-3">
        <span
          aria-hidden="true"
          className={`size-4 shrink-0 rounded-full ${recording ? "rec-ring bg-record" : v.status.tone === "bad" ? "bg-bad" : v.status.busy ? "bg-accent pulse" : "bg-line-strong"}`}
        />
        <div className="min-w-0 flex-1">
          <StatusPill status={{ ...v.status, detail: "" }} detail={false} />
        </div>
        {v.clock && (
          <span aria-hidden="true" className="font-mono text-2xl font-semibold tabular-nums">
            {v.clock}
          </span>
        )}
      </div>
      <p className="text-sm text-ink">{v.status.detail}</p>
      {v.transcript && (
        <blockquote className="line-clamp-3 border-l-2 border-line-strong pl-2 font-serif text-base">{v.transcript}</blockquote>
      )}
      {v.hint && <p className="text-xs text-muted">{v.hint}</p>}
      {(v.speech || v.extraction) && (
        <ul className="flex flex-col gap-0.5 text-2xs text-muted" aria-label="Where results came from">
          {v.speech && <li>{v.speech}</li>}
          {v.extraction && <li>{v.extraction}</li>}
        </ul>
      )}
      <div className="mt-auto flex items-center gap-2">
        {v.showOpenReview && (
          <div className="flex flex-1 flex-col gap-1">
            <Button variant="secondary" onClick={onOpen}>
              {v.error ? "Open Metis for details" : "Open Metis to review and confirm"}
            </Button>
            {openError && (
              <p role="alert" className="text-2xs text-bad">
                {OPEN_ERROR_COPY}
              </p>
            )}
          </div>
        )}
        {v.showCancel && (
          <Button variant="danger" onClick={onCancel} className="ml-auto">
            Cancel
          </Button>
        )}
      </div>
    </main>
  );
}
