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
import { Empty, Loading, Notice, PageHeader, SectionHeading } from "../components/Card";
import { EvidenceLink } from "../components/EvidenceLink";
import { Field, inputClass } from "../components/Field";
import { useSession } from "../app/session";
import { api } from "../lib/api";

export function InboxScreen() {
  const { timezone } = useSession();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [events, setEvents] = useState<MetisEvent[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    try {
      const [taskPage, eventPage] = await Promise.all([api.listTasks({ state: TaskState.INBOX }), api.listEvents()]);
      setTasks(taskPage.items ?? []);
      setEvents((eventPage.items ?? []).filter((e) => e.registration_status === RegistrationStatus.UNKNOWN));
      setError(null);
      setLoaded(true);
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

  if (error && !loaded)
    return (
      <Notice tone="bad" live action={<Button onClick={() => void load()}>Try again</Button>}>
        {error}
      </Notice>
    );
  if (!loaded) return <Loading label="Loading the inbox" />;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Inbox" sub="Things that are captured but still missing a detail. Leaving one here is fine." />
      {error && (
        <Notice tone="warn" live>
          {error}
        </Notice>
      )}
      <section aria-labelledby="h-need">
        <SectionHeading id="h-need" hint="missing information, not an error">
          Needs a detail
        </SectionHeading>
        {tasks.length === 0 ? (
          <Empty title="Nothing is waiting on you">Captured tasks that need a deadline will collect here.</Empty>
        ) : (
          <ul className="flex flex-col gap-2">
            {tasks.map((task) => (
              <InboxTask key={task.id} task={task} busy={busy === task.id} onCommit={commit} />
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="h-reg">
        <SectionHeading id="h-reg" hint="interest is not registration">
          Registration unknown
        </SectionHeading>
        {events.length === 0 ? (
          <Empty>No event is waiting on a registration answer.</Empty>
        ) : (
          <ul className="flex flex-col gap-2">
            {events.map((event) => (
              <li
                key={event.id}
                className="rounded-lg border border-line border-l-[3px] border-l-ink bg-surface p-4"
              >
                <p className="font-serif text-base font-semibold text-ink">{event.title}</p>
                <p className="text-xs text-muted">
                  {event.local_date ?? "no date"} <span aria-hidden="true">·</span> attendance{" "}
                  {event.attendance_status} <span aria-hidden="true">·</span> registration not recorded
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
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
                        <span className="capitalize">{status.replace("_", " ")}</span>
                      </Button>
                    ),
                  )}
                </div>
              </li>
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
    <li className="rounded-lg border border-line border-l-[3px] border-l-warn bg-surface p-4 [border-left-style:dashed]">
      <p className="font-serif text-base font-semibold text-ink">{task.title}</p>
      <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
        <span>
          <span aria-hidden="true" className="font-semibold text-warn">? </span>
          {task.deadline_local_date ? `due ${task.deadline_local_date}` : "No deadline. Nothing was assumed."}
        </span>
        {task.source_refs?.[0] && <EvidenceLink evidence={task.source_refs[0]} label={`from ${task.source_refs[0].source_id.slice(0, 12)}…`} />}
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <Field label="Deadline">
          <input type="date" className={inputClass} value={deadline} onChange={(e) => setDeadline(e.target.value)} />
        </Field>
        <Button variant="primary" disabled={busy || !deadline} onClick={() => void onCommit(task, deadline)}>
          Commit to it
        </Button>
        <span className="pb-2 text-xs text-muted">Or leave it here.</span>
      </div>
    </li>
  );
}
