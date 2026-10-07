/** Locating a piece of evidence inside the source it came from. Nothing is inferred. */
import type { CaptureDetail } from "../contracts/metis";

export type EvidenceRef = { source_id: string; source_version: number; span_ref?: string | null };

export type Located = { before: string; match: string; after: string } | { text: string; span: null } | null;

/** The transcript revision the evidence cites, with the cited span marked when the server gave one. */
export function locate(detail: CaptureDetail, ref: EvidenceRef): Located {
  const revision = (detail.source.transcripts ?? []).find((t) => t.source_version === ref.source_version);
  const text = revision?.text ?? (ref.source_version === detail.source.current_version ? detail.transcript_text : null);
  if (!text) return null;
  const span = ref.span_ref ? (detail.source.spans ?? []).find((s) => s.span_ref === ref.span_ref && s.source_version === ref.source_version) : null;
  if (!span || span.end_char > text.length || span.start_char >= span.end_char) return { text, span: null };
  return { before: text.slice(0, span.start_char), match: text.slice(span.start_char, span.end_char), after: text.slice(span.end_char) };
}
