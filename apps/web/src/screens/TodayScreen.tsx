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
import { PlanStatus, TaskState, type TodayResponse } from "../contracts/metis";
import { Button } from "../components/Button";
import { Card, Empty, SectionHeading } from "../components/Card";
import { useSession } from "../app/session";
import { api } from "../lib/api";
import { dateLabel, timeLabel } from "../lib/format";
import { FLOW_STATUS } from "../lib/status";
import { StatusPill } from "../components/StatusPill";

export function TodayScreen({ onOpenCapture }: { onOpenCapture: () => void }) {
  const { timezone, preferences } = useSession();
  const [today, setToday] = useState<TodayResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
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

  const complete = async (taskId: string, revision: number) => {
    setBusyTask(taskId);
    try {
      await api.patchTask(taskId, { expected_revision: revision, state: TaskState.DONE });
      await load();
    } finally {
      setBusyTask(null);
    }
  };

  const defer = async (taskId: string, revision: number) => {
    setBusyTask(taskId);
    try {
      await api.patchTask(taskId, { expected_revision: revision, state: TaskState.INBOX });
      await load();
    } finally {
      setBusyTask(null);
    }
  };

  if (error) return <Card>{error}</Card>;
  if (!today) return <Card>Loading today…</Card>;

  const focusLimit = preferences?.max_focus_tasks ?? 3;
  const open = today.open_tasks ?? [];
  const focus = open.slice(0, focusLimit);
  const backlog = open.slice(focusLimit);
  const overdue = today.overdue_tasks ?? [];

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h1 className="text-lg font-semibold text-ink">{dateLabel(`${today.local_date}T12:00:00Z`, today.timezone)}</h1>
            <p className="text-xs text-muted">{today.timezone} · planning epoch {today.planning_epoch}</p>
          </div>
          <Button variant="primary" onClick={onOpenCapture}>
            Capture a thought
          </Button>
        </div>

        {!today.availability_configured && (
          <p className="mt-3 rounded-lg border border-warn/40 bg-warn-soft px-3 py-2 text-xs text-warn">
            No calendar is connected and no availability was entered, so free time is unknown. Nothing below claims
            to have found one.
          </p>
        )}
        {today.inputs_complete === false && (
          <p className="mt-2 text-xs text-muted">Some planning inputs are missing; any ordering here is partial.</p>
        )}
      </Card>

      <section>
        <SectionHeading hint="fixed times">Appointments</SectionHeading>
        {(today.fixed_events ?? []).length === 0 ? (
          <Empty>No fixed appointments known for today.</Empty>
        ) : (
          <ul className="flex flex-col gap-2">
            {(today.fixed_events ?? []).map((event) => (
              <Card as="li" key={event.id}>
                <p className="text-sm font-medium text-ink">{event.title}</p>
                <p className="text-xs text-muted">
                  {timeLabel(event.starts_at, event.timezone || timezone)} · {event.location_text ?? "no location"} ·
                  attendance {event.attendance_status} · registration {event.registration_status}
                </p>
              </Card>
            ))}
          </ul>
        )}
      </section>

      <section>
        <SectionHeading hint={`${focus.length} of ${open.length} shown`}>Focus</SectionHeading>
        {focus.length === 0 ? (
          <Empty>Nothing flexible is ready for today.</Empty>
        ) : (
          <ul className="flex flex-col gap-2">
            {focus.map((task) => (
              <Card as="li" key={task.id}>
                <p className="text-sm font-medium text-ink">{task.title}</p>
                <p className="text-xs text-muted">
                  {task.state} · {task.deadline_local_date ? `due ${task.deadline_local_date}` : "no deadline"} ·{" "}
                  {task.duration_estimate
                    ? `${task.duration_estimate.minutes} min (${task.duration_estimate.provenance} estimate)`
                    : "no estimate"}
                  {task.source_refs?.[0] ? ` · from ${task.source_refs[0].source_id.slice(0, 12)}…` : ""}
                </p>
                <div className="mt-2 flex gap-2">
                  <Button disabled={busyTask === task.id} onClick={() => void complete(task.id, task.revision ?? 1)}>
                    Complete
                  </Button>
                  <Button
                    variant="quiet"
                    disabled={busyTask === task.id}
                    onClick={() => void defer(task.id, task.revision ?? 1)}
                  >
                    Defer to inbox
                  </Button>
                </div>
              </Card>
            ))}
          </ul>
        )}
      </section>

      {overdue.length > 0 && (
        <section>
          <SectionHeading hint="past the deadline">Overdue</SectionHeading>
          <ul className="flex flex-col gap-2">
            {overdue.map((task) => (
              <Card as="li" key={task.id} className="border-warn/40">
                <p className="text-sm font-medium text-ink">{task.title}</p>
                <p className="text-xs text-warn">due {task.deadline_local_date ?? task.deadline_at}</p>
              </Card>
            ))}
          </ul>
        </section>
      )}

      {backlog.length > 0 && (
        <section>
          <SectionHeading hint="visible, not promoted">Backlog</SectionHeading>
          <ul className="flex flex-col gap-1">
            {backlog.map((task) => (
              <li key={task.id} className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-muted">
                {task.title}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <SectionHeading hint={today.plan ? `revision ${today.plan.revision}` : "none generated"}>Plan</SectionHeading>
        {!today.plan ? (
          <Empty>
            No plan has been generated for today. Planning is a separate step and nothing here was invented in its
            place.
          </Empty>
        ) : (
          <Card>
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill
                status={
                  today.plan_stale
                    ? FLOW_STATUS.plan_conflict
                    : {
                        label: today.plan.status ?? PlanStatus.PROPOSED,
                        detail: today.plan.change_summary ?? "No material change explained.",
                        tone: "neutral",
                        cue: "·",
                        busy: false,
                      }
                }
              />
            </div>
            {today.plan_stale_reason && <p className="mt-2 text-xs text-warn">{today.plan_stale_reason}</p>}
            <ul className="mt-3 flex flex-col gap-2">
              {(today.plan.blocks ?? []).map((block, index) => (
                <li key={index} className="rounded-lg border border-line bg-canvas px-3 py-2">
                  <p className="text-sm font-medium text-ink">{block.title}</p>
                  <p className="text-xs text-muted">
                    {timeLabel(block.starts_at, today.timezone)} – {timeLabel(block.ends_at, today.timezone)}
                    {block.fixed ? " · fixed" : " · flexible"}
                  </p>
                  {block.rationale && <p className="mt-1 text-xs text-muted">{block.rationale}</p>}
                </li>
              ))}
            </ul>
          </Card>
        )}
      </section>
    </div>
  );
}
