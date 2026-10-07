/**
 * Manual availability for one day. The server stores what is typed and nothing
 * else; the plan only claims free time that appears here. No delete route
 * exists yet, so a block is edited (or flipped to busy), never removed.
 */
import { useState } from "react";
import type { AvailabilityBlock, TodayResponse } from "../contracts/metis";
import { ApiError, api } from "../lib/api";
import { Button } from "./Button";
import { Empty, Notice, SectionHeading } from "./Card";
import { Field, inputClass } from "./Field";

type Draft = { id: string | null; start: string; end: string; kind: "available" | "busy"; label: string };
const BLANK: Draft = { id: null, start: "09:00", end: "12:00", kind: "available", label: "" };

export function AvailabilityPanel({ today, onChanged }: { today: TodayResponse; onChanged: () => Promise<void> }) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const blocks = [...(today.availability ?? [])].sort((a, b) => a.window.start.localeCompare(b.window.start));

  const save = async () => {
    if (!draft) return;
    if (draft.end <= draft.start) return setError("The end must be after the start. Overnight blocks are not supported here.");
    setSaving(true);
    setError(null);
    const existing = draft.id ? blocks.find((b) => b.id === draft.id) : undefined;
    const now = new Date().toISOString();
    const block: AvailabilityBlock = {
      // The server replaces user_id, timestamps and revision; only the user's own facts matter here.
      id: draft.id ?? `avail_${crypto.randomUUID()}`,
      user_id: existing?.user_id ?? "-",
      created_at: existing?.created_at ?? now,
      updated_at: now,
      local_date: today.local_date,
      timezone: today.timezone,
      window: { start: draft.start, end: draft.end },
      kind: draft.kind,
      label: draft.label.trim() || null,
    };
    try {
      await api.saveAvailability(block);
      setDraft(null);
      await onChanged();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The request did not reach Metis. Nothing was saved.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section aria-labelledby="h-avail" className="order-6">
      <SectionHeading id="h-avail" hint="typed by you, never guessed">
        Availability
      </SectionHeading>
      {blocks.length === 0 ? (
        <Empty icon="today">No availability entered for {today.local_date}.</Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {blocks.map((b) => (
            <li key={b.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line bg-surface px-3.5 py-2.5">
              <p className="text-sm">
                <span className="font-mono">
                  {b.window.start}–{b.window.end}
                </span>{" "}
                <span className="font-medium">{b.kind === "busy" ? "Busy" : "Available"}</span>
                {b.label ? ` · ${b.label}` : ""}
              </p>
              <Button
                variant="quiet"
                onClick={() => (setError(null), setDraft({ id: b.id, start: b.window.start, end: b.window.end, kind: b.kind, label: b.label ?? "" }))}
              >
                Edit
              </Button>
            </li>
          ))}
        </ul>
      )}
      {draft ? (
        <div className="mt-3 flex flex-col gap-3 rounded-lg border border-line bg-surface p-4">
          {error && <Notice tone="warn" live>{error}</Notice>}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Field label="From">
              <input className={inputClass} type="time" value={draft.start} onChange={(e) => setDraft({ ...draft, start: e.target.value })} />
            </Field>
            <Field label="Until">
              <input className={inputClass} type="time" value={draft.end} onChange={(e) => setDraft({ ...draft, end: e.target.value })} />
            </Field>
            <Field label="Kind">
              <select className={inputClass} value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as Draft["kind"] })}>
                <option value="available">Available</option>
                <option value="busy">Busy</option>
              </select>
            </Field>
            <Field label="Label" hint="Optional.">
              <input className={inputClass} value={draft.label} onChange={(e) => setDraft({ ...draft, label: e.target.value })} />
            </Field>
          </div>
          <div className="flex gap-2">
            <Button variant="primary" disabled={saving} onClick={() => void save()}>
              {saving ? "Saving…" : draft.id ? "Save changes" : "Add block"}
            </Button>
            <Button variant="quiet" onClick={() => setDraft(null)}>Cancel</Button>
          </div>
        </div>
      ) : (
        <Button className="mt-3" onClick={() => (setError(null), setDraft(BLANK))}>
          Add availability or a fixed block
        </Button>
      )}
    </section>
  );
}
