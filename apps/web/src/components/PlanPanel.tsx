/**
 * Request, review, adjust, accept and regenerate one day's plan.
 *
 * The server owns every fact here. The panel never calls a plan accepted or
 * current on its own: acceptance is whatever the API returned, and a plan on
 * an older commitments epoch is shown as out of date with Accept disabled.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { PlanStatus, type Plan, type TodayResponse } from "../contracts/metis";
import { api } from "../lib/api";
import { timeLabel } from "../lib/format";
import { canAccept, isAcceptedCurrent, isStale, isWorking, planErrorMessage, shiftBlock } from "../lib/planFlow";
import { Button } from "./Button";
import { Chip, Empty, Notice, SectionHeading } from "./Card";
import { EvidenceLink } from "./EvidenceLink";
import { Field, inputClass } from "./Field";
import { StatusPill } from "./StatusPill";

const POLL_MS = 1500;
const POLL_MAX = 60;

const LABEL: Record<PlanStatus, string> = {
  [PlanStatus.PENDING]: "Queued",
  [PlanStatus.GENERATING]: "Generating",
  [PlanStatus.PROPOSED]: "Proposed, not accepted",
  [PlanStatus.ACCEPTED]: "Accepted",
  [PlanStatus.INVALIDATED]: "Out of date",
  [PlanStatus.FAILED]: "Generation failed",
  [PlanStatus.FALLBACK]: "Simple fallback outline",
};

export function PlanPanel({ today, onChanged }: { today: TodayResponse; onChanged: () => Promise<void> }) {
  const [local, setLocal] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [minutes, setMinutes] = useState("");
  const [notes, setNotes] = useState("");
  const [gaveUp, setGaveUp] = useState(false);
  const key = useRef<string | null>(null);

  const plan = local ?? today.plan ?? null;
  useEffect(() => setLocal(null), [today.plan?.id, today.plan?.revision, today.planning_epoch]);

  // Poll while the server is still generating; stop on any settled status.
  const watching = plan && isWorking(plan) ? plan.id : null;
  useEffect(() => {
    if (!watching) return;
    let tries = 0;
    let stop = false;
    const tick = async () => {
      if (stop) return;
      try {
        const next = await api.getPlan(watching);
        if (stop) return;
        if (!isWorking(next)) return void (setLocal(null), void onChanged());
        setLocal(next);
      } catch {
        /* the next tick retries */
      }
      if (++tries >= POLL_MAX) return setGaveUp(true);
      timer = window.setTimeout(tick, POLL_MS);
    };
    let timer = window.setTimeout(tick, POLL_MS);
    setGaveUp(false);
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [watching, onChanged]);

  const run = useCallback(
    async (name: string, action: () => Promise<Plan | void>) => {
      setBusy(name);
      setError(null);
      try {
        const result = await action();
        if (result) setLocal(result);
        await onChanged();
      } catch (cause) {
        const { text, reload } = planErrorMessage(cause);
        setError(text);
        if (reload) await onChanged();
      } finally {
        setBusy(null);
      }
    },
    [onChanged],
  );

  const request = () =>
    run("request", async () => {
      // One key per intent: a retry after a dropped response replays, not duplicates.
      key.current ??= `plan:${crypto.randomUUID()}`;
      const created = await api.createPlan(
        {
          local_date: today.local_date,
          timezone: today.timezone,
          time_budget_minutes: minutes ? Number(minutes) : null,
          notes: notes.trim() || null,
        },
        key.current,
      );
      key.current = null;
      return api.getPlan(created.plan_id);
    });

  const accept = (p: Plan) =>
    run("accept", () => api.acceptPlan(p.id, { expected_revision: p.revision ?? 1, expected_planning_epoch: today.planning_epoch }));

  const adjust = (p: Plan, body: { blocks?: Plan["blocks"]; pinned_block_indexes?: number[] }) =>
    run("adjust", () => api.patchPlan(p.id, { expected_revision: p.revision ?? 1, ...body }));

  const status = plan?.status ?? PlanStatus.PENDING;
  const stale = plan ? isStale(plan, today) : false;
  const tz = plan?.timezone ?? today.timezone;

  const form = (
    <div className="flex flex-wrap items-end gap-3">
      <Field label="Time budget (minutes)" hint="Optional.">
        <input className={`${inputClass} w-36`} type="number" min={15} step={15} inputMode="numeric" value={minutes} onChange={(e) => setMinutes(e.target.value)} />
      </Field>
      <div className="min-w-48 flex-1">
        <Field label="Notes for the planner" hint="Optional.">
          <input className={inputClass} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </Field>
      </div>
    </div>
  );

  return (
    <section aria-labelledby="h-plan" className="order-5">
      <SectionHeading id="h-plan" hint={plan ? `revision ${plan.revision ?? 1} · epoch ${plan.planning_epoch}` : "none generated"}>
        Plan
      </SectionHeading>
      {error && (
        <Notice tone="warn" live className="mb-3">
          {error}
        </Notice>
      )}
      {!plan ? (
        <Empty
          icon="tasks"
          title="No plan for today"
          action={
            <div className="flex flex-col gap-3">
              {form}
              <div>
                <Button variant="primary" disabled={busy === "request"} onClick={() => void request()}>
                  {busy === "request" ? "Requesting…" : "Plan my day"}
                </Button>
              </div>
            </div>
          }
        >
          Planning is a separate step and nothing here was invented in its place.
        </Empty>
      ) : (
        <div className="rounded-lg border border-line bg-surface p-4">
          <StatusPill
            status={{
              label: stale && status !== PlanStatus.INVALIDATED ? "Out of date" : LABEL[status],
              detail: stale ? "Your commitments changed after this was generated." : (plan.change_summary ?? ""),
              tone: stale || status === PlanStatus.FAILED ? "warn" : status === PlanStatus.ACCEPTED ? "good" : isWorking(plan) ? "busy" : "neutral",
              cue: isWorking(plan) ? "…" : stale ? "!" : status === PlanStatus.ACCEPTED ? "✓" : "·",
              busy: isWorking(plan),
            }}
          />
          {isWorking(plan) && (
            <p role="status" className="mt-2 text-xs text-muted">
              {gaveUp ? "Still working on the server. Reload to check again." : "The server is generating this plan. Nothing is accepted yet."}
            </p>
          )}
          {status === PlanStatus.FALLBACK && (
            <Notice tone="info" className="mt-3">
              Model planning was not used, so this is a plain ordered outline. Review it before accepting.
            </Notice>
          )}
          {status === PlanStatus.FAILED && <Notice tone="bad" className="mt-3">The plan could not be generated. Nothing was scheduled.</Notice>}
          {stale && (
            <Notice tone="warn" className="mt-3">
              {today.plan_stale_reason ?? "Your commitments changed after this outline was generated."} It cannot be accepted; regenerate it.
            </Notice>
          )}
          {isAcceptedCurrent(plan, today) && plan.accepted_at && (
            <p className="mt-2 text-xs text-muted">Accepted {timeLabel(plan.accepted_at, tz)}.</p>
          )}

          <ol className="mt-3 flex flex-col gap-2">
            {(plan.blocks ?? []).map((block, index) => (
              <li
                key={index}
                className={`rounded-md border-l-[3px] bg-canvas px-3 py-2 ${block.fixed ? "border-l-ink" : "border-l-accent [border-left-style:dashed]"}`}
              >
                <p className="text-sm font-medium text-ink">
                  {block.title} {block.pinned && <Chip>pinned</Chip>}
                </p>
                <p className="font-mono text-xs text-muted">
                  {timeLabel(block.starts_at, tz)} – {timeLabel(block.ends_at, tz)}{" "}
                  <span className="font-sans">
                    {block.fixed ? "fixed" : "flexible"}
                    {block.estimate ? ` · ${block.estimate.minutes} min (${block.estimate.provenance} estimate)` : ""}
                  </span>
                </p>
                {block.rationale && <p className="mt-1 text-xs text-muted">{block.rationale}</p>}
                {block.completion_criterion && <p className="mt-1 text-xs text-muted">Done when: {block.completion_criterion}</p>}
                {(block.evidence ?? []).length > 0 && (
                  <p className="mt-1.5 flex flex-wrap gap-1.5">
                    {(block.evidence ?? []).map((ev, i) => (
                      <EvidenceLink key={i} evidence={ev} />
                    ))}
                  </p>
                )}
                {!stale && !block.fixed && (status === PlanStatus.PROPOSED || status === PlanStatus.FALLBACK) && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    <Button variant="quiet" disabled={busy !== null} onClick={() => void adjust(plan, { blocks: shiftBlock(plan.blocks ?? [], index, -15) })}>
                      15 min earlier
                    </Button>
                    <Button variant="quiet" disabled={busy !== null} onClick={() => void adjust(plan, { blocks: shiftBlock(plan.blocks ?? [], index, 15) })}>
                      15 min later
                    </Button>
                    <Button
                      variant="quiet"
                      aria-pressed={!!block.pinned}
                      disabled={busy !== null}
                      onClick={() =>
                        void adjust(plan, {
                          pinned_block_indexes: (plan.blocks ?? []).flatMap((b, i) => ((i === index ? !b.pinned : b.pinned) ? [i] : [])),
                        })
                      }
                    >
                      {block.pinned ? "Unpin" : "Pin"}
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ol>

          {plan.validation && (
            <div className="mt-3 text-xs text-muted">
              <p>
                Checked by the app: {plan.validation.scheduled_minutes} of {plan.validation.capacity_minutes} minutes scheduled
                {plan.validation.valid ? "" : " — this arrangement is not valid"}.
              </p>
              {(plan.validation.issues ?? []).length > 0 && (
                <ul className="mt-1 list-disc pl-5">
                  {(plan.validation.issues ?? []).map((issue, i) => (
                    <li key={i} className={issue.severity === "error" ? "text-bad" : "text-warn"}>
                      {issue.message}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {(plan.overflow_task_ids ?? []).length > 0 && (
            <p className="mt-1 text-xs text-muted">{plan.overflow_task_ids?.length} task(s) did not fit and stay in the backlog.</p>
          )}
          {(plan.assumptions ?? []).length > 0 && (
            <p className="mt-1 text-xs text-muted">Assumed: {(plan.assumptions ?? []).join("; ")}</p>
          )}

          {!isWorking(plan) && (
            <div className="mt-4 flex flex-col gap-3 border-t border-line pt-3">
              {(stale || status === PlanStatus.ACCEPTED || status === PlanStatus.INVALIDATED || status === PlanStatus.FAILED) && form}
              <div className="flex flex-wrap gap-2">
                {canAccept(plan, today) && (
                  <Button variant="primary" disabled={busy !== null} onClick={() => void accept(plan)}>
                    {busy === "accept" ? "Accepting…" : "Accept plan"}
                  </Button>
                )}
                <Button variant={stale ? "primary" : "secondary"} disabled={busy !== null} onClick={() => void request()}>
                  {busy === "request" ? "Requesting…" : "Regenerate"}
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
