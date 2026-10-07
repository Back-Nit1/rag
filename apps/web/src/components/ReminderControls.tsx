/**
 * Cancel and snooze for one reminder, shown with the server's own state.
 * Rescheduling has no API route yet, so it is not offered.
 */
import { useEffect, useState } from "react";
import { ReminderState, type Reminder } from "../contracts/metis";
import { ApiError, api } from "../lib/api";
import { timeLabel } from "../lib/format";
import { REMINDER_STATUS } from "../lib/status";
import { Button } from "./Button";
import { StatusPill } from "./StatusPill";

const OPEN = new Set<ReminderState>([ReminderState.PENDING_SCHEDULE, ReminderState.SCHEDULED, ReminderState.SNOOZED, ReminderState.SCHEDULE_FAILED]);

export function ReminderControls({ reminderId, initial, timezone }: { reminderId: string; initial?: Reminder; timezone: string }) {
  const [reminder, setReminder] = useState<Reminder | null>(initial ?? null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (initial) setReminder(initial);
  }, [initial]);

  useEffect(() => {
    api.getReminder(reminderId).then(setReminder, () => setMessage("This reminder could not be read."));
  }, [reminderId]);

  if (!reminder) return message ? <p className="text-xs text-muted">{message}</p> : null;
  const state = reminder.state ?? ReminderState.PENDING_SCHEDULE;

  const act = async (name: string, run: () => Promise<Reminder>) => {
    setBusy(true);
    setMessage(null);
    try {
      setReminder(await run());
    } catch (cause) {
      if (cause instanceof ApiError && cause.currentRevision !== null) setReminder(await api.getReminder(reminderId).catch(() => reminder));
      setMessage(
        cause instanceof ApiError && cause.currentRevision !== null
          ? "This reminder changed elsewhere. Nothing was overwritten; the latest state is shown."
          : `${name} was not accepted.`,
      );
    } finally {
      setBusy(false);
    }
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
            <Button key={m} variant="quiet" disabled={busy} onClick={() => void act("Snooze", () => api.snoozeReminder(reminderId, reminder.revision ?? 1, m))}>
              Snooze {m === 60 ? "1 hour" : `${m} min`}
            </Button>
          ))}
          <Button variant="quiet" disabled={busy} onClick={() => void act("Cancel", () => api.cancelReminder(reminderId))}>
            Cancel reminder
          </Button>
        </div>
      )}
      {message && <p role="status" className="text-xs text-warn">{message}</p>}
    </div>
  );
}
