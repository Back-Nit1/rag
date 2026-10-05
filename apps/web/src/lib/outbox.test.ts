import { describe, expect, it } from "vitest";
import { MAX_ENTRIES, Outbox, OutboxFullError, type OutboxStorage } from "./outbox";

function store(initial?: string): OutboxStorage {
  const map = new Map<string, string>();
  if (initial !== undefined) map.set("metis.outbox.v1", initial);
  return {
    getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => void map.set(k, v),
    removeItem: (k) => void map.delete(k),
  };
}

const entry = (id: string) => ({
  client_capture_id: id,
  text: `note ${id}`,
  capture_timezone: "America/New_York",
  captured_at: "2026-10-05T12:00:00Z",
});

describe("Outbox", () => {
  it("keeps one entry per client capture id so a retry cannot duplicate a note", () => {
    const outbox = new Outbox(store());
    outbox.add(entry("cc_1"));
    outbox.add(entry("cc_1"));
    expect(outbox.list()).toHaveLength(1);
  });

  it("refuses to grow without bound instead of silently dropping input", () => {
    const outbox = new Outbox(store());
    for (let i = 0; i < MAX_ENTRIES; i += 1) outbox.add(entry(`cc_${i}`));
    expect(() => outbox.add(entry("cc_overflow"))).toThrow(OutboxFullError);
    expect(outbox.list()).toHaveLength(MAX_ENTRIES);
  });

  it("reports nothing queued when the stored payload is corrupt", () => {
    expect(new Outbox(store("not json")).list()).toEqual([]);
  });

  it("removes an entry only once the send succeeded", async () => {
    const outbox = new Outbox(store());
    outbox.add(entry("cc_ok"));
    outbox.add(entry("cc_bad"));
    const result = await outbox.flush(async (e) => {
      if (e.client_capture_id === "cc_bad") throw new Error("network down");
    });
    expect(result).toEqual({ sent: 1, kept: 1 });
    const kept = outbox.list();
    expect(kept[0]?.client_capture_id).toBe("cc_bad");
    expect(kept[0]?.attempts).toBe(1);
    expect(kept[0]?.last_error).toBe("network down");
  });
});
