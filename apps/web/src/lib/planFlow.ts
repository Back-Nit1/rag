/** Pure rules for what a plan may be shown as, and what may be done with it. */
import { ErrorCode, PlanStatus, type Plan, type PlanBlock, type TodayResponse } from "../contracts/metis";
import { ApiError } from "./api";

export const isWorking = (p: Plan) => p.status === PlanStatus.PENDING || p.status === PlanStatus.GENERATING;

/** A plan built on an older commitments epoch is never current, whatever its status says. */
export function isStale(plan: Plan, today: Pick<TodayResponse, "planning_epoch" | "plan_stale">): boolean {
  return plan.status === PlanStatus.INVALIDATED || !!today.plan_stale || plan.planning_epoch !== today.planning_epoch;
}

export function canAccept(plan: Plan, today: Pick<TodayResponse, "planning_epoch" | "plan_stale">): boolean {
  const reviewable = plan.status === PlanStatus.PROPOSED || plan.status === PlanStatus.FALLBACK;
  return reviewable && !isStale(plan, today) && plan.validation?.valid !== false;
}

/** Only an accepted plan on the current epoch is presented as accepted. */
export const isAcceptedCurrent = (plan: Plan, today: Pick<TodayResponse, "planning_epoch" | "plan_stale">) =>
  plan.status === PlanStatus.ACCEPTED && !isStale(plan, today);

const MINUTE = 60_000;
/** Shift one block, keeping its length. Date arithmetic on instants is timezone-safe. */
export function shiftBlock(blocks: PlanBlock[], index: number, minutes: number): PlanBlock[] {
  return blocks.map((b, i) =>
    i !== index
      ? b
      : {
          ...b,
          starts_at: new Date(Date.parse(b.starts_at) + minutes * MINUTE).toISOString(),
          ends_at: new Date(Date.parse(b.ends_at) + minutes * MINUTE).toISOString(),
        },
  );
}

export function planErrorMessage(cause: unknown): { text: string; reload: boolean } {
  if (cause instanceof ApiError) {
    if (cause.code === ErrorCode.EPOCH_CONFLICT)
      return { text: "Your commitments changed after this outline was generated. It was not accepted. Regenerate it.", reload: true };
    if (cause.code === ErrorCode.REVISION_CONFLICT)
      return { text: "This plan changed elsewhere. Nothing was overwritten; the latest version is shown.", reload: true };
    if (cause.code === ErrorCode.INVALID_STATE_TRANSITION) return { text: cause.message, reload: true };
    return { text: cause.message, reload: false };
  }
  return { text: "The request did not reach Metis. Nothing was changed.", reload: false };
}
