import { afterEach, describe, expect, it, vi } from "vitest";
import { ErrorCode } from "../contracts/metis";
import { API_BASE, ApiError, DesktopUnavailableError, OfflineError, api, setAuthToken, setUnauthenticatedHandler } from "./api";
import { connectDesktop, desktopBase, resetDesktopForTests } from "./desktop";
import { Outbox } from "./outbox";
import { resetClientData } from "./reset";

type Call = { url: string; init: RequestInit };

function mockFetch(responder: (call: Call) => Response | Promise<Response>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const call = { url: String(url), init: init ?? {} };
    calls.push(call);
    return responder(call);
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => {
  vi.unstubAllGlobals();
  setAuthToken(null);
});

describe("api client", () => {
  it("sends a correlation id on every request and no credentials by default", async () => {
    const calls = mockFetch(() => json({ status: "ok" }));
    await api.health();
    const headers = calls[0]?.init.headers as Record<string, string>;
    expect(headers["X-Correlation-Id"]).toMatch(/^web_/);
    expect(headers["Authorization"]).toBeUndefined();
  });

  it("attaches the bearer token only once a session exists", async () => {
    const calls = mockFetch(() => json({ status: "ok" }));
    setAuthToken("token-abc");
    await api.health();
    expect((calls[0]?.init.headers as Record<string, string>)["Authorization"]).toBe("Bearer token-abc");
  });

  it("carries an Idempotency-Key on the calls that create work, and never a user_id", async () => {
    const calls = mockFetch(() => json({ capture_id: "src_1", revision: 1 }));
    await api.createCapture(
      { kind: "text" as never, text: "note", capture_timezone: "UTC", captured_at: "2026-10-05T12:00:00Z" },
      "capture:cc_1",
    );
    const call = calls[0];
    expect((call?.init.headers as Record<string, string>)["Idempotency-Key"]).toBe("capture:cc_1");
    expect(String(call?.init.body)).not.toContain("user_id");
  });

  it("turns a contract error body into an ApiError that keeps the revision", async () => {
    mockFetch(() =>
      json({ code: ErrorCode.REVISION_CONFLICT, message: "capture is at revision 4", current_revision: 4 }, 409),
    );
    const failure = await api.patchTranscript("src_1", { expected_revision: 1, text: "x" }).catch((e) => e);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).code).toBe(ErrorCode.REVISION_CONFLICT);
    expect((failure as ApiError).currentRevision).toBe(4);
    expect((failure as ApiError).retryable).toBe(false);
  });

  it("marks provider and throttle failures retryable", async () => {
    mockFetch(() => json({ code: ErrorCode.PROVIDER_UNAVAILABLE, message: "down" }, 503));
    const failure = (await api.health().catch((e) => e)) as ApiError;
    expect(failure.retryable).toBe(true);
  });

  it("reports a network failure as offline, not as a server error", async () => {
    mockFetch(() => {
      throw new TypeError("Failed to fetch");
    });
    await expect(api.today()).rejects.toBeInstanceOf(OfflineError);
  });

  it("handles a 204 with no body", async () => {
    mockFetch(() => new Response(null, { status: 204 }));
    await expect(api.markNotificationRead("not_1")).resolves.toBeUndefined();
  });

  it("uploads the recording bytes with its own content type", async () => {
    const calls = mockFetch(() => new Response(null, { status: 204 }));
    await api.uploadBytes(
      { url: "/v1/local-uploads/src_1", object_key: "k", expires_at: "", max_bytes: 100 },
      new Blob(["abc"], { type: "audio/webm" }),
    );
    expect(calls[0]?.url).toContain("/v1/local-uploads/src_1");
    expect(calls[0]?.init.method).toBe("PUT");
    expect((calls[0]?.init.headers as Record<string, string>)["Content-Type"]).toBe("audio/webm");
  });
});

