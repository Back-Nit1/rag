import { setAuthToken } from "./api";
import { clearDesktopConnection } from "./desktop";
import { Outbox } from "./outbox";

/**
 * Sign-out / profile reset: drops in-memory credentials and the device-local
 * unsent-note queue. Server data is untouched; unsent notes are lost by design,
 * so callers must confirm with the user first.
 */
export function resetClientData(outbox: Outbox = new Outbox()): void {
  setAuthToken(null);
  clearDesktopConnection();
  outbox.clear();
}
