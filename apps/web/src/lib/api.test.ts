import { afterEach, describe, expect, it, vi } from "vitest";
import { ErrorCode } from "../contracts/metis";
import { ApiError, OfflineError, api, setAuthToken } from "./api";

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
