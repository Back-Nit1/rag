/**
 * Cancel and snooze for one reminder, shown with the server's own state.
 * Reschedule returns `pending_schedule`, shown as such until the scheduler confirms.
 */
import { useEffect, useState } from "react";
import { ReminderState, type Reminder } from "../contracts/metis";
import { api } from "../lib/api";
import { mutationError } from "../lib/mutationError";
import { timeLabel } from "../lib/format";
import { REMINDER_STATUS } from "../lib/status";
import { Button } from "./Button";
import { Field, inputClass } from "./Field";
import { StatusPill } from "./StatusPill";

const OPEN = new Set<ReminderState>([ReminderState.PENDING_SCHEDULE, ReminderState.SCHEDULED, ReminderState.SNOOZED, ReminderState.SCHEDULE_FAILED]);

export function ReminderControls({ reminderId, initial, timezone }: { reminderId: string; initial?: Reminder; timezone: string }) {
  const [reminder, setReminder] = useState<Reminder | null>(initial ?? null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");

  useEffect(() => {
    if (initial) setReminder(initial);
  }, [initial]);

  useEffect(() => {
    api.getReminder(reminderId).then(setReminder, () => setMessage("This reminder could not be read."));
  }, [reminderId]);

  // pending_schedule is not a result: re-read until the scheduler settles it.
  const waiting = reminder?.state === ReminderState.PENDING_SCHEDULE;
  useEffect(() => {
    if (!waiting) return;
    const t = setInterval(() => void api.getReminder(reminderId).then(setReminder, () => undefined), 3000);
    return () => clearInterval(t);
  }, [waiting, reminderId]);

  if (!reminder) return message ? <p className="text-xs text-muted">{message}</p> : null;
  const state = reminder.state ?? ReminderState.PENDING_SCHEDULE;

  const act = async (run: () => Promise<Reminder>) => {
    setBusy(true);
    setMessage(null);
    try {
      setReminder(await run());
      return true;
    } catch (cause) {
      const e = mutationError("This reminder", cause);
      if (e.conflict) setReminder(await api.getReminder(reminderId).catch(() => reminder));
      setMessage(e.text);
    } finally {
      setBusy(false);
    }
    return false;
  };

  return (
    <div className="mt-2 flex flex-col gap-1.5">
      <StatusPill status={REMINDER_STATUS[state]} detail={false} />
      <p className="text-xs text-muted">
        {timeLabel(reminder.snoozed_until ?? reminder.trigger_at, reminder.timezone || timezone)} · {reminder.channel}
      </p>
      {OPEN.has(state) && (
        <div className="flex flex-wrap gap-1.5">
          {[15, 60].map((m) => (
            <Button key={m} variant="quiet" disabled={busy} onClick={() => void act(() => api.snoozeReminder(reminderId, reminder.revision ?? 1, m))}>
              Snooze {m === 60 ? "1 hour" : `${m} min`}
            </Button>
          ))}
          <Button variant="quiet" disabled={busy} onClick={() => setEditing((v) => !v)}>
            Reschedule
          </Button>
          <Button variant="quiet" disabled={busy} onClick={() => void act(() => api.cancelReminder(reminderId))}>
            Cancel reminder
          </Button>
        </div>
      )}
      {editing && OPEN.has(state) && (
        <div className="flex flex-wrap items-end gap-2">
          <Field label="New date">
            <input type="date" className={inputClass} value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label="New time" hint={reminder.timezone || timezone}>
            <input type="time" className={inputClass} value={time} onChange={(e) => setTime(e.target.value)} />
          </Field>
          <Button
            variant="primary"
            disabled={busy || !date || !time}
            onClick={() =>
              void act(() =>
                api.rescheduleReminder(
                  reminderId,
                  { expected_revision: reminder.revision ?? 1, local_date: date, local_time: time },
                  `resched:${reminderId}:${reminder.revision ?? 1}:${date}T${time}`,
                ),
              ).then((ok) => ok && setEditing(false))
            }
          >
            Save new time
          </Button>
        </div>
      )}
      {waiting && <p className="text-xs text-muted">Waiting for the scheduler to confirm; this is not yet scheduled.</p>}
      {message && <p role="status" className="text-xs text-warn">{message}</p>}
    </div>
  );
}
