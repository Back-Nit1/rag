/**
 * The product's status vocabulary, in one place.
 *
 * Every state the specification requires has an honest label here, and each
 * carries a text/symbol cue as well as a tone, so colour is never the only
 * indicator. "Uploaded" is not "transcribed" and "scheduling" is not
 * "scheduled" — those distinctions are the point of this file.
 */
import { ErrorCode, ReminderState } from "../contracts/metis";

export type FlowState =
  | "idle"
  | "mic_denied"
  | "recording"
  | "paused"
  | "canceled"
  | "upload_pending"
  | "offline"
  | "queued"
  | "transcribing"
  | "transcript_ready"
  | "extraction_pending"
  | "clarification_required"
  | "saved"
  | "failed"
  | "retrying"
  | "provider_unavailable"
  | "budget_exceeded"
  | "notifications_disabled"
  | "no_relevant_memory"
  | "plan_conflict";

export type Tone = "neutral" | "busy" | "good" | "warn" | "bad";

export type StatusDescriptor = {
  label: string;
  detail: string;
  tone: Tone;
  /** Text cue so the state reads without colour. */
  cue: string;
  /** Drives aria-busy and the assertiveness of the live region. */
  busy: boolean;
};

export const FLOW_STATUS: Record<FlowState, StatusDescriptor> = {
  idle: { label: "Ready", detail: "Record or type a thought.", tone: "neutral", cue: "·", busy: false },
  mic_denied: {
    label: "Microphone blocked",
    detail: "The browser denied microphone access. Typed capture still works.",
    tone: "warn",
    cue: "!",
    busy: false,
  },
  recording: { label: "Recording", detail: "Listening.", tone: "busy", cue: "●", busy: true },
  paused: { label: "Paused", detail: "Recording paused — resume or stop.", tone: "neutral", cue: "||", busy: false },
  canceled: {
    label: "Recording canceled",
    detail: "Nothing was uploaded and nothing was saved.",
    tone: "neutral",
    cue: "×",
    busy: false,
  },
  upload_pending: {
    label: "Uploading audio",
    detail: "The recording is uploading. Uploaded is not yet transcribed.",
    tone: "busy",
    cue: "↑",
    busy: true,
  },
  offline: {
    label: "Offline — held locally",
    detail: "This capture is in the outbox on this device and will sync when you reconnect.",
    tone: "warn",
    cue: "⛌",
    busy: false,
  },
  queued: {
    label: "Queued",
    detail: "Accepted by the server and waiting for a worker.",
    tone: "busy",
    cue: "…",
    busy: true,
  },
  transcribing: { label: "Transcribing", detail: "Turning the audio into text.", tone: "busy", cue: "~", busy: true },
  transcript_ready: {
    label: "Transcript ready",
    detail: "Correct anything misheard before the fields are committed.",
    tone: "good",
    cue: "✓",
    busy: false,
  },
  extraction_pending: {
    label: "Extracting",
    detail: "Looking for tasks, events and reminders in the note.",
    tone: "busy",
    cue: "…",
    busy: true,
  },
  clarification_required: {
    label: "Needs your answer",
    detail: "Something is missing. Nothing was guessed.",
    tone: "warn",
    cue: "?",
    busy: false,
  },
  saved: { label: "Saved", detail: "The records below were created.", tone: "good", cue: "✓", busy: false },
  failed: { label: "Failed", detail: "Your input is kept. You can retry.", tone: "bad", cue: "✕", busy: false },
  retrying: { label: "Retrying", detail: "Trying the same request again.", tone: "busy", cue: "↻", busy: true },
  provider_unavailable: {
    label: "Provider unavailable",
    detail: "The model provider is not answering. The note is safe; retry later.",
    tone: "bad",
    cue: "✕",
    busy: false,
  },
  budget_exceeded: {
    label: "Cost limit reached",
    detail: "The spend limit stopped the paid step. Typed capture and reminders still work.",
    tone: "warn",
    cue: "$",
    busy: false,
  },
  notifications_disabled: {
    label: "Notifications off",
    detail: "Reminders will appear in the in-app feed only.",
    tone: "warn",
    cue: "!",
    busy: false,
  },
  no_relevant_memory: {
    label: "No relevant memory",
    detail: "Nothing in your notes supports this yet.",
    tone: "neutral",
    cue: "·",
    busy: false,
  },
  plan_conflict: {
    label: "Plan conflict",
    detail: "The plan was built from an older version of this work.",
    tone: "warn",
    cue: "!",
    busy: false,
  },
};

/** The state an error code should move the capture flow into. */
export function flowStateForError(code: ErrorCode): FlowState {
  switch (code) {
    case ErrorCode.BUDGET_EXCEEDED:
      return "budget_exceeded";
    case ErrorCode.PROVIDER_UNAVAILABLE:
      return "provider_unavailable";
    case ErrorCode.RATE_LIMITED:
      return "retrying";
    default:
      return "failed";
  }
}

/**
 * Reminder wording. "Reminder scheduled" is reserved for a confirmed
 * scheduler synchronisation; everything earlier says so plainly.
 */
export const REMINDER_STATUS: Record<ReminderState, StatusDescriptor> = {
  [ReminderState.PENDING_SCHEDULE]: {
    label: "Scheduling reminder",
    detail: "Saved, but the scheduler has not confirmed it yet.",
    tone: "busy",
    cue: "…",
    busy: true,
  },
  [ReminderState.SCHEDULED]: {
    label: "Reminder scheduled",
    detail: "The scheduler confirmed this time.",
    tone: "good",
    cue: "✓",
    busy: false,
  },
  [ReminderState.DISPATCHING]: {
    label: "Sending reminder",
    detail: "Due now and being delivered.",
    tone: "busy",
    cue: "→",
    busy: true,
  },
  [ReminderState.DELIVERED]: {
    label: "Reminder delivered",
    detail: "It reached your in-app feed.",
    tone: "good",
    cue: "✓",
    busy: false,
  },
  [ReminderState.SCHEDULE_FAILED]: {
    label: "Schedule needs retry",
    detail: "The scheduler refused the sync. This reminder will not fire yet.",
    tone: "bad",
    cue: "✕",
    busy: false,
  },
  [ReminderState.DELIVERY_FAILED]: {
    label: "Delivery failed",
    detail: "We could not deliver it. It is not marked as read.",
    tone: "bad",
    cue: "✕",
    busy: false,
  },
  [ReminderState.SNOOZED]: {
    label: "Snoozed",
    detail: "It will come back later.",
    tone: "neutral",
    cue: "z",
    busy: false,
  },
  [ReminderState.CANCELED]: {
    label: "Canceled",
    detail: "This reminder will not fire.",
    tone: "neutral",
    cue: "×",
    busy: false,
  },
  [ReminderState.SUPERSEDED]: {
    label: "Superseded",
    detail: "A newer version of the note replaced it.",
    tone: "neutral",
    cue: "×",
    busy: false,
  },
};
