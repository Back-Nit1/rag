import { describe, expect, it } from "vitest";
import { ErrorCode, ReminderState } from "../contracts/metis";
import { FLOW_STATUS, REMINDER_STATUS, flowStateForError, type FlowState } from "./status";

/** Every state the product specification requires must be expressible. */
const REQUIRED: FlowState[] = [
  "idle",
  "mic_denied",
  "recording",
  "canceled",
  "upload_pending",
  "offline",
  "queued",
  "transcribing",
  "transcript_ready",
  "extraction_pending",
  "clarification_required",
  "saved",
  "failed",
  "retrying",
  "provider_unavailable",
  "budget_exceeded",
  "notifications_disabled",
  "no_relevant_memory",
  "plan_conflict",
];

describe("status vocabulary", () => {
  it("has an honest label and a non-colour cue for every required state", () => {
    for (const state of REQUIRED) {
      const descriptor = FLOW_STATUS[state];
      expect(descriptor, state).toBeDefined();
      expect(descriptor.label.length, state).toBeGreaterThan(0);
      expect(descriptor.cue.length, state).toBeGreaterThan(0);
      expect(descriptor.detail.length, state).toBeGreaterThan(0);
    }
  });

  it("never calls an unsynchronised reminder scheduled", () => {
    expect(REMINDER_STATUS[ReminderState.PENDING_SCHEDULE].label).toBe("Scheduling reminder");
    expect(REMINDER_STATUS[ReminderState.SCHEDULED].label).toBe("Reminder scheduled");
    expect(REMINDER_STATUS[ReminderState.SCHEDULE_FAILED].label).toBe("Schedule needs retry");
  });

  it("covers every reminder state the contract can return", () => {
    for (const state of Object.values(ReminderState)) {
      expect(REMINDER_STATUS[state], state).toBeDefined();
    }
  });

  it("maps the error codes the user must be told apart", () => {
    expect(flowStateForError(ErrorCode.BUDGET_EXCEEDED)).toBe("budget_exceeded");
    expect(flowStateForError(ErrorCode.PROVIDER_UNAVAILABLE)).toBe("provider_unavailable");
    expect(flowStateForError(ErrorCode.RATE_LIMITED)).toBe("retrying");
    expect(flowStateForError(ErrorCode.VALIDATION_FAILED)).toBe("failed");
  });
});
