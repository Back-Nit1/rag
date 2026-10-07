import { type ExtractedItem, StatusUpdateAction } from "../contracts/metis";

/** A status_update changes an existing record; it never creates one. */
export const UPDATE_LABEL: Record<StatusUpdateAction, string> = {
  [StatusUpdateAction.REGISTERED]: "Mark registered",
  [StatusUpdateAction.CANCELED]: "Cancel",
};

/** Target is resolved only when exactly one id is present. */
export function updateTarget(item: Pick<ExtractedItem, "target_ids">): { id: string } | null {
  const ids = item.target_ids ?? [];
  return ids.length === 1 ? { id: ids[0] } : null;
}

export function updateTargetText(item: Pick<ExtractedItem, "target_ids">): string {
  const t = updateTarget(item);
  return t ? `Matched to record ${t.id}. Nothing changes until you save.` : "Needs a target";
}

/** Candidate ids from a target clarification; empty for any other kind. */
export function targetCandidates(c: { missing_fields: string[]; candidate_ids?: string[] }): string[] {
  return c.missing_fields.includes("target") ? (c.candidate_ids ?? []) : [];
}
