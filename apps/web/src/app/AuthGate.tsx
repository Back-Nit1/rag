/**
 * Cloud sign-in gate. Renders nothing that talks to the API until a real
 * session exists. With no Cognito config (fixture, local, desktop) it is a
 * pass-through and no login is ever shown.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Button } from "../components/Button";
import {
  AuthError,
  authMode,
  completeSignIn,
  loadSession,
  needsRefresh,
  refreshSession,
  saveSession,
  signOutUrl,
  startSignIn,
  type AuthConfig,
  type Session,
} from "../lib/auth";
import { setAuthToken, setUnauthenticatedHandler } from "../lib/api";
import { isDesktop } from "../lib/desktop";
import { resetClientData } from "../lib/reset";

type AuthValue = { signedIn: boolean; signOut: (() => void) | null };
const AuthContext = createContext<AuthValue>({ signedIn: false, signOut: null });
export const useAuth = () => useContext(AuthContext);

// The authorization code is single use; StrictMode runs effects twice in dev.
let callback: Promise<Session | null> | null = null;

function Panel({ children }: { children: ReactNode }) {
  return (
    <main className="grid min-h-dvh place-items-center bg-canvas p-6 text-ink">
      <div className="max-w-sm text-center">
        <p className="font-serif text-xl font-semibold">Metis</p>
        {children}
      </div>
    </main>
  );
}

function CognitoGate({ config, children }: { config: AuthConfig; children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(true);
  const [message, setMessage] = useState<string | null>(null);

  const drop = useCallback((why: string | null) => {
    saveSession(null);
    setAuthToken(null);
    setSession(null);
    setMessage(why);
  }, []);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        callback ??= completeSignIn(config, window.location.href);
        let next = await callback;
        if (next) window.history.replaceState(null, "", `${window.location.pathname.replace(/\/callback$/, "/")}#/today`);
        next ??= loadSession();
        if (next && needsRefresh(next)) next = await refreshSession(config, next);
        if (next) (saveSession(next), setAuthToken(next.accessToken)); // before children mount
        if (live) setSession(next);
      } catch (cause) {
        if (live) drop(cause instanceof AuthError ? cause.message : "Sign-in failed.");
      } finally {
        if (live) setChecking(false);
      }
    })();
    return () => {
      live = false;
    };
  }, [config, drop]);

  // Keep the bearer current, and fail closed when it cannot be renewed.
  useEffect(() => {
    if (!session) return;
    setUnauthenticatedHandler(() => drop("Your session ended. Sign in again."));
    const timer = window.setTimeout(
      () =>
        refreshSession(config, session).then(
          (next) => (saveSession(next), setAuthToken(next.accessToken), setSession(next)),
          () => drop("Your session expired. Sign in again."),
        ),
      Math.max(1000, session.expiresAt - Date.now() - 60_000),
    );
    return () => {
      window.clearTimeout(timer);
      setUnauthenticatedHandler(null);
    };
  }, [session, config, drop]);

  const signOut = useCallback(() => {
    resetClientData();
    saveSession(null);
    window.location.assign(signOutUrl(config));
  }, [config]);

  const value = useMemo(() => ({ signedIn: !!session, signOut }), [session, signOut]);

  if (checking) return <Panel><p role="status" className="mt-2 text-sm text-muted">Checking your session…</p></Panel>;
  if (!session)
    return (
      <Panel>
        {message && (
          <p role="alert" className="mt-2 text-sm text-bad">
            {message}
          </p>
        )}
        <p className="mt-2 text-sm text-muted">Sign in to open your notebook.</p>
        <Button variant="primary" className="mt-4" onClick={() => void startSignIn(config)}>
          Sign in
        </Button>
      </Panel>
    );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

const KEY_STORAGE = "metis.accessKey";
const readKey = (): string | null => {
  try {
    return window.sessionStorage.getItem(KEY_STORAGE);
  } catch {
    return null; // storage can be blocked; the key then lives only in memory
  }
};
const writeKey = (value: string | null): void => {
  try {
    if (value) window.sessionStorage.setItem(KEY_STORAGE, value);
    else window.sessionStorage.removeItem(KEY_STORAGE);
  } catch {
    /* in-memory only */
  }
};

/**
 * Single-user deployment gate: the API is public HTTPS and only accepts the
 * access key the operator set on the server. The key stays in this tab's
 * sessionStorage and is sent as the bearer; the server decides, so a wrong key
 * simply fails closed on the first request.
 */
function AccessKeyGate({ children }: { children: ReactNode }) {
  const [key, setKey] = useState<string | null>(() => {
    const stored = readKey();
    setAuthToken(stored); // before children mount, so their first request carries it
    return stored;
  });
  const [draft, setDraft] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  const drop = useCallback((why: string | null) => {
    writeKey(null);
    setAuthToken(null);
    setKey(null);
    setMessage(why);
  }, []);

  useEffect(() => {
    if (!key) return;
    setUnauthenticatedHandler(() => drop("That access key was not accepted."));
    return () => setUnauthenticatedHandler(null);
  }, [key, drop]);

  const signOut = useCallback(() => {
    resetClientData();
    drop(null);
  }, [drop]);
  const value = useMemo(() => ({ signedIn: !!key, signOut }), [key, signOut]);

  if (!key)
    return (
      <Panel>
        {message && (
          <p role="alert" className="mt-2 text-sm text-bad">
            {message}
          </p>
        )}
        <form
          className="mt-2"
          onSubmit={(e) => {
            e.preventDefault();
            const next = draft.trim();
            if (!next) return;
            writeKey(next);
            setAuthToken(next);
            setMessage(null);
            setDraft("");
            setKey(next);
          }}
        >
          <label className="block text-sm text-muted" htmlFor="access-key">
            Enter your access key to open your notebook.
          </label>
          <input
            id="access-key"
            type="password"
            autoComplete="current-password"
            className="mt-3 w-full rounded-md border border-line bg-surface px-3 py-2 text-sm"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <Button variant="primary" className="mt-4" type="submit">
            Open
          </Button>
        </form>
      </Panel>
    );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function AuthGate({ children }: { children: ReactNode }) {
  const env = import.meta.env as Record<string, string | undefined>;
  const mode = authMode(env, isDesktop());
  if (mode.kind === "none")
    return env.VITE_METIS_ACCESS_KEY === "required" && !isDesktop() ? (
      <AccessKeyGate>{children}</AccessKeyGate>
    ) : (
      <>{children}</>
    );
  if (mode.kind === "misconfigured")
    return (
      <Panel>
        <p role="alert" className="mt-2 text-sm text-bad">
          Sign-in is only partly configured, so Metis will not open. Missing: {mode.missing.join(", ")}.
        </p>
      </Panel>
    );
  return <CognitoGate config={mode.config}>{children}</CognitoGate>;
}
