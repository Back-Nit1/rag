/**
 * The transcript, correctable before anything is committed.
 *
 * A background poll never overwrites what the user is typing: the draft is
 * only re-seeded from the server while it is clean. If the underlying revision
 * moved, the edit is kept and a conflict is shown instead of being lost.
 */
import { useEffect, useRef, useState } from "react";
import type { CaptureDetail } from "../contracts/metis";
import { SourceLifecycle } from "../contracts/metis";
import { Button } from "./Button";
import { Card, SectionHeading } from "./Card";

export function TranscriptPanel({
  capture,
  highlightSpan,
  conflict,
  busy,
  onSave,
}: {
  capture: CaptureDetail;
  highlightSpan?: string | null;
  conflict?: string | null;
  busy: boolean;
  onSave: (text: string) => void;
}) {
  const serverText = capture.transcript_text ?? "";
  const [draft, setDraft] = useState(serverText);
  const dirty = useRef(false);

  useEffect(() => {
    if (!dirty.current) setDraft(serverText);
  }, [serverText]);

  const partial = capture.source.lifecycle === SourceLifecycle.PROCESSING;
  const edited = draft !== serverText;
  const version = capture.source.current_version ?? 1;

  return (
    <Card>
      <SectionHeading hint={`version ${version}${partial ? " · partial" : ""}`}>
        {capture.source.kind === "audio" ? "Transcript" : "Your note"}
      </SectionHeading>

      {partial && (
        <p className="mb-2 rounded-lg border border-line bg-raised px-3 py-2 text-xs text-muted">
          Partial result — still processing. Corrections are best made once this finishes.
        </p>
      )}

      {highlightSpan && (
        <p className="mb-2 text-xs text-muted">
          Evidence span <code className="text-ink">{highlightSpan}</code> — characters{" "}
          {capture.source.spans?.find((s) => s.span_ref === highlightSpan)?.start_char ?? 0}–
          {capture.source.spans?.find((s) => s.span_ref === highlightSpan)?.end_char ?? serverText.length} of
          version {capture.source.spans?.find((s) => s.span_ref === highlightSpan)?.source_version ?? version}.
        </p>
      )}

      <label className="flex flex-col gap-1">
        <span className="visually-hidden">Transcript text</span>
        <textarea
          className="min-h-28 w-full resize-y rounded-lg border border-line bg-canvas p-3 text-sm leading-relaxed text-ink"
          value={draft}
          onChange={(e) => {
            dirty.current = true;
            setDraft(e.target.value);
          }}
        />
      </label>

      {conflict && (
        <p className="mt-2 rounded-lg border border-warn/40 bg-warn-soft px-3 py-2 text-xs text-warn">{conflict}</p>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button
          variant={edited ? "primary" : "secondary"}
          disabled={!edited || busy}
          onClick={() => {
            dirty.current = false;
            onSave(draft);
          }}
        >
          {busy ? "Saving correction…" : "Save correction"}
        </Button>
        {edited && (
          <Button
            variant="quiet"
            onClick={() => {
              dirty.current = false;
              setDraft(serverText);
            }}
          >
            Revert
          </Button>
        )}
        {edited && <span className="text-xs text-muted">A correction creates version {version + 1}.</span>}
      </div>
    </Card>
  );
}
