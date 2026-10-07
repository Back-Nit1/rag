/**
 * Today: fixed commitments, flexible work, and an honest account of what the
 * server does not know.
 *
 * Missing availability is stated, not inferred, and a plan that was never
 * generated shows as absent rather than as an empty success. Only the
 * configured number of focus tasks is promoted; the rest stay visible in the
 * backlog instead of disappearing.
 */
import { useCallback, useEffect, useState } from "react";
import { TaskState, type TodayResponse } from "../contracts/metis";
import { Button } from "../components/Button";
import { Empty, Loading, Notice, PageHeader, SectionHeading } from "../components/Card";
import { Icon } from "../components/Icon";
import { useSession } from "../app/session";
import { api } from "../lib/api";
import { dateLabel } from "../lib/format";
import { AvailabilityPanel } from "../components/AvailabilityPanel";
import { EvidenceLink } from "../components/EvidenceLink";
import { PlanPanel } from "../components/PlanPanel";

const hourLabel = (iso: string | null | undefined, tz: string) =>
  iso
    ? new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", timeZone: tz }).format(new Date(iso))
    : "time not set";

export function TodayScreen({ onOpenCapture }: { onOpenCapture: () => void }) {
  const { timezone, preferences } = useSession();
  const [today, setToday] = useState<TodayResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyTask, setBusyTask] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setToday(await api.today());
      setError(null);
    } catch {
      setError("Today could not be loaded. Nothing was changed.");
    }
  }, []);

  useEffect(() => void load(), [load]);

  const move = async (taskId: string, revision: number, state: TaskState, done: string) => {
    setBusyTask(taskId);
    setNotice(null);
    try {
      await api.patchTask(taskId, { expected_revision: revision, state });
      await load();
      setNotice(done);
    } finally {
      setBusyTask(null);
    }
  };
  const complete = (id: string, rev: number) => move(id, rev, TaskState.DONE, "Task completed.");
  const defer = (id: string, rev: number) => move(id, rev, TaskState.INBOX, "Task moved back to the inbox.");

  if (error)
    return (
      <Notice
        tone="bad"
        live
        action={
          <Button onClick={() => void load()}>Try again</Button>
        }
      >
        {error}
      </Notice>
    );
  if (!today) return <Loading label="Loading today" />;

  const focusLimit = preferences?.max_focus_tasks ?? 3;
  const open = today.open_tasks ?? [];
  const focus = open.slice(0, focusLimit);
  const backlog = open.slice(focusLimit);
  const overdue = today.overdue_tasks ?? [];
  const events = [...(today.fixed_events ?? [])].sort((a, b) => (a.starts_at ?? "").localeCompare(b.starts_at ?? ""));
  const date = dateLabel(`${today.local_date}T12:00:00Z`, today.timezone);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={date}
        sub={
          <>
            {today.timezone} <span aria-hidden="true">·</span> planning epoch {today.planning_epoch}
          </>
        }
        action={
          <Button variant="primary" onClick={onOpenCapture} className="max-lg:hidden">
            <Icon name="mic" size={16} /> Capture a thought
          </Button>
        }
      />

      {(!today.availability_configured || today.inputs_complete === false) && (
        <div className="flex flex-col gap-2">
          {!today.availability_configured && (
            <Notice tone="warn">
              <strong className="font-semibold">Free time is unknown.</strong> No calendar is connected and no
              availability was entered, so nothing below claims to have found a gap.
            </Notice>
          )}
          {today.inputs_complete === false && (
            <Notice tone="info">Some planning inputs are missing; any ordering here is partial.</Notice>
          )}
        </div>
      )}

      <p role="status" className="visually-hidden">
        {notice}
      </p>

      <dl className="grid grid-cols-3 divide-x divide-line overflow-hidden rounded-lg border border-line bg-surface shadow-card">
        {[
          ["Appointments", events.length, "fixed"],
          ["Focus", focus.length, `of ${open.length} open`],
          ["Overdue", overdue.length, overdue.length ? "needs a decision" : "none"],
        ].map(([label, n, note]) => (
          <div key={label} className="px-4 py-3 md:px-5">
            <dt className="font-mono text-2xs font-semibold uppercase tracking-[0.12em] text-muted">{label}</dt>
            <dd className={`font-serif text-3xl font-semibold leading-none ${label === "Overdue" && n ? "text-warn" : "text-ink"}`}>
              {n}
              <span className="ml-2 font-sans text-xs font-normal text-muted">{note}</span>
            </dd>
          </div>
        ))}
      </dl>

      <div className="flex flex-col gap-6 lg:grid lg:grid-cols-[minmax(0,1fr)_22rem] lg:items-start lg:gap-8">
        <div className="contents lg:flex lg:flex-col lg:gap-6">
          <section aria-labelledby="h-focus" className="order-2">
            <SectionHeading id="h-focus" hint={`${focus.length} of ${open.length} open tasks`}>
              Focus
            </SectionHeading>
            {focus.length === 0 ? (
              <Empty
                icon="tasks"
                title="No focus work yet"
                action={
                  <Button onClick={onOpenCapture}>
                    <Icon name="mic" size={16} /> Capture a thought
                  </Button>
                }
              >
                Nothing flexible is ready for today. Capture something, or commit a task from the inbox.
              </Empty>
            ) : (
              <ul className="flex flex-col gap-2">
                {focus.map((task) => (
                  <li
                    key={task.id}
                    className="rounded-lg border border-line border-l-[4px] border-l-accent bg-surface p-4 shadow-card [border-left-style:dashed]"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
                      <div className="min-w-0 flex-1 basis-56">
                        <p className="font-serif text-base font-semibold leading-snug text-ink">{task.title}</p>
                        <p className="mt-1 text-xs text-muted">
                          {task.state} <span aria-hidden="true">·</span>{" "}
                          {task.deadline_local_date ? `due ${task.deadline_local_date}` : "no deadline"}{" "}
                          <span aria-hidden="true">·</span>{" "}
                          {task.duration_estimate
                            ? `${task.duration_estimate.minutes} min (${task.duration_estimate.provenance} estimate)`
                            : "no estimate"}
                        </p>
                        {task.source_refs?.[0] && (
                          <p className="mt-1.5">
                            <EvidenceLink evidence={task.source_refs[0]} label={`from ${task.source_refs[0].source_id.slice(0, 12)}…`} />
                          </p>
                        )}
                      </div>
                      <div className="flex gap-2">
                        <Button
                          disabled={busyTask === task.id}
                          onClick={() => void complete(task.id, task.revision ?? 1)}
                        >
                          <Icon name="check" size={16} /> Complete
                        </Button>
                        <Button
                          variant="quiet"
                          disabled={busyTask === task.id}
                          onClick={() => void defer(task.id, task.revision ?? 1)}
                        >
                          Defer to inbox
                        </Button>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {overdue.length > 0 && (
            <section aria-labelledby="h-overdue" className="order-3">
              <SectionHeading id="h-overdue" hint="past the deadline">
                Overdue
              </SectionHeading>
              <ul className="flex flex-col gap-2">
                {overdue.map((task) => (
                  <li
                    key={task.id}
                    className="flex items-start gap-3 rounded-lg border border-warn/50 bg-warn-soft p-3.5 text-warn"
                  >
                    <Icon name="alert" size={18} />
                    <div className="min-w-0">
                      <p className="font-serif text-base font-semibold leading-snug text-ink">{task.title}</p>
                      <p className="text-xs">Overdue: was due {task.deadline_local_date ?? task.deadline_at}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {backlog.length > 0 && (
            <section aria-labelledby="h-backlog" className="order-4">
              <SectionHeading id="h-backlog" hint="visible, not promoted">
                Backlog
              </SectionHeading>
              <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
                {backlog.map((task) => (
                  <li key={task.id} className="defer-paint px-4 py-2.5 text-sm text-muted">
                    {task.title}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>

        <div className="contents lg:flex lg:flex-col lg:gap-6">
          <section aria-labelledby="h-appts" className="order-1">
            <SectionHeading id="h-appts" hint="fixed times">
              Appointments
            </SectionHeading>
            {events.length === 0 ? (
              <Empty icon="today">No fixed appointments known for today.</Empty>
            ) : (
              <ol className="flex flex-col gap-2">
                {events.map((event) => (
                  <li
                    key={event.id}
                    className="grid grid-cols-[4.75rem_1fr] gap-3 rounded-lg border border-line border-l-[4px] border-l-ink bg-surface p-3.5 shadow-card"
                  >
                    <p className="font-mono text-lg font-bold leading-snug text-ink">
                      {hourLabel(event.starts_at, event.timezone || timezone)}
                    </p>
                    <div className="min-w-0">
                      <p className="font-serif text-base font-semibold leading-snug text-ink">{event.title}</p>
                      <p className="mt-0.5 text-xs text-muted">
                        <span className="font-medium text-ink">Fixed</span> <span aria-hidden="true">·</span>{" "}
                        {event.location_text ?? "no location"}
                      </p>
                      <p className="mt-0.5 text-xs text-muted">
                        Attendance {event.attendance_status}; registration {event.registration_status}
                      </p>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </section>

          <PlanPanel today={today} onChanged={load} />
          <AvailabilityPanel today={today} onChanged={load} />
        </div>
      </div>
    </div>
  );
}
