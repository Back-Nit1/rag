/**
 * Cognito authorization-code + PKCE sign-in for cloud mode.
 *
 * Public client only: no secret exists in the bundle. Configuration is three
 * non-secret Vite variables. When none is set (fixture, local, desktop) there
 * is no login at all; when any is set the app requires a real session and
 * never falls back to the fixture identity.
 *
 * Tokens live in sessionStorage (cleared when the tab closes). The access
 * token is the bearer; the refresh token only ever goes to the Cognito token
 * endpoint.
 */
export type AuthConfig = { domain: string; clientId: string; redirectUri: string };

export type AuthMode =
  | { kind: "none" }
  | { kind: "misconfigured"; missing: string[] }
  | { kind: "cognito"; config: AuthConfig };

export type Session = { accessToken: string; refreshToken: string | null; expiresAt: number };

type Env = Record<string, string | undefined>;
const KEYS = ["VITE_METIS_COGNITO_DOMAIN", "VITE_METIS_COGNITO_CLIENT_ID", "VITE_METIS_COGNITO_REDIRECT_URI"] as const;

export function authMode(env: Env, desktop: boolean): AuthMode {
  if (desktop) return { kind: "none" };
  const present = KEYS.filter((k) => env[k]?.trim());
  if (present.length === 0) return { kind: "none" };
  if (present.length < KEYS.length) return { kind: "misconfigured", missing: KEYS.filter((k) => !present.includes(k)) };
  const [domain, clientId, redirectUri] = KEYS.map((k) => env[k]!.trim());
  return {
    kind: "cognito",
    config: {
      domain: (domain!.startsWith("http") ? domain! : `https://${domain}`).replace(/\/$/, ""),
      clientId: clientId!,
      redirectUri: redirectUri!,
    },
  };
}

const b64url = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
export const randomToken = () => b64url(crypto.getRandomValues(new Uint8Array(32)));
export async function pkceChallenge(verifier: string): Promise<string> {
  return b64url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
}

export function authorizeUrl(c: AuthConfig, state: string, challenge: string): string {
  const q = new URLSearchParams({
    response_type: "code",
    client_id: c.clientId,
    redirect_uri: c.redirectUri,
    scope: "openid email",
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
  });
  return `${c.domain}/oauth2/authorize?${q}`;
}

export class AuthError extends Error {}

async function tokenRequest(c: AuthConfig, form: Record<string, string>, previousRefresh: string | null): Promise<Session> {
  let response: Response;
  try {
    response = await fetch(`${c.domain}/oauth2/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ client_id: c.clientId, ...form }),
    });
  } catch {
    throw new AuthError("The sign-in service could not be reached.");
  }
  if (!response.ok) throw new AuthError("Sign-in was refused or the session expired.");
  const t = (await response.json()) as { access_token?: string; refresh_token?: string; expires_in?: number };
  if (!t.access_token || !t.expires_in) throw new AuthError("The sign-in service returned an unusable response.");
  // Cognito does not return a new refresh token on refresh: keep the old one.
  return { accessToken: t.access_token, refreshToken: t.refresh_token ?? previousRefresh, expiresAt: Date.now() + t.expires_in * 1000 };
}

export const refreshSession = (c: AuthConfig, s: Session) => {
  if (!s.refreshToken) throw new AuthError("The session expired.");
  return tokenRequest(c, { grant_type: "refresh_token", refresh_token: s.refreshToken }, s.refreshToken);
};

/** True when the token is expired or within `skewMs` of it. */
export const needsRefresh = (s: Session, now = Date.now(), skewMs = 60_000) => s.expiresAt - now <= skewMs;

// ----- browser glue -------------------------------------------------------- //
const SESSION_KEY = "metis.session";
const FLOW_KEY = "metis.pkce";

export function loadSession(store: Storage = sessionStorage): Session | null {
  try {
    const raw = store.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}
export function saveSession(s: Session | null, store: Storage = sessionStorage): void {
  try {
    if (s) store.setItem(SESSION_KEY, JSON.stringify(s));
    else store.removeItem(SESSION_KEY);
  } catch {
    /* private mode: the session simply does not survive a reload */
  }
}

export async function startSignIn(c: AuthConfig, store: Storage = sessionStorage): Promise<void> {
  const verifier = randomToken();
  const state = randomToken();
  store.setItem(FLOW_KEY, JSON.stringify({ verifier, state }));
  window.location.assign(authorizeUrl(c, state, await pkceChallenge(verifier)));
}

/**
 * If this URL is the redirect back from Cognito, finish the flow. Returns null
 * when it is not a callback; throws AuthError when it is one that failed.
 */
export async function completeSignIn(c: AuthConfig, href: string, store: Storage = sessionStorage): Promise<Session | null> {
  const url = new URL(href);
  const redirect = new URL(c.redirectUri);
  if (url.origin !== redirect.origin || url.pathname !== redirect.pathname) return null;
  const code = url.searchParams.get("code");
  const error = url.searchParams.get("error");
  if (!code && !error) return null;
  const flow = JSON.parse(store.getItem(FLOW_KEY) ?? "null") as { verifier: string; state: string } | null;
  store.removeItem(FLOW_KEY);
  if (error) throw new AuthError(`Sign-in failed: ${url.searchParams.get("error_description") ?? error}.`);
  if (!flow || url.searchParams.get("state") !== flow.state) throw new AuthError("Sign-in could not be verified (state mismatch). Start again.");
  return tokenRequest(
    c,
    { grant_type: "authorization_code", code: code!, redirect_uri: c.redirectUri, code_verifier: flow.verifier },
    null,
  );
}

export function signOutUrl(c: AuthConfig): string {
  return `${c.domain}/logout?${new URLSearchParams({ client_id: c.clientId, logout_uri: c.redirectUri })}`;
}
