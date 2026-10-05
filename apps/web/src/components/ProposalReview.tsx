/**
 * Proposal review: compact editable cards, one small control per missing
 * field, and no mandatory form.
 *
 * Nothing here fills a blank in: an unknown date stays empty, the card says
 * what is missing, and an item the user does not resolve can be left alone or
 * dismissed without being turned into a commitment.
 */
import { useMemo, useState } from "react";
import {
  AttendanceStatus,
  type ExtractedItem,
  ExtractedItemKind,
  type ExtractionProposal,
  type ItemDecision,
  RegistrationStatus,
  RequestedAction,
} from "../contracts/metis";
import { Button } from "./Button";
import { Card, SectionHeading } from "./Card";
import { Field, inputClass } from "./Field";

const KIND_LABEL: Record<ExtractedItemKind, string> = {
  [ExtractedItemKind.TASK]: "Task",
  [ExtractedItemKind.EVENT_INTENT]: "Event (tentative)",
  [ExtractedItemKind.THOUGHT]: "Idea",
  [ExtractedItemKind.DECISION]: "Decision",
  [ExtractedItemKind.WORK_LOG]: "Work log",
  [ExtractedItemKind.REMINDER_REQUEST]: "Reminder",
  [ExtractedItemKind.PREFERENCE]: "Preference",
  [ExtractedItemKind.PROJECT_HINT]: "Project",
};

type Draft = {
  item: ExtractedItem;
  dismissed: boolean;
  touched: boolean;
  /** The user asked for a reminder on this item. */
  wantsReminder: boolean;
};

function initialDrafts(items: ExtractedItem[]): Draft[] {
  return items.map((item) => ({
    item,
    dismissed: false,
    touched: false,
    wantsReminder: (item.requested_actions ?? []).some(
      (a) => a === RequestedAction.REMIND_GENERIC || a === RequestedAction.REMIND_TO_REGISTER,
    ),
  }));
}

/** Only fields the user actually filled are sent; blanks stay null. */
function decisionFor(draft: Draft, index: number, timezone: string): ItemDecision {
  if (draft.dismissed) return { item_index: index, action: "dismiss" };
  if (!draft.touched) return { item_index: index, action: "accept" };
  const item = draft.item;
  const actions = new Set(item.requested_actions ?? []);
  if (draft.wantsReminder) actions.add(RequestedAction.REMIND_GENERIC);
  else {
    actions.delete(RequestedAction.REMIND_GENERIC);
    actions.delete(RequestedAction.REMIND_TO_REGISTER);
  }
  const resolved = (item.missing_fields ?? []).filter((field) => !isFilled(item, field, draft.wantsReminder));
  return {
    item_index: index,
    action: "edit",
    edited: {
      ...item,
      timezone: item.timezone ?? timezone,
      requested_actions: [...actions],
      missing_fields: resolved,
    },
  };
}

function isFilled(item: ExtractedItem, field: string, wantsReminder: boolean): boolean {
  switch (field) {
    case "deadline_local_date":
      return !!item.deadline_local_date;
    case "event_date_time":
      return !!item.local_date;
    case "registration_reminder_time":
      return !wantsReminder || (!!item.remind_local_time && !!(item.local_date ?? item.deadline_local_date));
    case "registration_status":
      return !!item.registration_status && item.registration_status !== RegistrationStatus.UNKNOWN;
    default:
      return false;
  }
}

