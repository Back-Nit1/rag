/**
 * Tasks and events: list and detail in one compact surface.
 *
 * Fixed event times and flexible task work look different, registration and
 * attendance stay separate, and every action states the revision it acted on
 * so a stale view cannot silently overwrite a newer one.
 */
import { useCallback, useEffect, useState } from "react";
import { type Event as MetisEvent, type Task, TaskState } from "../contracts/metis";
import { Button } from "../components/Button";
import { Card, Empty, SectionHeading } from "../components/Card";
import { useSession } from "../app/session";
import { ApiError, api } from "../lib/api";
import { timeLabel } from "../lib/format";

const STATES: TaskState[] = [TaskState.INBOX, TaskState.READY, TaskState.IN_PROGRESS, TaskState.DONE];

export function TasksScreen() {
  const { timezone } = useSession();
  const [filter, setFilter] = useState<TaskState | "all">("all");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [events, setEvents] = useState<MetisEvent[]>([]);
  const [selected, setSelected] = useState<Task | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [taskPage, eventPage] = await Promise.all([
      api.listTasks(filter === "all" ? {} : { state: filter }),
      api.listEvents(),
    ]);
    setTasks(taskPage.items ?? []);
    setEvents(eventPage.items ?? []);
  }, [filter]);

  useEffect(() => void load(), [load]);

  const act = async (task: Task, state: TaskState) => {
    try {
      const updated = await api.patchTask(task.id, { expected_revision: task.revision ?? 1, state });
      setTasks((current) => current.map((t) => (t.id === updated.id ? updated : t)));
      setSelected((current) => (current?.id === updated.id ? updated : current));
      setNotice(null);
      await load();
    } catch (cause) {
      setNotice(
        cause instanceof ApiError && cause.currentRevision !== null
          ? `This task changed elsewhere (now revision ${cause.currentRevision}). Nothing was overwritten — reload and try again.`
          : "That change was rejected.",
      );
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <SectionHeading hint={`${tasks.length} shown`}>Tasks</SectionHeading>
        <div className="mb-3 flex flex-wrap gap-1" role="group" aria-label="Filter by state">
          {(["all", ...STATES] as const).map((state) => (
            <Button
              key={state}
              variant={filter === state ? "primary" : "quiet"}
              aria-pressed={filter === state}
              onClick={() => setFilter(state)}
            >
              {state}
            </Button>
          ))}
        </div>
        {notice && <p className="mb-2 rounded-lg border border-warn/40 bg-warn-soft px-3 py-2 text-xs text-warn">{notice}</p>}
        {tasks.length === 0 ? (
          <Empty>No task matches this filter.</Empty>
        ) : (
          <ul className="flex flex-col gap-2">
            {tasks.map((task) => (
              <li key={task.id} className="rounded-lg border border-line bg-canvas px-3 py-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <button
                    type="button"
                    className="text-left text-sm font-medium text-ink underline-offset-2 hover:underline"
                    onClick={() => setSelected(task)}
                  >
                    {task.title}
                  </button>
                  <span className="rounded-md bg-raised px-2 py-0.5 text-[11px] text-muted">
                    {task.state} · r{task.revision}
                  </span>
                </div>
                <p className="text-xs text-muted">
                  {task.deadline_local_date ? `due ${task.deadline_local_date}` : "no deadline"}
                  {task.depends_on?.length ? ` · depends on ${task.depends_on.length}` : ""}
                  {task.user_edited_fields?.length ? ` · you edited ${task.user_edited_fields.join(", ")}` : ""}
                </p>
                <div className="mt-2 flex flex-wrap gap-1">
                  {task.state !== TaskState.DONE && (
                    <Button onClick={() => void act(task, TaskState.DONE)}>Complete</Button>
                  )}
                  {task.state !== TaskState.IN_PROGRESS && task.state !== TaskState.DONE && (
                    <Button variant="quiet" onClick={() => void act(task, TaskState.IN_PROGRESS)}>
                      Start
                    </Button>
                  )}
                  {task.state !== TaskState.CANCELED && (
                    <Button variant="quiet" onClick={() => void act(task, TaskState.CANCELED)}>
                      Cancel
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {selected && (
        <Card>
          <SectionHeading hint={`revision ${selected.revision}`}>{selected.title}</SectionHeading>
          <dl className="grid grid-cols-2 gap-2 text-xs">
            <dt className="text-muted">State</dt>
            <dd className="text-ink">{selected.state}</dd>
            <dt className="text-muted">Deadline</dt>
            <dd className="text-ink">{selected.deadline_local_date ?? "not set"}</dd>
            <dt className="text-muted">Created</dt>
            <dd className="text-ink">{timeLabel(selected.created_at, timezone)}</dd>
            <dt className="text-muted">Source</dt>
            <dd className="text-ink">
              {selected.source_refs?.length
                ? selected.source_refs
                    .map((ref) => `${ref.source_id} v${ref.source_version}${ref.span_ref ? ` (${ref.span_ref})` : ""}`)
                    .join(", ")
                : "no source — created by hand"}
            </dd>
          </dl>
          <Button variant="quiet" className="mt-2 px-0" onClick={() => setSelected(null)}>
            Close
          </Button>
        </Card>
      )}

      <Card>
        <SectionHeading hint="fixed times">Events</SectionHeading>
        {events.length === 0 ? (
          <Empty>No events yet.</Empty>
        ) : (
          <ul className="flex flex-col gap-2">
            {events.map((event) => (
              <li key={event.id} className="rounded-lg border-l-4 border-accent bg-canvas px-3 py-2">
                <p className="text-sm font-medium text-ink">{event.title}</p>
                <p className="text-xs text-muted">
                  {event.starts_at ? timeLabel(event.starts_at, event.timezone || timezone) : event.local_date ?? "no date set"} ·
                  attendance {event.attendance_status} · registration {event.registration_status}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
