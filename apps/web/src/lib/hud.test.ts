import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe as suite, expect, it, vi } from "vitest";
import { HudView } from "../components/HudView";
import { OPEN_ERROR_COPY, OPEN_REVIEW_EVENT, parseOpenReview, ERROR_COPY, ERROR_STEPS, HUD_STATES, IDLE_SNAPSHOT, describe, parseSnapshot, tauriHudAdapter, type HudSnapshot } from "./hud";
import { parseGate, readGate } from "./recordingGate";

const snap = (over: Partial<HudSnapshot> = {}): HudSnapshot => ({ ...IDLE_SNAPSHOT, ...over });
const html = (s: HudSnapshot) => renderToStaticMarkup(createElement(HudView, { snapshot: s, onCancel: () => undefined }));

suite("parseSnapshot", () => {
  it("accepts the Rust camelCase payload, any state case", () => {
    const s = parseSnapshot({ state: "Recording", locked: true, elapsedMs: 4200, error: null, adapterMode: "local", micReady: true });
    expect(s).toMatchObject({ state: "recording", locked: true, elapsedMs: 4200, adapterMode: "local" });
  });
  it("rejects unknown states and junk, and turns unknown adapters/steps into null/unknown", () => {
    expect(parseSnapshot({ state: "exploding" })).toBeNull();
    expect(parseSnapshot("x")).toBeNull();
    expect(parseSnapshot({ state: "error", error: { step: "weird" } })?.error?.step).toBe("unknown");
    expect(parseSnapshot({ state: "idle", adapterMode: "openai" })?.adapterMode).toBeNull();
  });
});

suite("state rendering", () => {
  it("renders every state with a text label", () => {
    for (const state of HUD_STATES) {
      const out = html(snap({ state, error: state === "error" ? { step: "timeout" } : null }));
      expect(out).toContain("<main");
      expect(describe(snap({ state })).status.label.length).toBeGreaterThan(0);
    }
  });
  it("recording shows a clock, lock hint and cancel", () => {
    const out = html(snap({ state: "recording", elapsedMs: 65_000, locked: true }));
    expect(out).toContain("1:05");
    expect(out).toContain("Locked on");
    expect(out).toContain("Cancel");
  });
  it("uploaded is not transcribed", () => {
    expect(describe(snap({ state: "transcribing" })).status.detail).toMatch(/not transcribed yet/i);
  });
  it("review never says Saved and points to the main window", () => {
    for (const state of ["review", "saved"] as const) {
      const out = html(snap({ state, transcript: "call Sam", captureId: "c1" }));
      expect(out).not.toMatch(/>Saved</);
      expect(out).toContain("Nothing is saved until you confirm");
      expect(out).toContain("review and confirm");
    }
  });
  it("clarification is surfaced", () => {
    expect(describe(snap({ state: "review", needsClarification: true })).status.label).toBe("Needs your answer");
  });
  it("labels speech and extraction independently", () => {
    const v = describe(snap({ state: "review", adapterMode: "local" }));
    expect(v.speech).toMatch(/local model/);
    expect(v.extraction).toMatch(/shown in the full review/);
    expect(describe(snap({ state: "review", adapterMode: "fixture" })).speech).toMatch(/fixture/);
    expect(describe(snap({ state: "idle" })).speech).toBeNull();
  });
});

suite("error steps", () => {
  it("has distinct copy for every step", () => {
    const titles = new Set(ERROR_STEPS.map((s) => ERROR_COPY[s].title));
    expect(titles.size).toBe(ERROR_STEPS.length);
    for (const step of ERROR_STEPS) expect(html(snap({ state: "error", error: { step } }))).toContain(ERROR_COPY[step].title);
  });
  it("mic_permission is a warning, others failures", () => {
    expect(describe(snap({ state: "error", error: { step: "mic_permission" } })).status.tone).toBe("warn");
    expect(describe(snap({ state: "error", error: { step: "upload" } })).status.tone).toBe("bad");
  });
});

suite("no leakage", () => {
  it("never renders the raw error message", () => {
    const raw = {
      state: "error",
      error: { step: "upload", message: "PUT /Users/alice/Library/x failed Bearer sk-secret123 Traceback (most recent call last)" },
    };
    const out = html(parseSnapshot(raw)!);
    for (const bad of ["alice", "/Users", "sk-secret123", "Bearer", "Traceback"]) expect(out).not.toContain(bad);
  });
  it("source has no credentials or model ids", () => {
    const src = ["hud.ts", "../components/HudView.tsx", "../screens/HudScreen.tsx", "recordingGate.ts"]
      .map((f) => readFileSync(new URL(f, import.meta.url), "utf8")).join("\n");
    expect(src).not.toMatch(/AKIA|aws_|bedrock|anthropic\.|apiKey|desktop_connection|bearer/i);
  });
});

