import { type ExtractedItem, type ExtractionProposal, StatusUpdateAction, type TargetChoice } from "../contracts/metis";

/** A status_update changes an existing record; it never creates one. */
export const UPDATE_LABEL: Record<StatusUpdateAction, string> = {
  [StatusUpdateAction.REGISTERED]: "Mark registered",
  [StatusUpdateAction.CANCELED]: "Cancel",
};

/** Target is resolved only when exactly one id is present. */
export function updateTarget(item: Pick<ExtractedItem, "target_ids">): { id: string } | null {
  const ids = item.target_ids ?? [];
  return ids.length === 1 ? { id: ids[0]! } : null;
}

export function updateTargetText(item: Pick<ExtractedItem, "target_ids">): string {
  const t = updateTarget(item);
  return t ? `Matched to record ${t.id}. Nothing changes until you save.` : "Needs a target";
}

/** Candidate ids from a target clarification; empty for any other kind. */
export function targetCandidates(c: { missing_fields: string[]; candidate_ids?: string[] }): string[] {
  return c.missing_fields.includes("target") ? (c.candidate_ids ?? []) : [];
}

/** Target choices still awaiting the user's pick. */
export function openTargetChoices(proposal: Pick<ExtractionProposal, "target_choices">): TargetChoice[] {
  return (proposal.target_choices ?? []).filter((c) => c.chosen_id == null && (c.candidate_ids ?? []).length > 0);
}
