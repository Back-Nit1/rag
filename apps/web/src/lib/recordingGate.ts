/**
 * Desktop recording gate: the full app's record affordance follows the
 * desktop shell's `desktop_status` (stack.recording_allowed, true only while
 * every service including the speech model is ready). Browser mode is never gated.
 *
 * Wire shape (serde defaults, snake_case): { stack: { state, recording_allowed, services } | null, error }.
 * camelCase is accepted too. Anything unreadable means "not allowed".
 */
import { useEffect, useState } from "react";
import { isDesktop } from "./desktop";

export type RecordingGate = { allowed: boolean; reason: string | null };

export function parseGate(raw: unknown): RecordingGate {
  const stack = (raw as { stack?: Record<string, unknown> | null } | null)?.stack;
  if (!stack || typeof stack !== "object") {
    return { allowed: false, reason: "The local services have not started." };
  }
  const allowed = (stack.recording_allowed ?? stack.recordingAllowed) === true;
  if (allowed) return { allowed, reason: null };
  return {
    allowed,
    reason:
      stack.state === "starting"
        ? "Local speech is still loading. Typed notes work now."
        : "Voice capture is off until every local service is ready. Typed notes still work.",
  };
}

type Call = (cmd: string) => Promise<unknown>;
const tauri: Call = (cmd) =>
  (window as unknown as { __TAURI_INTERNALS__: { invoke: Call } }).__TAURI_INTERNALS__.invoke(cmd);

export async function readGate(invoke: Call = tauri): Promise<RecordingGate> {
  try {
    return parseGate(await invoke("desktop_status"));
  } catch {
    return { allowed: false, reason: "The desktop status could not be read." };
  }
}

/** Asks Rust to run the microphone permission prompt (main window only). */
export const prepareMic = (invoke: Call = tauri): Promise<unknown> => invoke("capture_prepare_mic").catch(() => undefined);

export function useRecordingGate(pollMs = 3000): RecordingGate {
  const [gate, setGate] = useState<RecordingGate>(() => (isDesktop() ? { allowed: false, reason: null } : { allowed: true, reason: null }));
  useEffect(() => {
    if (!isDesktop()) return;
    let live = true;
    const tick = () => void readGate().then((g) => live && setGate(g));
    tick();
    const id = setInterval(tick, pollMs);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [pollMs]);
  return gate;
}
