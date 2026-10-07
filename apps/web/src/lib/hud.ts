/**
 * Desktop HUD: view model + Tauri adapter.
 *
 * The HUD window (label `hud`) may only call `capture_state`, `capture_cancel`
 * and listen/unlisten (see apps/desktop capabilities/hud-window.json). It has
 * no API connection, so it can never prove a proposal was resolved: it never
 * shows "Saved". Durable review happens in the main window.
 *
 * Rust payload (camelCase, `state` is snake_case lowercase on the wire; we
 * accept any case). Raw error `message` text is never rendered: the copy below
 * is ours, so paths, usernames, tokens and traces cannot leak through it.
 */
import { FLOW_STATUS, type StatusDescriptor } from "./status";

export const HUD_EVENT = "capture:state";

export const HUD_STATES = [
  "idle", "armed", "recording", "transcribing", "extracting", "review", "saved", "error",
] as const;
export type HudState = (typeof HUD_STATES)[number];

export const ERROR_STEPS = [
  "shortcut", "not_ready", "startup_failed", "mic_permission", "audio_device", "audio",
  "create", "upload", "complete", "poll", "processing", "timeout",
] as const;
export type ErrorStep = (typeof ERROR_STEPS)[number];

export type HudSnapshot = {
  state: HudState;
  locked: boolean;
  elapsedMs: number;
  error: { step: ErrorStep | "unknown" } | null;
  adapterMode: "fixture" | "local" | "cloud" | null;
  transcript: string | null;
  captureId: string | null;
  needsClarification: boolean;
  truncated: boolean;
  micReady: boolean;
  releaseToTranscriptMs: number | null;
  extractionMs: number | null;
};

export const IDLE_SNAPSHOT: HudSnapshot = {
  state: "idle", locked: false, elapsedMs: 0, error: null, adapterMode: null, transcript: null,
  captureId: null, needsClarification: false, truncated: false, micReady: false,
  releaseToTranscriptMs: null, extractionMs: null,
};

const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : 0);
const optNum = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null);

/** Defensive parse: unknown shapes become null, never a thrown error or a guess. */
export function parseSnapshot(raw: unknown): HudSnapshot | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  const state = typeof r.state === "string" ? (r.state.toLowerCase() as HudState) : null;
  if (!state || !(HUD_STATES as readonly string[]).includes(state)) return null;
  const err = r.error as Record<string, unknown> | null | undefined;
  const step = err && typeof err === "object" && typeof err.step === "string" ? err.step : null;
  const adapter = r.adapterMode;
  return {
    state,
    locked: r.locked === true,
    elapsedMs: num(r.elapsedMs),
    error: step
      ? { step: (ERROR_STEPS as readonly string[]).includes(step) ? (step as ErrorStep) : "unknown" }
      : state === "error" ? { step: "unknown" } : null,
    adapterMode: adapter === "fixture" || adapter === "local" || adapter === "cloud" ? adapter : null,
    transcript: typeof r.transcript === "string" && r.transcript ? r.transcript.slice(0, 4000) : null,
    captureId: typeof r.captureId === "string" ? r.captureId : null,
    needsClarification: r.needsClarification === true,
    truncated: r.truncated === true,
    micReady: r.micReady === true,
    releaseToTranscriptMs: optNum(r.releaseToTranscriptMs),
    extractionMs: optNum(r.extractionMs),
  };
}

export type ErrorCopy = { title: string; detail: string; retryable: boolean };

/** Our own copy per failing step. Nothing from the Rust message is shown. */
export const ERROR_COPY: Record<ErrorStep | "unknown", ErrorCopy> = {
  shortcut: { title: "Hotkey unavailable", detail: "Another app may own the shortcut. Capture from the main window instead.", retryable: false },
  not_ready: { title: "Still starting", detail: "Local speech or the API is not ready yet. Nothing was recorded.", retryable: true },
  startup_failed: { title: "Local services failed to start", detail: "Open Metis to see which service stopped. Nothing was recorded.", retryable: false },
  mic_permission: { title: "Microphone blocked", detail: "Allow microphone access for Metis in system settings, then enable it from the main window.", retryable: false },
  audio_device: { title: "No usable microphone", detail: "Check the input device and try again. Nothing was recorded.", retryable: true },
  audio: { title: "Audio stopped", detail: "The microphone stream ended unexpectedly. Nothing was saved.", retryable: true },
  create: { title: "Could not start the note", detail: "The local API refused the request. Nothing was uploaded.", retryable: true },
  upload: { title: "Upload failed", detail: "The audio was not sent. Nothing was transcribed.", retryable: true },
  complete: { title: "Upload not confirmed", detail: "The server did not confirm the audio. It is not transcribed.", retryable: true },
  poll: { title: "Lost contact with the API", detail: "Progress cannot be read. Check the main window for the note.", retryable: false },
  processing: { title: "Processing failed", detail: "Transcription or extraction did not finish. Open Metis to retry.", retryable: false },
  timeout: { title: "Timed out", detail: "Processing took too long. The note may still finish; check the main window.", retryable: false },
  unknown: { title: "Something went wrong", detail: "Open Metis for details. Nothing was saved from here.", retryable: false },
};

