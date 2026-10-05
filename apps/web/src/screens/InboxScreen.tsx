/**
 * Inbox / clarify: captured things that still need a date, a commitment or a
 * registration answer.
 *
 * Missing information and a processing error are different sections, and an
 * idea can be left unresolved — there is no nagging, no badge that cannot be
 * cleared, and no field filled in on the user's behalf.
 */
import { useCallback, useEffect, useState } from "react";
import { type Event as MetisEvent, RegistrationStatus, type Task, TaskState } from "../contracts/metis";
import { Button } from "../components/Button";
import { Card, Empty, SectionHeading } from "../components/Card";
import { Field, inputClass } from "../components/Field";
import { useSession } from "../app/session";
import { api } from "../lib/api";

export function InboxScreen() {
  const { timezone } = useSession();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [events, setEvents] = useState<MetisEvent[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [taskPage, eventPage] = await Promise.all([api.listTasks({ state: TaskState.INBOX }), api.listEvents()]);
      setTasks(taskPage.items ?? []);
      setEvents((eventPage.items ?? []).filter((e) => e.registration_status === RegistrationStatus.UNKNOWN));
      setError(null);
    } catch {
      setError("The inbox could not be loaded.");
    }
  }, []);

  useEffect(() => void load(), [load]);

  const commit = async (task: Task, deadline: string) => {
    setBusy(task.id);
    try {
      await api.patchTask(task.id, {
        expected_revision: task.revision ?? 1,
        deadline_local_date: deadline || null,
        timezone,
        state: deadline ? TaskState.READY : TaskState.INBOX,
      });
      await load();
    } catch {
      setError("That change was rejected. Your task is unchanged.");
    } finally {
      setBusy(null);
    }
  };

  if (error) return <Card>{error}</Card>;

  return (
    <div className="flex flex-col gap-4">
      <section>
        <SectionHeading hint="missing information, not an error">Needs a detail</SectionHeading>
        {tasks.length === 0 ? (
          <Empty>Nothing is waiting on you.</Empty>
        ) : (
          <ul className="flex flex-col gap-2">
            {tasks.map((task) => (
              <InboxTask key={task.id} task={task} busy={busy === task.id} onCommit={commit} />
            ))}
          </ul>
        )}
      </section>

      <section>
        <SectionHeading hint="interest is not registration">Registration unknown</SectionHeading>
        {events.length === 0 ? (
          <Empty>No event is waiting on a registration answer.</Empty>
        ) : (
          <ul className="flex flex-col gap-2">
            {events.map((event) => (
              <Card as="li" key={event.id}>
                <p className="text-sm font-medium text-ink">{event.title}</p>
                <p className="text-xs text-muted">
                  {event.local_date ?? "no date"} · attendance {event.attendance_status}
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {[RegistrationStatus.REGISTERED, RegistrationStatus.NOT_REGISTERED].map(
                    (status) => (
                      <Button
                        key={status}
                        onClick={async () => {
                          await api.patchEvent(event.id, {
                            expected_revision: event.revision ?? 1,
                            registration_status: status,
                          });
                          await load();
                        }}
                      >
                        {status.replace("_", " ")}
                      </Button>
                    ),
                  )}
                </div>
              </Card>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function InboxTask({
  task,
  busy,
  onCommit,
}: {
  task: Task;
  busy: boolean;
  onCommit: (task: Task, deadline: string) => Promise<void>;
}) {
  const [deadline, setDeadline] = useState(task.deadline_local_date ?? "");
  return (
    <Card as="li">
      <p className="text-sm font-medium text-ink">{task.title}</p>
      <p className="text-xs text-muted">
        {task.deadline_local_date ? `due ${task.deadline_local_date}` : "no deadline — nothing was assumed"}
        {task.source_refs?.[0] ? ` · from ${task.source_refs[0].source_id.slice(0, 12)}…` : ""}
      </p>
      <div className="mt-2 flex flex-wrap items-end gap-2">
        <Field label="Deadline">
          <input type="date" className={inputClass} value={deadline} onChange={(e) => setDeadline(e.target.value)} />
        </Field>
        <Button variant="primary" disabled={busy || !deadline} onClick={() => void onCommit(task, deadline)}>
          Commit to it
        </Button>
        <span className="text-xs text-muted">Or leave it here.</span>
      </div>
    </Card>
  );
}
