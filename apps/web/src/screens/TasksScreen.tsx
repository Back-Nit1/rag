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
import { Chip, Empty, Notice, PageHeader, SectionHeading } from "../components/Card";
import { Icon } from "../components/Icon";
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
  const [loadError, setLoadError] = useState(false);

  const load = useCallback(async () => {
    try {
      const [taskPage, eventPage] = await Promise.all([
        api.listTasks(filter === "all" ? {} : { state: filter }),
        api.listEvents(),
      ]);
      setTasks(taskPage.items ?? []);
      setEvents(eventPage.items ?? []);
      setLoadError(false);
    } catch {
      setLoadError(true);
    }
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
    <div className="flex flex-col gap-6">
      <PageHeader title="Tasks and events" sub="Flexible tasks have a dashed edge; fixed events have a solid one." />

      {loadError && (
        <Notice tone="bad" live action={<Button onClick={() => void load()}>Try again</Button>}>
          Tasks and events could not be loaded. Nothing was changed.
        </Notice>
      )}

      <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_20rem] lg:items-start lg:gap-8">
        <section aria-labelledby="h-tasks" className="flex flex-col gap-3">
          <SectionHeading id="h-tasks" hint={`${tasks.length} shown`}>
            Tasks
          </SectionHeading>
          <div
            className="-mx-1 flex gap-1 overflow-x-auto px-1 pb-1"
            role="group"
            aria-label="Filter by state"
          >
            {(["all", ...STATES] as const).map((state) => (
              <Button
                key={state}
                variant={filter === state ? "primary" : "secondary"}
                aria-pressed={filter === state}
                className="shrink-0 min-h-9! px-3"
                onClick={() => setFilter(state)}
              >
                {state === "all" ? "All" : state.replace("_", " ")}
              </Button>
            ))}
          </div>
          {notice && (
            <Notice tone="warn" live>
              {notice}
            </Notice>
          )}
          {tasks.length === 0 ? (
            <Empty title="No task matches this filter">
              {filter === "all" ? "Captured tasks will appear here." : "Try another state, or show all tasks."}
            </Empty>
          ) : (
            <ul className="flex flex-col gap-2">
              {tasks.map((task) => (
                <li
                  key={task.id}
                  className={`defer-paint rounded-lg border bg-surface p-4 ${
                    selected?.id === task.id ? "border-accent" : "border-line"
                  } border-l-[3px] border-l-accent [border-left-style:dashed]`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
                    <button
                      type="button"
                      aria-expanded={selected?.id === task.id}
                      className="min-h-6 text-left font-serif text-base font-semibold text-ink underline-offset-2 hover:underline"
                      onClick={() => setSelected(selected?.id === task.id ? null : task)}
                    >
                      {task.title}
                    </button>
                    <Chip title={`state ${task.state}, revision ${task.revision}`}>
                      {task.state} · r{task.revision}
                    </Chip>
                  </div>
                  <p className="mt-0.5 text-xs text-muted">
                    {task.deadline_local_date ? `due ${task.deadline_local_date}` : "no deadline"}
                    {task.depends_on?.length ? ` · depends on ${task.depends_on.length}` : ""}
                    {task.user_edited_fields?.length ? ` · you edited ${task.user_edited_fields.join(", ")}` : ""}
                  </p>
                  {selected?.id === task.id && (
                    <dl className="mt-3 grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1.5 rounded-md bg-canvas p-3 text-xs">
                      <dt className="text-muted">State</dt>
                      <dd className="text-ink">{selected.state}</dd>
                      <dt className="text-muted">Deadline</dt>
                      <dd className="text-ink">{selected.deadline_local_date ?? "not set"}</dd>
                      <dt className="text-muted">Created</dt>
                      <dd className="text-ink">{timeLabel(selected.created_at, timezone)}</dd>
                      <dt className="text-muted">Revision</dt>
                      <dd className="text-ink">{selected.revision}</dd>
                      <dt className="text-muted">Source</dt>
                      <dd className="break-all font-mono text-ink">
                        {selected.source_refs?.length
                          ? selected.source_refs
                              .map(
                                (ref) =>
                                  `${ref.source_id} v${ref.source_version}${ref.span_ref ? ` (${ref.span_ref})` : ""}`,
                              )
                              .join(", ")
                          : "no source: created by hand"}
                      </dd>
                    </dl>
                  )}
                  <div className="mt-3 flex flex-wrap gap-2">
                    {task.state !== TaskState.DONE && (
                      <Button onClick={() => void act(task, TaskState.DONE)}>
                        <Icon name="check" size={16} /> Complete
                      </Button>
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
        </section>

        <section aria-labelledby="h-events" className="mt-8 flex flex-col gap-3 lg:mt-0">
          <SectionHeading id="h-events" hint="fixed times">
            Events
          </SectionHeading>
          {events.length === 0 ? (
            <Empty title="No events yet">Dated things you capture will be listed here with their exact times.</Empty>
          ) : (
            <ul className="flex flex-col gap-2">
              {events.map((event) => (
                <li
                  key={event.id}
                  className="defer-paint rounded-lg border border-line border-l-[3px] border-l-ink bg-surface p-4"
                >
                  <p className="font-serif text-base font-semibold text-ink">{event.title}</p>
                  <p className="mt-0.5 font-mono text-xs text-ink">
                    {event.starts_at
                      ? timeLabel(event.starts_at, event.timezone || timezone)
                      : (event.local_date ?? "no date set")}
                  </p>
                  <p className="mt-1 text-xs text-muted">
                    Attendance: {event.attendance_status}. Registration: {event.registration_status}.
                  </p>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
