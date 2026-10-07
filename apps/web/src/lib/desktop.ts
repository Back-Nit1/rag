/**
 * Desktop (Tauri) runtime connection.
 *
 * The desktop shell starts local sidecars on an ephemeral loopback port and
 * mints a per-launch bearer. Both reach the webview only through the Tauri
 * command `desktop_connection`, and live only in this module's memory: never
 * Vite env, URL, storage, logs or error text. Browser/PWA mode never touches
 * any of this.
 *
 * Command contract (implemented by the desktop owner):
 *   invoke("desktop_connection") -> { apiBaseUrl: "http://127.0.0.1:<port>", bearer: string }
 *   rejects with the string "not_ready" while sidecars start, or any other
 *   string on failure.
 */

export type DesktopPhase = "browser" | "starting" | "ready" | "unavailable";
export type DesktopState = { phase: DesktopPhase; reason: string | null };
export type Invoke = (command: string) => Promise<unknown>;

type Connection = { base: string; bearer: string };

const LOOPBACK = /^http:\/\/127\.0\.0\.1:(\d{1,5})\/?$/;
// Header-safe: a bearer with whitespace or control characters is malformed.
const BEARER = /^[\x21-\x7e]{1,4096}$/;

let connection: Connection | null = null;
let state: DesktopState = { phase: isDesktop() ? "starting" : "browser", reason: null };
const listeners = new Set<() => void>();

export function isDesktop(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

function setState(next: DesktopState): void {
  state = next;
  listeners.forEach((l) => l());
}

export const subscribeDesktop = (l: () => void) => (listeners.add(l), () => void listeners.delete(l));
export const getDesktopState = (): DesktopState => state;

/** Loopback API base while connected, else null. Never falls back to anything. */
export function desktopBase(): string | null {
  return connection?.base ?? null;
}

/** Authorization header for the loopback API only; `url` must be on its origin. */
export function desktopAuthFor(url: string): Record<string, string> {
  if (!connection) return {};
  try {
    return new URL(url).origin === new URL(connection.base).origin
      ? { Authorization: `Bearer ${connection.bearer}` }
      : {};
  } catch {
    return {};
  }
}

export function parseConnection(raw: unknown): Connection | null {
  if (typeof raw !== "object" || raw === null) return null;
  const { apiBaseUrl, bearer } = raw as Record<string, unknown>;
  if (typeof apiBaseUrl !== "string" || typeof bearer !== "string") return null;
  const match = LOOPBACK.exec(apiBaseUrl);
  if (!match || Number(match[1]) < 1 || Number(match[1]) > 65535) return null;
  if (!BEARER.test(bearer)) return null;
  return { base: apiBaseUrl.replace(/\/$/, ""), bearer };
}

const tauriInvoke: Invoke = (command) =>
  (
    window as unknown as { __TAURI_INTERNALS__: { invoke: Invoke } }
  ).__TAURI_INTERNALS__.invoke(command);

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Waits for the sidecars with bounded backoff. Safe to call again to retry. */
export async function connectDesktop(
  options: { invoke?: Invoke; sleep?: (ms: number) => Promise<void>; maxAttempts?: number } = {},
): Promise<DesktopState> {
  if (!isDesktop() && !options.invoke) return state;
  const invoke = options.invoke ?? tauriInvoke;
  const wait = options.sleep ?? sleep;
  const maxAttempts = options.maxAttempts ?? 40;
  connection = null;
  setState({ phase: "starting", reason: null });
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      const parsed = parseConnection(await invoke("desktop_connection"));
      if (!parsed) {
        setState({ phase: "unavailable", reason: "The desktop runtime returned invalid connection data." });
        return state;
      }
      connection = parsed;
      setState({ phase: "ready", reason: null });
      return state;
    } catch (error) {
      if (error !== "not_ready") {
        // The raw error is deliberately not shown: it is not ours to trust.
        setState({ phase: "unavailable", reason: "The local services failed to start." });
        return state;
      }
    }
    await wait(Math.min(250 * 1.5 ** attempt, 2000));
  }
  setState({ phase: "unavailable", reason: "The local services did not become ready in time." });
  return state;
}

/** Forget the runtime connection (sign-out / profile reset). */
export function clearDesktopConnection(): void {
  connection = null;
  if (isDesktop()) setState({ phase: "starting", reason: null });
}

/** Test helper. */
export function resetDesktopForTests(): void {
  connection = null;
  state = { phase: isDesktop() ? "starting" : "browser", reason: null };
}