suite("reduced motion", () => {
  it("animations are disabled under prefers-reduced-motion and the HUD relies on them only", () => {
    const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
    expect(css).toMatch(/prefers-reduced-motion: reduce\)[\s\S]*animation: none !important/);
    // state is also carried by text: no animation-only signal
    expect(html(snap({ state: "recording" }))).toContain("Recording");
  });
  it("never autofocuses or sets tabindex", () => {
    expect(html(snap({ state: "recording" }))).not.toMatch(/autofocus|tabindex/i);
  });
});

suite("tauri adapter", () => {
  it("uses plugin:event listen/unlisten and delivers payloads", async () => {
    const calls: Array<[string, unknown]> = [];
    let cb: ((e: { payload: unknown }) => void) | undefined;
    const unregister = vi.fn();
    vi.stubGlobal("window", {
      __TAURI_INTERNALS__: {
        invoke: async (cmd: string, args?: unknown) => (calls.push([cmd, args]), cmd === "plugin:event|listen" ? 7 : null),
        transformCallback: (f: typeof cb) => ((cb = f), 1),
      },
      __TAURI_EVENT_PLUGIN_INTERNALS__: { unregisterListener: unregister },
    });
    const got: unknown[] = [];
    const off = await tauriHudAdapter().listen((p) => got.push(p));
    expect(calls[0]).toEqual(["plugin:event|listen", { event: "capture:state", target: { kind: "Any" }, handler: 1 }]);
    cb?.({ payload: { state: "idle" } });
    expect(got).toEqual([{ state: "idle" }]);
    off();
    expect(unregister).toHaveBeenCalledWith("capture:state", 7);
    expect(calls.at(-1)).toEqual(["plugin:event|unlisten", { event: "capture:state", eventId: 7 }]);
    vi.unstubAllGlobals();
  });
});

suite("recording gate", () => {
  it("allows only when the stack says so", () => {
    expect(parseGate({ stack: { state: "ready", recording_allowed: true, services: [] } }).allowed).toBe(true);
    expect(parseGate({ stack: { state: "starting", recording_allowed: false } })).toMatchObject({ allowed: false });
    expect(parseGate({ stack: null }).allowed).toBe(false);
    expect(parseGate(undefined).allowed).toBe(false);
  });
  it("a failing status call blocks recording", async () => {
    expect((await readGate(() => Promise.reject("boom"))).allowed).toBe(false);
  });
});

suite("open main window", () => {
  const view = (s: HudSnapshot, openError = false) =>
    renderToStaticMarkup(createElement(HudView, { snapshot: s, onCancel: () => undefined, onOpen: () => undefined, openError }));
  it("renders a button, and an alert with generic copy only on failure", () => {
    const ok = view(snap({ state: "review", captureId: "cap_1" }));
    expect(ok).toMatch(/<button[^>]*>Open Metis to review and confirm/);
    expect(ok).not.toContain("alert");
    expect(view(snap({ state: "review", captureId: "cap_1" }), true)).toContain(OPEN_ERROR_COPY);
  });
  it("invokes show_main_window with captureId only when present", async () => {
    const invoke = vi.fn(async () => null);
    vi.stubGlobal("window", { __TAURI_INTERNALS__: { invoke, transformCallback: () => 1 } });
    await tauriHudAdapter().openMain("cap_1");
    await tauriHudAdapter().openMain(null);
    expect(invoke.mock.calls).toEqual([["show_main_window", { captureId: "cap_1" }], ["show_main_window", undefined]]);
    vi.unstubAllGlobals();
  });
  it("a rejection propagates without the raw text in the copy", async () => {
    vi.stubGlobal("window", { __TAURI_INTERNALS__: { invoke: () => Promise.reject("/Users/alice boom"), transformCallback: () => 1 } });
    await expect(tauriHudAdapter().openMain("cap_1")).rejects.toBeDefined();
    expect(OPEN_ERROR_COPY).not.toContain("alice");
    vi.unstubAllGlobals();
  });
  it("validates hud:open-review payloads", () => {
    expect(OPEN_REVIEW_EVENT).toBe("hud:open-review");
    expect(parseOpenReview({ captureId: null })).toEqual({ captureId: null });
    expect(parseOpenReview({ captureId: "cap_AB-12" })).toEqual({ captureId: "cap_AB-12" });
    for (const bad of [null, "x", {}, { captureId: "a" }, { captureId: "../etc" }, { captureId: 5 }, { captureId: "a".repeat(129) }])
      expect(parseOpenReview(bad)).toBeNull();
  });
});
