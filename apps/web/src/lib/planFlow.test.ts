import { describe, expect, it } from "vitest";
import { ErrorCode, PlanStatus, type Plan, type CaptureDetail } from "../contracts/metis";
import { ApiError } from "./api";
import { locate } from "./evidence";
import { canAccept, isAcceptedCurrent, planErrorMessage, shiftBlock } from "./planFlow";

const plan = (over: Partial<Plan> = {}): Plan =>
  ({ id: "p", user_id: "u", created_at: "", updated_at: "", local_date: "2026-10-07", timezone: "UTC", planning_epoch: 3, time_budget_minutes: 60, status: PlanStatus.PROPOSED, ...over }) as Plan;

describe("plan rules", () => {
  it("accepts only a proposed plan on the current epoch", () => {
    expect(canAccept(plan(), { planning_epoch: 3 })).toBe(true);
    expect(canAccept(plan(), { planning_epoch: 4 })).toBe(false);
    expect(canAccept(plan(), { planning_epoch: 3, plan_stale: true })).toBe(false);
    expect(canAccept(plan({ status: PlanStatus.INVALIDATED }), { planning_epoch: 3 })).toBe(false);
    expect(canAccept(plan({ validation: { valid: false, capacity_minutes: 0, scheduled_minutes: 1 } }), { planning_epoch: 3 })).toBe(false);
  });
  it("never presents an accepted plan as current once its epoch is stale", () => {
    const accepted = plan({ status: PlanStatus.ACCEPTED });
    expect(isAcceptedCurrent(accepted, { planning_epoch: 3 })).toBe(true);
    expect(isAcceptedCurrent(accepted, { planning_epoch: 4 })).toBe(false);
  });
  it("shifts a block without changing its length", () => {
    const b = { title: "x", starts_at: "2026-10-07T09:00:00.000Z", ends_at: "2026-10-07T09:30:00.000Z" };
    const [s] = shiftBlock([b], 0, 15);
    expect(s?.starts_at).toBe("2026-10-07T09:15:00.000Z");
    expect(s?.ends_at).toBe("2026-10-07T09:45:00.000Z");
  });
  it("names the epoch conflict explicitly", () => {
    const e = new ApiError(409, { code: ErrorCode.EPOCH_CONFLICT, message: "x" });
    expect(planErrorMessage(e)).toMatchObject({ reload: true });
    expect(planErrorMessage(e).text).toMatch(/commitments changed/);
  });
});

describe("evidence", () => {
  const detail = {
    source: {
      current_version: 2,
      transcripts: [{ source_version: 2, text: "call Sam about the grant today" }],
      spans: [{ span_ref: "s1", source_id: "src", source_version: 2, start_char: 5, end_char: 8 }],
    },
  } as unknown as CaptureDetail;
  it("marks the cited span", () => {
    expect(locate(detail, { source_id: "src", source_version: 2, span_ref: "s1" })).toEqual({ before: "call ", match: "Sam", after: " about the grant today" });
  });
  it("shows the cited version without a span when none resolves, and null when the version is gone", () => {
    expect(locate(detail, { source_id: "src", source_version: 2, span_ref: "nope" })).toMatchObject({ span: null });
    expect(locate(detail, { source_id: "src", source_version: 1 })).toBeNull();
  });
});
