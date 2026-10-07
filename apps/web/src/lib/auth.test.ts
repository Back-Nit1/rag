import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthError, authMode, authorizeUrl, completeSignIn, needsRefresh, pkceChallenge, refreshSession, signOutUrl } from "./auth";

const full = {
  VITE_METIS_COGNITO_DOMAIN: "auth.example.com",
  VITE_METIS_COGNITO_CLIENT_ID: "cid",
  VITE_METIS_COGNITO_REDIRECT_URI: "https://app.example.com/callback",
};
const cfg = (authMode(full, false) as { kind: "cognito"; config: never }).config as {
  domain: string; clientId: string; redirectUri: string;
};

const store = (init: Record<string, string> = {}) => {
  const m = new Map(Object.entries(init));
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) } as unknown as Storage;
};

afterEach(() => vi.unstubAllGlobals());

describe("auth mode", () => {
  it("is none without config and in desktop, so fixture/local never see a login", () => {
    expect(authMode({}, false).kind).toBe("none");
    expect(authMode(full, true).kind).toBe("none");
  });
  it("is misconfigured, not fixture, when only part of the config exists", () => {
    const m = authMode({ VITE_METIS_COGNITO_DOMAIN: "x" }, false);
    expect(m).toEqual({ kind: "misconfigured", missing: ["VITE_METIS_COGNITO_CLIENT_ID", "VITE_METIS_COGNITO_REDIRECT_URI"] });
  });
  it("normalises the domain", () => expect(cfg.domain).toBe("https://auth.example.com"));
});

describe("pkce", () => {
  it("matches the RFC 7636 example challenge", async () => {
    expect(await pkceChallenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")).toBe("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
  });
  it("sends S256 and no secret", () => {
    const u = new URL(authorizeUrl(cfg, "s", "c"));
    expect(u.searchParams.get("code_challenge_method")).toBe("S256");
    expect(u.search).not.toContain("secret");
  });
});

describe("callback", () => {
  const cb = (q: string) => `https://app.example.com/callback?${q}`;
  const flow = JSON.stringify({ verifier: "v", state: "st" });

  it("ignores URLs that are not the callback", async () => {
    expect(await completeSignIn(cfg, "https://app.example.com/?code=x", store())).toBeNull();
  });
  it("rejects a state mismatch without calling the token endpoint", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    await expect(completeSignIn(cfg, cb("code=c&state=evil"), store({ "metis.pkce": flow }))).rejects.toBeInstanceOf(AuthError);
    expect(f).not.toHaveBeenCalled();
  });
  it("exchanges the code with the verifier", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ access_token: "a", refresh_token: "r", expires_in: 3600 })));
    vi.stubGlobal("fetch", f);
    const s = await completeSignIn(cfg, cb("code=c&state=st"), store({ "metis.pkce": flow }));
    expect(s?.accessToken).toBe("a");
    const body = String((f.mock.calls[0] as unknown as [string, RequestInit])[1].body);
    expect(body).toContain("code_verifier=v");
  });
  it("surfaces a provider error", async () => {
    await expect(completeSignIn(cfg, cb("error=access_denied"), store())).rejects.toThrow(/access_denied/);
  });
});

describe("refresh", () => {
  it("keeps the old refresh token and fails closed on refusal", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ access_token: "b", expires_in: 60 }))));
    const s = await refreshSession(cfg, { accessToken: "a", refreshToken: "r", expiresAt: 0 });
    expect(s.refreshToken).toBe("r");
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 400 })));
    await expect(refreshSession(cfg, s)).rejects.toBeInstanceOf(AuthError);
  });
  it("flags tokens near expiry", () => {
    expect(needsRefresh({ accessToken: "a", refreshToken: null, expiresAt: 1000 }, 950)).toBe(true);
    expect(needsRefresh({ accessToken: "a", refreshToken: null, expiresAt: 1_000_000 }, 0)).toBe(false);
  });
  it("builds a sign-out url", () => expect(signOutUrl(cfg)).toContain("/logout?client_id=cid"));
});
