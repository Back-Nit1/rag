/**
 * An ambiguous "cancel / mark registered" has no single target. Nothing was
 * changed; the user picks one of the recorded candidates, or leaves it.
 */
import { useEffect, useState } from "react";
import type { ExtractionProposal, TargetChoice } from "../contracts/metis";
import { api } from "../lib/api";
import { mutationError } from "../lib/mutationError";
import { UPDATE_LABEL } from "../lib/statusUpdate";
import { Button } from "./Button";
import { Card, Notice, SectionHeading } from "./Card";

async function titleOf(id: string): Promise<string> {
  const record = await api.getEvent(id).catch(() => api.getTask(id)).catch(() => null);
  return record ? `${record.title}${"local_date" in record && record.local_date ? ` (${record.local_date})` : ""}` : id;
}

export function TargetChoicePrompt({
  proposal,
  choice,
  onChanged,
}: {
  proposal: ExtractionProposal;
  choice: TargetChoice;
  /** Called after a pick or a conflict so the caller re-reads the capture. */
  onChanged: () => void;
}) {
  const ids = choice.candidate_ids ?? [];
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    void Promise.all(ids.map(async (id) => [id, await titleOf(id)] as const)).then(
      (pairs) => live && setTitles(Object.fromEntries(pairs)),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids.join("|")]);

  const pick = async (id: string) => {
    setBusy(true);
    setError(null);
    try {
      await api.chooseTarget(
        proposal.id,
        { expected_revision: proposal.revision ?? 1, item_index: choice.item_index, target_id: id },
        `target:${proposal.id}:${choice.item_index}:${id}`,
      );
      onChanged();
    } catch (cause) {
      const e = mutationError("This update", cause);
      setError(e.text);
      if (e.conflict) onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="border-warn/60 border-l-4">
      <SectionHeading hint="nothing was changed">Which one?</SectionHeading>
      <p className="text-sm text-ink">
        “{UPDATE_LABEL[choice.update_action]}” matched {ids.length} records. Pick the one you meant.
      </p>
      <ul className="mt-2 flex flex-col gap-2">
        {ids.map((id) => (
          <li key={id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <span>{titles[id] ?? id}</span>
            <Button variant="primary" disabled={busy} onClick={() => void pick(id)}>
              {UPDATE_LABEL[choice.update_action]} this one
            </Button>
          </li>
        ))}
      </ul>
      {error && (
        <Notice tone="bad" live className="mt-3">
          {error}
        </Notice>
      )}
    </Card>
  );
}
