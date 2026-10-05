/**
 * Offline outbox for typed captures.
 *
 * Stable client ids are the whole point: the server treats
 * `client_capture_id` as idempotent, so a retried flush can never create a
 * second task from one note. Storage is bounded and eviction by the browser is
 * possible, so the UI shows "held on this device", never "saved".
 *
 * Audio is deliberately not queued here: a multi-megabyte Blob does not belong
 * in localStorage. An audio capture that cannot upload now reports the failure
 * and keeps the recording in memory for an explicit retry.
 */
export type OutboxEntry = {
  client_capture_id: string;
  text: string;
  capture_timezone: string;
  captured_at: string;
  attempts: number;
  last_error?: string;
};

const KEY = "metis.outbox.v1";
export const MAX_ENTRIES = 50;
export const MAX_TEXT_CHARS = 20_000;

export class OutboxFullError extends Error {
  constructor() {
    super(`the offline outbox holds ${MAX_ENTRIES} unsent notes; sync or delete one first`);
    this.name = "OutboxFullError";
  }
}

/** Minimal slice of the Storage interface, so this is testable without a DOM. */
export type OutboxStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function memoryStorage(): OutboxStorage {
  const map = new Map<string, string>();
  return {
    getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => void map.set(k, v),
    removeItem: (k) => void map.delete(k),
  };
}

export class Outbox {
  constructor(private readonly storage: OutboxStorage = safeLocalStorage()) {}

  list(): OutboxEntry[] {
    const raw = this.storage.getItem(KEY);
    if (!raw) return [];
    try {
      const parsed: unknown = JSON.parse(raw);
      return Array.isArray(parsed) ? (parsed as OutboxEntry[]) : [];
    } catch {
      // Corrupt payload: report nothing queued rather than crash the app.
      return [];
    }
  }

  /** Adds a note, or returns the existing entry for a repeated client id. */
  add(entry: Omit<OutboxEntry, "attempts">): OutboxEntry {
    const entries = this.list();
    const existing = entries.find((e) => e.client_capture_id === entry.client_capture_id);
    if (existing) return existing;
    if (entries.length >= MAX_ENTRIES) throw new OutboxFullError();
    const queued: OutboxEntry = { ...entry, text: entry.text.slice(0, MAX_TEXT_CHARS), attempts: 0 };
    this.write([...entries, queued]);
    return queued;
  }

  remove(clientCaptureId: string): void {
    this.write(this.list().filter((e) => e.client_capture_id !== clientCaptureId));
  }

  markAttempt(clientCaptureId: string, error: string): void {
    this.write(
      this.list().map((e) =>
        e.client_capture_id === clientCaptureId ? { ...e, attempts: e.attempts + 1, last_error: error } : e,
      ),
    );
  }

  /**
   * Sends every queued note. An entry is removed only when the server accepted
   * it; anything else stays queued with the error recorded.
   */
  async flush(send: (entry: OutboxEntry) => Promise<void>): Promise<{ sent: number; kept: number }> {
    let sent = 0;
    for (const entry of this.list()) {
      try {
        await send(entry);
        this.remove(entry.client_capture_id);
        sent += 1;
      } catch (error) {
        this.markAttempt(entry.client_capture_id, error instanceof Error ? error.message : String(error));
      }
    }
    return { sent, kept: this.list().length };
  }

  private write(entries: OutboxEntry[]): void {
    try {
      this.storage.setItem(KEY, JSON.stringify(entries));
    } catch {
      // Quota or a private window: the caller's state still holds the note, so
      // the UI keeps showing it as unsent instead of claiming durability.
    }
  }
}

function safeLocalStorage(): OutboxStorage {
  try {
    const probe = "metis.probe";
    globalThis.localStorage.setItem(probe, "1");
    globalThis.localStorage.removeItem(probe);
    return globalThis.localStorage;
  } catch {
    return memoryStorage();
  }
}

export function newClientCaptureId(): string {
  return `cc_${crypto.randomUUID()}`;
}
