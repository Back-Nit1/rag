/** Preferences editing: a draft form and the minimal patch it implies. */
import { DeliveryChannel, type Preferences, type TimeWindow } from "../contracts/metis";

export type PrefsDraft = {
  timezone: string;
  maxFocus: string;
  workHours: TimeWindow[];
  quietHours: TimeWindow[];
  webPush: boolean;
  dailyEnabled: boolean;
  dailyTime: string;
  dailyBudgetUsd: string;
};

export const toDraft = (p: Preferences): PrefsDraft => ({
  timezone: p.timezone ?? "",
  maxFocus: String(p.max_focus_tasks ?? 3),
  workHours: (p.work_hours ?? []).map((w) => ({ ...w })),
  quietHours: (p.quiet_hours ?? []).map((w) => ({ ...w })),
  webPush: (p.channels ?? []).includes(DeliveryChannel.WEB_PUSH),
  dailyEnabled: !!p.daily_planning_enabled,
  dailyTime: p.daily_planning_local_time ?? "07:30",
  dailyBudgetUsd: p.budgets?.daily_micro_usd == null ? "" : String(p.budgets.daily_micro_usd / 1_000_000),
});

const same = (a: TimeWindow[], b: TimeWindow[]) => JSON.stringify(a) === JSON.stringify(b);

/** Validation message, or null when the draft can be sent. */
export function validate(d: PrefsDraft): string | null {
  const focus = Number(d.maxFocus);
  if (!Number.isInteger(focus) || focus < 1 || focus > 10) return "Focus tasks must be a whole number from 1 to 10.";
  for (const w of [...d.workHours, ...d.quietHours]) if (!w.start || !w.end) return "Every window needs a start and an end.";
  if (d.workHours.some((w) => w.end <= w.start)) return "A work window must end after it starts. Overnight is only for quiet hours.";
  if (d.dailyBudgetUsd && !(Number(d.dailyBudgetUsd) >= 0)) return "The daily budget must be a dollar amount.";
  if (!d.timezone.trim()) return "A timezone is required.";
  return null;
}

/** Only fields that changed; empty when nothing did. */
export function buildPatch(p: Preferences, d: PrefsDraft): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  if (d.timezone.trim() !== (p.timezone ?? "")) patch.timezone = d.timezone.trim();
  if (Number(d.maxFocus) !== (p.max_focus_tasks ?? 3)) patch.max_focus_tasks = Number(d.maxFocus);
  if (!same(d.workHours, p.work_hours ?? [])) patch.work_hours = d.workHours;
  if (!same(d.quietHours, p.quiet_hours ?? [])) patch.quiet_hours = d.quietHours;
  const channels = d.webPush ? [DeliveryChannel.IN_APP, DeliveryChannel.WEB_PUSH] : [DeliveryChannel.IN_APP];
  const have = new Set(p.channels ?? []);
  if (have.size !== channels.length || channels.some((c) => !have.has(c))) patch.channels = channels;
  if (d.dailyEnabled !== !!p.daily_planning_enabled) patch.daily_planning_enabled = d.dailyEnabled;
  if (d.dailyTime !== (p.daily_planning_local_time ?? "07:30") && d.dailyTime) patch.daily_planning_local_time = d.dailyTime;
  const micro = d.dailyBudgetUsd === "" ? null : Math.round(Number(d.dailyBudgetUsd) * 1_000_000);
  if (micro !== null && micro !== (p.budgets?.daily_micro_usd ?? null)) patch.budgets = { ...(p.budgets ?? {}), daily_micro_usd: micro };
  return patch;
}

/** What the daily-plan schedule is, in the server's own terms. Never "scheduled" unless confirmed. */
export function dailyScheduleLabel(p: Preferences): { label: string; tone: "good" | "warn" | "neutral" | "busy" } {
  switch (p.daily_schedule_state) {
    case "scheduled":
      return { label: "Scheduled: the scheduler confirmed the daily plan", tone: "good" };
    case "pending_schedule":
      return { label: "Scheduling: not confirmed yet", tone: "busy" };
    case "schedule_failed":
      return { label: "Schedule failed: the daily plan will not run until this is fixed", tone: "warn" };
    default:
      return { label: p.daily_planning_enabled ? "Not confirmed by the scheduler" : "Off", tone: "neutral" };
  }
}
