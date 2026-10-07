import { describe, expect, it } from "vitest";
import { DeliveryChannel, type Preferences } from "../contracts/metis";
import { buildPatch, dailyScheduleLabel, toDraft, validate } from "./prefs";

const prefs = {
  id: "p", user_id: "u", created_at: "", updated_at: "", revision: 4, timezone: "UTC", max_focus_tasks: 3,
  work_hours: [{ start: "09:00", end: "18:00" }], quiet_hours: [{ start: "22:00", end: "08:00" }],
  channels: [DeliveryChannel.IN_APP], daily_planning_enabled: false, daily_planning_local_time: "07:30",
} as Preferences;

describe("preferences patch", () => {
  it("is empty when nothing changed", () => expect(buildPatch(prefs, toDraft(prefs))).toEqual({}));
  it("sends only what changed", () => {
    const d = { ...toDraft(prefs), maxFocus: "5", webPush: true, dailyEnabled: true, dailyBudgetUsd: "1.5" };
    expect(buildPatch(prefs, d)).toEqual({
      max_focus_tasks: 5,
      channels: [DeliveryChannel.IN_APP, DeliveryChannel.WEB_PUSH],
      daily_planning_enabled: true,
      budgets: { daily_micro_usd: 1_500_000 },
    });
  });
  it("rejects bad input before sending", () => {
    expect(validate({ ...toDraft(prefs), maxFocus: "0" })).toMatch(/Focus/);
    expect(validate({ ...toDraft(prefs), workHours: [{ start: "10:00", end: "09:00" }] })).toMatch(/work window/);
    expect(validate(toDraft(prefs))).toBeNull();
  });
});

describe("daily schedule label", () => {
  it("only says scheduled for a confirmed state", () => {
    expect(dailyScheduleLabel({ ...prefs, daily_planning_enabled: true, daily_schedule_state: "pending_schedule" }).label).toMatch(/not confirmed/);
    expect(dailyScheduleLabel({ ...prefs, daily_schedule_state: "scheduled" }).tone).toBe("good");
    expect(dailyScheduleLabel({ ...prefs, daily_schedule_state: "schedule_failed" }).tone).toBe("warn");
    expect(dailyScheduleLabel({ ...prefs, daily_schedule_state: "disabled" }).label).toBe("Off");
  });
});