export function ProposalReview({
  proposal,
  timezone,
  busy,
  onResolve,
  onShowEvidence,
}: {
  proposal: ExtractionProposal;
  timezone: string;
  busy: boolean;
  onResolve: (decisions: ItemDecision[]) => void;
  onShowEvidence: (spanRef: string | null | undefined) => void;
}) {
  const [drafts, setDrafts] = useState<Draft[]>(() => initialDrafts(proposal.items ?? []));

  const update = (index: number, patch: Partial<ExtractedItem>, extra: Partial<Draft> = {}) =>
    setDrafts((current) =>
      current.map((draft, i) =>
        i === index ? { ...draft, ...extra, touched: true, item: { ...draft.item, ...patch } } : draft,
      ),
    );

  const decisions = useMemo(
    () => drafts.map((draft, index) => decisionFor(draft, index, timezone)),
    [drafts, timezone],
  );
  const keptCount = decisions.filter((d) => d.action !== "dismiss").length;
  const stillMissing = drafts.flatMap((draft, index) =>
    draft.dismissed
      ? []
      : (draft.item.missing_fields ?? []).filter((f) => !isFilled(draft.item, f, draft.wantsReminder)).map((f) => ({ index, f })),
  );

  return (
    <Card>
      <SectionHeading hint={`${proposal.items?.length ?? 0} found · ${proposal.extractor_version}`}>
        Review what was found
      </SectionHeading>
      <p className="mb-3 text-xs text-muted">
        Proposed by <code className="text-ink">{proposal.model_id}</code> ({proposal.adapter_mode}). Nothing below
        exists yet — unknown fields were left empty on purpose.
      </p>

      <ul className="flex flex-col gap-3">
        {drafts.map((draft, index) => {
          const item = draft.item;
          const missing = new Set(item.missing_fields ?? []);
          const isEvent = item.kind === ExtractedItemKind.EVENT_INTENT;
          return (
            <Card as="li" key={index} className={draft.dismissed ? "opacity-50" : ""}>
              <div className="mb-2 flex items-start justify-between gap-2">
                <span className="rounded-md bg-raised px-2 py-0.5 text-[11px] font-medium text-muted">
                  {KIND_LABEL[item.kind]}
                </span>
                <div className="flex gap-1">
                  <Button
                    variant="quiet"
                    aria-pressed={draft.dismissed}
                    onClick={() =>
                      setDrafts((c) => c.map((d, i) => (i === index ? { ...d, dismissed: !d.dismissed } : d)))
                    }
                  >
                    {draft.dismissed ? "Keep" : "Dismiss"}
                  </Button>
                </div>
              </div>

              <Field label="Title">
                <input
                  className={inputClass}
                  value={item.title}
                  disabled={draft.dismissed}
                  onChange={(e) => update(index, { title: e.target.value })}
                />
              </Field>

              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                {(missing.has("deadline_local_date") || item.deadline_local_date) && (
                  <Field label="Deadline" required={missing.has("deadline_local_date") && !item.deadline_local_date}>
                    <input
                      type="date"
                      className={inputClass}
                      value={item.deadline_local_date ?? ""}
                      disabled={draft.dismissed}
                      onChange={(e) => update(index, { deadline_local_date: e.target.value || null })}
                    />
                  </Field>
                )}

                {(isEvent || missing.has("event_date_time") || item.local_date) && (
                  <Field label={isEvent ? "Event date" : "Date"} required={missing.has("event_date_time") && !item.local_date}>
                    <input
                      type="date"
                      className={inputClass}
                      value={item.local_date ?? ""}
                      disabled={draft.dismissed}
                      onChange={(e) => update(index, { local_date: e.target.value || null })}
                    />
                  </Field>
                )}

                {isEvent && (
                  <Field label="Registered?" hint="Interest is not registration.">
                    <select
                      className={inputClass}
                      value={item.registration_status ?? RegistrationStatus.UNKNOWN}
                      disabled={draft.dismissed}
                      onChange={(e) =>
                        update(index, { registration_status: e.target.value as RegistrationStatus })
                      }
                    >
                      <option value={RegistrationStatus.UNKNOWN}>Not stated</option>
                      <option value={RegistrationStatus.NOT_REGISTERED}>Not registered</option>
                      <option value={RegistrationStatus.REGISTERED}>Registered</option>
                    </select>
                  </Field>
                )}

                {isEvent && (
                  <Field label="Attending?">
                    <select
                      className={inputClass}
                      value={item.attendance_status ?? AttendanceStatus.TENTATIVE}
                      disabled={draft.dismissed}
                      onChange={(e) => update(index, { attendance_status: e.target.value as AttendanceStatus })}
                    >
                      <option value={AttendanceStatus.TENTATIVE}>Maybe</option>
                      <option value={AttendanceStatus.COMMITTED}>Going</option>
                      <option value={AttendanceStatus.DECLINED}>Not going</option>
                    </select>
                  </Field>
                )}
              </div>

              <div className="mt-3 rounded-lg border border-line bg-canvas p-2">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={draft.wantsReminder}
                    disabled={draft.dismissed}
                    onChange={(e) =>
                      setDrafts((c) =>
                        c.map((d, i) =>
                          i === index ? { ...d, wantsReminder: e.target.checked, touched: true } : d,
                        ),
                      )
                    }
                  />
                  Remind me
                </label>
                {draft.wantsReminder && (
                  <div className="mt-2 grid gap-2 sm:grid-cols-2">
                    <Field label="Remind on" required={!(item.local_date ?? item.deadline_local_date)}>
                      <input
                        type="date"
                        className={inputClass}
                        value={item.local_date ?? item.deadline_local_date ?? ""}
                        disabled={draft.dismissed}
                        onChange={(e) => update(index, { local_date: e.target.value || null })}
                      />
                    </Field>
                    <Field label="At" required={!item.remind_local_time} hint={timezone}>
                      <input
                        type="time"
                        className={inputClass}
                        value={item.remind_local_time ?? ""}
                        disabled={draft.dismissed}
                        onChange={(e) => update(index, { remind_local_time: e.target.value || null })}
                      />
                    </Field>
                  </div>
                )}
              </div>

              {(item.evidence ?? []).length > 0 && (
                <Button
                  variant="quiet"
                  className="mt-2 px-0"
                  onClick={() => onShowEvidence(item.evidence?.[0]?.span_ref)}
                >
                  Show the words this came from
                </Button>
              )}
            </Card>
          );
        })}
      </ul>

      {stillMissing.length > 0 && (
        <p className="mt-3 rounded-lg border border-warn/40 bg-warn-soft px-3 py-2 text-xs text-warn">
          {stillMissing.length} field{stillMissing.length > 1 ? "s" : ""} still unanswered. Saving now keeps those
          items in the inbox instead of making them commitments.
        </p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button variant="primary" disabled={busy || keptCount === 0} onClick={() => onResolve(decisions)}>
          {busy ? "Saving…" : `Save ${keptCount} item${keptCount === 1 ? "" : "s"}`}
        </Button>
        <Button
          variant="quiet"
          disabled={busy}
          onClick={() => onResolve(decisions.map((d) => ({ item_index: d.item_index, action: "dismiss" })))}
        >
          Dismiss all
        </Button>
      </div>
    </Card>
  );
}
