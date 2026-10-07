/**
 * An evidence chip that opens the cited source at the cited span, and lets the
 * owner delete that source. Deletion shows the server's own purge job state:
 * "tombstoned" means access is gone, "purged" only when the job says so.
 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { JobState, type CaptureDetail, type DeleteCaptureResponse, type Job } from "../contracts/metis";
import { ApiError, api } from "../lib/api";
import { locate, type EvidenceRef } from "../lib/evidence";
import { Button } from "./Button";
import { Loading, Notice } from "./Card";

const TERMINAL = new Set<string>([JobState.READY, JobState.FAILED, JobState.CANCELED]);

function SourceBody({ refs, onClose }: { refs: EvidenceRef; onClose: () => void }) {
  const [detail, setDetail] = useState<CaptureDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [deleted, setDeleted] = useState<DeleteCaptureResponse | null>(null);
  const [purge, setPurge] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const polls = useRef(0);

  useEffect(() => {
    api.getCapture(refs.source_id).then(setDetail, (cause) =>
      setError(cause instanceof ApiError && cause.status === 404 ? "This source is not available. It may have been deleted." : "The source could not be loaded."),
    );
  }, [refs.source_id]);

  useEffect(() => {
    if (!deleted || polls.current >= 40 || (purge && TERMINAL.has(purge.state ?? ""))) return; // then stop: the job record, not this tab, is the truth
    polls.current += 1;
    const timer = window.setTimeout(() => api.getJob(deleted.purge_job_id).then(setPurge, () => undefined), purge ? 1500 : 0);
    return () => window.clearTimeout(timer);
  }, [deleted, purge]);

  const remove = async () => {
    setBusy(true);
    try {
      setDeleted(await api.deleteCapture(refs.source_id));
      setConfirming(false);
    } catch {
      setError("The delete was not accepted. Nothing was removed.");
    } finally {
      setBusy(false);
    }
  };

  const found = detail ? locate(detail, refs) : null;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-muted">
        <span className="font-mono">{refs.source_id}</span> · version {refs.source_version}
        {refs.span_ref ? ` · span ${refs.span_ref}` : " · no span supplied"}
      </p>
      {error && <Notice tone="warn" live>{error}</Notice>}
      {!detail && !error && <Loading label="Loading the source" rows={1} />}
      {detail && !found && <Notice tone="info">Version {refs.source_version} of this source has no stored text.</Notice>}
      {found && (
        <p className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-canvas p-3 font-serif text-base leading-relaxed">
          {"match" in found ? (
            <>
              {found.before}
              <mark className="rounded-sm bg-accent-soft px-0.5 text-ink">{found.match}</mark>
              {found.after}
            </>
          ) : (
            found.text
          )}
        </p>
      )}
      {found && "text" in found && refs.span_ref && (
        <p className="text-xs text-muted">The cited span could not be located in this version, so the whole text is shown.</p>
      )}

      {deleted ? (
        <Notice tone={purge?.state === JobState.READY ? "good" : purge?.state === JobState.FAILED ? "bad" : "info"} live>
          {purge?.state === JobState.READY
            ? "Deleted. The server reports the purge finished."
            : purge?.state === JobState.FAILED
              ? `Access is removed, but the purge job failed${purge.error_message ? `: ${purge.error_message}` : ""}.`
              : "Access is removed now. Derived copies are being purged by a background job; that is not finished yet."}{" "}
          <span className="font-mono text-xs">{deleted.purge_job_id}</span>
        </Notice>
      ) : confirming ? (
        <Notice tone="warn" action={
          <div className="flex gap-2">
            <Button variant="danger" disabled={busy} onClick={() => void remove()}>{busy ? "Deleting…" : "Delete this source"}</Button>
            <Button variant="quiet" onClick={() => setConfirming(false)}>Keep it</Button>
          </div>
        }>
          This removes the original and everything derived from it. It cannot be undone.
        </Notice>
      ) : (
        detail && <div><Button variant="danger" onClick={() => setConfirming(true)}>Delete source…</Button></div>
      )}
      <div><Button onClick={onClose}>Close</Button></div>
    </div>
  );
}

export function EvidenceLink({ evidence, label }: { evidence: EvidenceRef; label?: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => (setOpen(true), dialog.current?.showModal())}
        className="rounded-sm border border-line bg-raised px-1.5 py-0.5 font-mono text-2xs text-accent-ink underline underline-offset-2 hover:bg-accent-soft"
      >
        {label ?? `source ${evidence.source_id.slice(0, 10)}…`}
      </button>
      {createPortal(
      <dialog
        ref={dialog}
        aria-label="Source"
        onClose={() => setOpen(false)}
        className="m-auto w-[min(92vw,40rem)] rounded-lg border border-line-strong bg-surface p-5 text-ink backdrop:bg-ink/40"
      >
        {open && <SourceBody refs={evidence} onClose={() => dialog.current?.close()} />}
      </dialog>,
        document.body,
      )}
    </>
  );
}
