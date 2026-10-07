/**
 * A clarification is a question, not a form.
 *
 * The only thing offered here is the smallest control that answers it. When
 * the answer is a reminder date and time, answering creates the reminder and
 * the real scheduler state is shown — never a cheerful "scheduled" before the
 * scheduler agreed.
 */
import { useState } from "react";
import type { ClarificationRequest, Reminder } from "../contracts/metis";
import { DeliveryChannel, ReminderState } from "../contracts/metis";
import { api, ApiError } from "../lib/api";
import { targetCandidates } from "../lib/statusUpdate";
import { REMINDER_STATUS } from "../lib/status";
import { Button } from "./Button";
import { Card, Notice, SectionHeading } from "./Card";
import { Field, inputClass } from "./Field";
import { StatusPill } from "./StatusPill";

const REMINDER_FIELDS = ["registration_reminder_time", "event_date_time"];

export function ClarificationPrompt({
  clarification,
  timezone,
  onAnswered,
}: {
  clarification: ClarificationRequest;
  timezone: string;
  onAnswered: (reminder: Reminder) => void;
}) {
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reminder, setReminder] = useState<Reminder | null>(null);

  const candidates = targetCandidates(clarification);
  const answerable =
    !!clarification.target_id && clarification.missing_fields.some((f) => REMINDER_FIELDS.includes(f));

  const submit = async () => {
    if (!clarification.target_id) return;
    setBusy(true);
    setError(null);
    try {
      const target = await api.getTask(clarification.target_id).catch(() => null);
      const kind: "task" | "event" = target ? "task" : "event";
      const revision =
        target?.revision ?? (await api.getEvent(clarification.target_id)).revision ?? 1;
      const created = await api.createReminder(
        {
          target_id: clarification.target_id,
          target_kind: kind,
          expected_target_revision: revision,
          local_date: date,
          local_time: time,
          timezone,
          channel: DeliveryChannel.IN_APP,
        },
        `clarify:${clarification.clarification_id}`,
      );
      setReminder(created);
      onAnswered(created);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "that answer could not be saved; your note is unchanged",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className={clarification.blocking ? "border-warn/60 border-l-4" : ""}>
      <SectionHeading hint={clarification.blocking ? "blocking" : "optional"}>
        {clarification.missing_fields.length === 1 ? "A detail is missing" : `${clarification.missing_fields.length} details are missing`}
      </SectionHeading>
      <p className="font-serif text-base text-ink">{clarification.question}</p>
      <p className="mt-1 text-xs text-muted">
        Missing: {clarification.missing_fields.join(", ")}. Nothing was assumed in the meantime.
      </p>

      {candidates.length > 0 && (
        <div className="mt-2 text-sm">
          <p className="text-muted">Possible records (nothing was changed):</p>
          <ul className="list-disc pl-5">
            {candidates.map((id) => (
              <li key={id}>
                <code className="font-mono">{id}</code>
              </li>
            ))}
          </ul>
        </div>
      )}

      {reminder ? (
        <div className="mt-3">
          <StatusPill status={REMINDER_STATUS[reminder.state ?? ReminderState.PENDING_SCHEDULE]} />
        </div>
      ) : answerable ? (
        <>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            <Field label="Date" required>
              <input type="date" className={inputClass} value={date} onChange={(e) => setDate(e.target.value)} />
            </Field>
            <Field label="Time" required hint={timezone}>
              <input type="time" className={inputClass} value={time} onChange={(e) => setTime(e.target.value)} />
            </Field>
          </div>
          <div className="mt-2 flex items-center gap-2">
            <Button variant="primary" disabled={busy || !date || !time} onClick={() => void submit()}>
              {busy ? "Saving…" : "Answer"}
            </Button>
            <span className="text-xs text-muted">Or leave it — it stays in the inbox without nagging.</span>
          </div>
        </>
      ) : (
        <p className="mt-3 text-xs text-muted">
          This one is answered on the record itself, in Inbox. It will wait there.
        </p>
      )}

      {error && (
        <Notice tone="bad" live className="mt-3">
          {error}
        </Notice>
      )}
    </Card>
  );
}
