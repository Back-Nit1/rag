/**
 * Session-wide facts the whole app needs: who the server thinks we are, the
 * limits it will enforce, the notification feed and whether we are online.
 *
 * The browser enforces the server's own limits rather than its own guesses, so
 * the numbers come from `/v1/limits` instead of being hardcoded here.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { HealthResponse, Notification, Preferences } from "../contracts/metis";
import { api } from "../lib/api";

export type Limits = {
  max_audio_seconds: number;
  max_upload_bytes: number;
  max_text_chars: number;
  allowed_audio_types: string[];
};

const FALLBACK_LIMITS: Limits = {
  max_audio_seconds: 300,
  max_upload_bytes: 26_214_400,
  max_text_chars: 100_000,
  allowed_audio_types: ["audio/webm"],
};

export type NotificationPermissionState = "unsupported" | "default" | "granted" | "denied";

type SessionValue = {
  health: HealthResponse | null;
  preferences: Preferences | null;
  limits: Limits;
  timezone: string;
  notifications: Notification[];
  unreadCount: number;
  online: boolean;
  reachable: boolean;
  notificationPermission: NotificationPermissionState;
  requestNotificationPermission: () => Promise<void>;
  refreshNotifications: () => Promise<void>;
  markRead: (id: string) => Promise<void>;
  refreshPreferences: () => Promise<void>;
};

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [reachable, setReachable] = useState(true);
  const [preferences, setPreferences] = useState<Preferences | null>(null);
  const [limits, setLimits] = useState<Limits>(FALLBACK_LIMITS);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [online, setOnline] = useState(() => (typeof navigator === "undefined" ? true : navigator.onLine));
  const [permission, setPermission] = useState<NotificationPermissionState>(() =>
    typeof Notification === "undefined" ? "unsupported" : (Notification.permission as NotificationPermissionState),
  );

  const refreshNotifications = useCallback(async () => {
    try {
      const page = await api.listNotifications();
      setNotifications(page.items ?? []);
    } catch {
      // The feed is not worth an error banner; the status bar already shows
      // that the server is unreachable.
    }
  }, []);

  const refreshPreferences = useCallback(async () => {
    try {
      setPreferences(await api.preferences());
    } catch {
      /* keep the previous value */
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [healthResult, limitsResult] = await Promise.all([api.health(), api.limits()]);
        if (cancelled) return;
        setHealth(healthResult);
        setLimits({ ...FALLBACK_LIMITS, ...(limitsResult as Partial<Limits>) });
        setReachable(true);
      } catch {
        if (!cancelled) setReachable(false);
      }
      await refreshPreferences();
      await refreshNotifications();
    };
    void load();
    const timer = window.setInterval(() => void refreshNotifications(), 15_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [refreshNotifications, refreshPreferences]);

  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);

  const requestNotificationPermission = useCallback(async () => {
    if (typeof Notification === "undefined") return setPermission("unsupported");
    const result = await Notification.requestPermission();
    setPermission(result as NotificationPermissionState);
  }, []);

  const markRead = useCallback(async (id: string) => {
    const updated = await api.markNotificationRead(id);
    setNotifications((current) => current.map((n) => (n.id === id ? updated : n)));
  }, []);

  const value = useMemo<SessionValue>(
    () => ({
      health,
      preferences,
      limits,
      timezone: preferences?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone,
      notifications,
      unreadCount: notifications.filter((n) => !n.read_at).length,
      online,
      reachable,
      notificationPermission: permission,
      requestNotificationPermission,
      refreshNotifications,
      markRead,
      refreshPreferences,
    }),
    [
      health,
      preferences,
      limits,
      notifications,
      online,
      reachable,
      permission,
      requestNotificationPermission,
      refreshNotifications,
      markRead,
      refreshPreferences,
    ],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside SessionProvider");
  return value;
}
