/**
 * What the save actually created, with each reminder's real state.
 *
 * "Reminder scheduled" appears only for a confirmed scheduler sync; anything
 * else says "Scheduling reminder" or "Schedule needs retry".
 */
import type { Event, Reminder, Task } from "../contracts/metis";
import { ReminderState } from "../contracts/metis";
import { timeLabel } from "../lib/format";
import { REMINDER_STATUS } from "../lib/status";
import { Card, SectionHeading } from "./Card";
import { StatusPill } from "./StatusPill";

export function CreatedRecords({
  tasks,
  events,
  reminders,
  timezone,
  onRefreshReminders,
}: {
  tasks: Task[];
  events: Event[];
  reminders: Reminder[];
  timezone: string;
  onRefreshReminders: () => void;
}) {
  if (tasks.length + events.length + reminders.length === 0) return null;
  const unsettled = reminders.some((r) => (r.state ?? ReminderState.PENDING_SCHEDULE) === ReminderState.PENDING_SCHEDULE);
  return (
    <Card>
      <SectionHeading hint="these are now real records">Saved</SectionHeading>
      <ul className="flex flex-col gap-2">
        {tasks.map((task) => (
          <li key={task.id} className="rounded-md border border-line bg-canvas px-3 py-2.5">
            <p className="font-serif text-base font-semibold text-ink">{task.title}</p>
            <p className="text-xs text-muted">
              Task · {task.state} · {task.deadline_local_date ? `due ${task.deadline_local_date}` : "no deadline"}
            </p>
          </li>
        ))}
        {events.map((event) => (
          <li key={event.id} className="rounded-md border border-line bg-canvas px-3 py-2.5">
            <p className="font-serif text-base font-semibold text-ink">{event.title}</p>
            <p className="text-xs text-muted">
              Event · attendance {event.attendance_status} · registration {event.registration_status}
            </p>
          </li>
        ))}
        {reminders.map((reminder) => (
          <li key={reminder.id} className="rounded-md border border-line bg-canvas px-3 py-2.5">
            <StatusPill status={REMINDER_STATUS[reminder.state ?? ReminderState.PENDING_SCHEDULE]} detail={false} />
            <p className="mt-1 text-xs text-muted">
              {timeLabel(reminder.trigger_at, reminder.timezone || timezone)} · {reminder.channel}
            </p>
          </li>
        ))}
      </ul>
      {unsettled && (
        <button
          type="button"
          onClick={onRefreshReminders}
          className="mt-3 min-h-10 text-sm font-medium text-accent underline underline-offset-2"
        >
          Check the schedule again
        </button>
      )}
    </Card>
  );
}