describe("desktop bridge", () => {
  const BASE = "http://127.0.0.1:49152";
  const BEARER = "launch-secret-0123456789abcdef0123456789";
  const noSleep = async () => undefined;
  const asDesktop = () => vi.stubGlobal("window", { __TAURI_INTERNALS__: {} });
  const connect = (invoke: (c: string) => Promise<unknown>, maxAttempts = 5) =>
    connectDesktop({ invoke, sleep: noSleep, maxAttempts });

  afterEach(() => {
    resetDesktopForTests();
  });

  it("browser mode keeps API_BASE and VITE-style auth", async () => {
    const calls = mockFetch(() => json({}));
    setAuthToken("t");
    await api.health();
    expect(calls[0]?.url).toBe(`${API_BASE}/v1/health`);
    expect((calls[0]?.init.headers as Record<string, string>)["Authorization"]).toBe("Bearer t");
  });

  it("desktop waits through not_ready, then authenticates API calls on loopback", async () => {
    asDesktop();
    const invoke = vi
      .fn()
      .mockRejectedValueOnce("not_ready")
      .mockResolvedValue({ apiBaseUrl: BASE, bearer: BEARER });
    expect((await connect(invoke)).phase).toBe("ready");
    expect(invoke).toHaveBeenCalledTimes(2);
    setAuthToken("ignored");
    const calls = mockFetch(() => json({}));
    await api.health();
    const h = calls[0]?.init.headers as Record<string, string>;
    expect(calls[0]?.url).toBe(`${BASE}/v1/health`);
    expect(h["Authorization"]).toBe(`Bearer ${BEARER}`);
    expect(h["X-Correlation-Id"]).toMatch(/^web_/);
  });

  it("desktop upload to loopback is authenticated and correlated", async () => {
    asDesktop();
    await connect(async () => ({ apiBaseUrl: BASE, bearer: BEARER }));
    const calls = mockFetch(() => new Response(null, { status: 204 }));
    await api.uploadBytes(
      { url: "/v1/local-uploads/src_1", object_key: "k", expires_at: "", max_bytes: 9 },
      new Blob(["a"], { type: "audio/webm" }),
    );
    const h = calls[0]?.init.headers as Record<string, string>;
    expect(calls[0]?.url).toBe(`${BASE}/v1/local-uploads/src_1`);
    expect(h["Authorization"]).toBe(`Bearer ${BEARER}`);
    expect(h["X-Correlation-Id"]).toMatch(/^web_/);
  });

  it("never sends the bearer to an external presigned URL", async () => {
    asDesktop();
    await connect(async () => ({ apiBaseUrl: BASE, bearer: BEARER }));
    const calls = mockFetch(() => new Response(null, { status: 200 }));
    await api.uploadBytes(
      { url: "https://bucket.s3.example/key?sig=1", headers: { "x-amz-meta": "1" }, object_key: "k", expires_at: "", max_bytes: 9 },
      new Blob(["a"], { type: "audio/webm" }),
    );
    const h = calls[0]?.init.headers as Record<string, string>;
    expect(h["Authorization"]).toBeUndefined();
    expect(h["X-Correlation-Id"]).toBeUndefined();
    expect(JSON.stringify(calls[0])).not.toContain(BEARER);
  });

  it("missing bootstrap: no request is made and no fallback server is used", async () => {
    asDesktop();
    const calls = mockFetch(() => json({}));
    await expect(api.health()).rejects.toBeInstanceOf(DesktopUnavailableError);
    expect(calls).toHaveLength(0);
  });

  it("a failing command or exhausted retries is unavailable, without echoing the raw error", async () => {
    asDesktop();
    const failed = await connect(async () => Promise.reject(`boom ${BEARER}`));
    expect(failed.phase).toBe("unavailable");
    expect(JSON.stringify(failed)).not.toContain(BEARER);
    expect((await connect(async () => Promise.reject("not_ready"), 3)).phase).toBe("unavailable");
  });

  it.each([
    null,
    {},
    { apiBaseUrl: BASE },
    { apiBaseUrl: "https://example.com", bearer: BEARER },
    { apiBaseUrl: "http://localhost:80", bearer: BEARER },
    { apiBaseUrl: "http://127.0.0.1:99999", bearer: BEARER },
    { apiBaseUrl: "http://127.0.0.1:1234/evil", bearer: BEARER },
    { apiBaseUrl: BASE, bearer: "has space" },
    { apiBaseUrl: BASE, bearer: "" },
  ])("rejects malformed connection data %j", async (bad) => {
    asDesktop();
    const state = await connect(async () => bad);
    expect(state.phase).toBe("unavailable");
    expect(desktopBase()).toBeNull();
  });

  it("reset clears the connection, token and outbox", async () => {
    asDesktop();
    await connect(async () => ({ apiBaseUrl: BASE, bearer: BEARER }));
    const store = new Map<string, string>();
    const outbox = new Outbox({ getItem: (k) => store.get(k) ?? null, setItem: (k, v) => void store.set(k, v), removeItem: (k) => void store.delete(k) });
    outbox.add({ client_capture_id: "cc_1", text: "x", capture_timezone: "UTC", captured_at: "" });
    resetClientData(outbox);
    expect(outbox.list()).toEqual([]);
    expect(desktopBase()).toBeNull();
  });
});

describe("exports, plans and sessions", () => {
  it("creates an export with an Idempotency-Key", async () => {
    const calls = mockFetch(() => json({ job_id: "job_1", state: "queued" }, 202));
    await api.createExport({}, "export:k1");
    expect((calls[0]?.init.headers as Record<string, string>)["Idempotency-Key"]).toBe("export:k1");
  });

  it("downloads an export with the bearer in a header, never in the URL", async () => {
    const calls = mockFetch(() => new Response("zip-bytes"));
    setAuthToken("tok-secret");
    const blob = await api.downloadExport("job_1");
    expect(await blob.text()).toBe("zip-bytes");
    expect(calls[0]?.url).toBe(`${API_BASE}/v1/exports/job_1/download`);
    expect(calls[0]?.url).not.toContain("tok-secret");
    expect((calls[0]?.init.headers as Record<string, string>)["Authorization"]).toBe("Bearer tok-secret");
  });

  it("surfaces a failed download as an ApiError", async () => {
    mockFetch(() => json({ code: ErrorCode.NOT_FOUND, message: "export job_1 is not ready" }, 404));
    await expect(api.downloadExport("job_1")).rejects.toBeInstanceOf(ApiError);
  });

  it("accepts a plan with the revision and epoch it was reviewed at", async () => {
    const calls = mockFetch(() => json({ id: "plan_1" }));
    await api.acceptPlan("plan_1", { expected_revision: 2, expected_planning_epoch: 5 });
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({ expected_revision: 2, expected_planning_epoch: 5 });
  });

  it("tells the session layer when a signed-in request is refused, and not otherwise", async () => {
    const handler = vi.fn();
    setUnauthenticatedHandler(handler);
    mockFetch(() => json({ code: ErrorCode.UNAUTHENTICATED, message: "expired" }, 401));
    await api.today().catch(() => undefined);
    expect(handler).not.toHaveBeenCalled();
    setAuthToken("t");
    await api.today().catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1);
    setUnauthenticatedHandler(null);
  });
});