export type HudView = {
  status: StatusDescriptor;
  /** Short line under the status; copy, not raw error text. */
  hint: string | null;
  /** Independent provenance labels. */
  speech: string | null;
  extraction: string | null;
  showCancel: boolean;
  showOpenReview: boolean;
  clock: string | null;
  transcript: string | null;
  error: ErrorCopy | null;
};

export function formatClock(ms: number): string {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Speech adapter label. adapterMode is the ASR adapter as the API reports it. */
export function speechLabel(mode: HudSnapshot["adapterMode"]): string | null {
  if (mode === "fixture") return "Speech: fixture (canned text, not your voice)";
  if (mode === "local") return "Speech: local model on this Mac";
  if (mode === "cloud") return "Speech: cloud";
  return null;
}

export function describe(s: HudSnapshot): HudView {
  const speech = speechLabel(s.adapterMode);
  // The extraction adapter is not part of the desktop payload. We say so rather
  // than infer it from the speech adapter.
  const extractionKnown = s.state === "review" || s.state === "saved" || s.state === "extracting";
  const extraction = extractionKnown ? "Extraction: mode shown in the full review" : null;
  const base: HudView = {
    status: FLOW_STATUS.idle, hint: null, speech, extraction, showCancel: false, showOpenReview: false,
    clock: null, transcript: null, error: null,
  };
  switch (s.state) {
    case "idle":
      return { ...base, status: { ...FLOW_STATUS.idle, detail: s.micReady ? "Hold the hotkey to talk." : "Hold the hotkey to talk. The first use asks for the microphone." }, speech: null };
    case "armed":
      return { ...base, status: { label: "Starting", detail: "Opening the microphone.", tone: "busy", cue: "…", busy: true }, showCancel: true, speech: null };
    case "recording":
      return {
        ...base,
        status: { ...FLOW_STATUS.recording, detail: s.locked ? "Locked on. Press the hotkey again to stop." : "Release the hotkey to stop." },
        clock: formatClock(s.elapsedMs), showCancel: true, speech: null,
      };
    case "transcribing":
      return { ...base, status: { ...FLOW_STATUS.transcribing, detail: "Uploaded. Not transcribed yet." }, showCancel: true, speech: null };
    case "extracting":
      return { ...base, status: FLOW_STATUS.extraction_pending, transcript: s.transcript, showCancel: true };
    case "saved": // The desktop never emits this and the HUD cannot verify it.
    case "review":
      return {
        ...base,
        status: s.needsClarification ? FLOW_STATUS.clarification_required : {
          label: "Ready to review",
          detail: "Nothing is saved until you confirm in Metis.",
          tone: "good", cue: "✓", busy: false,
        },
        hint: s.truncated ? "The transcript is shortened here; the full text is in Metis." : null,
        transcript: s.transcript, showOpenReview: true,
      };
    case "error": {
      const error = ERROR_COPY[s.error?.step ?? "unknown"];
      return {
        ...base,
        status: { label: error.title, detail: error.detail, tone: s.error?.step === "mic_permission" ? "warn" : "bad", cue: s.error?.step === "mic_permission" ? "!" : "✕", busy: false },
        error, speech: null, showOpenReview: s.captureId !== null,
      };
    }
  }
}

// ---- Tauri adapter ------------------------------------------------------ //

export type HudAdapter = {
  getSnapshot(): Promise<unknown>;
  cancel(): Promise<void>;
  /** Resolves to an unlisten function. */
  listen(onSnapshot: (raw: unknown) => void): Promise<() => void>;
};

type Internals = {
  invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
  transformCallback: (cb: (e: { payload: unknown }) => void, once?: boolean) => number;
};

/**
 * Same wire calls as `@tauri-apps/api/event` in Tauri 2.12 (verified in
 * tauri-2.12.1/scripts/bundle.global.js): `plugin:event|listen` with
 * {event, target:{kind:"Any"}, handler}; the returned id is released with
 * `plugin:event|unlisten` and the page-side listener table.
 */
export function tauriHudAdapter(): HudAdapter {
  const t = () => (window as unknown as { __TAURI_INTERNALS__: Internals }).__TAURI_INTERNALS__;
  return {
    getSnapshot: () => t().invoke("capture_state"),
    cancel: async () => void (await t().invoke("capture_cancel")),
    async listen(onSnapshot) {
      const internals = t();
      const eventId = await internals.invoke("plugin:event|listen", {
        event: HUD_EVENT,
        target: { kind: "Any" },
        handler: internals.transformCallback((e) => onSnapshot(e.payload)),
      });
      return () => {
        (window as unknown as { __TAURI_EVENT_PLUGIN_INTERNALS__?: { unregisterListener?: (e: string, id: unknown) => void } })
          .__TAURI_EVENT_PLUGIN_INTERNALS__?.unregisterListener?.(HUD_EVENT, eventId);
        void internals.invoke("plugin:event|unlisten", { event: HUD_EVENT, eventId }).catch(() => undefined);
      };
    },
  };
}
