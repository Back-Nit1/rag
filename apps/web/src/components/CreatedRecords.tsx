/**
 * What the save actually created, with each reminder's real state.
 *
 * "Reminder scheduled" appears only for a confirmed scheduler sync; anything
 * else says "Scheduling reminder" or "Schedule needs retry".
 */
import type { Event, Reminder, Task } from "../contracts/metis";
import { ReminderState } from "../contracts/metis";
import { Card, SectionHeading } from "./Card";
import { ReminderControls } from "./ReminderControls";

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
            <ReminderControls reminderId={reminder.id} initial={reminder} timezone={timezone} />
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
