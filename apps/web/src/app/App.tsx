/**
 * The shell: five surfaces, one status bar, keyboard reachable throughout.
 *
 * Routing is the URL hash — a reload keeps the surface and a link can point at
 * one, without a router dependency. The bottom bar is thumb-reachable on a
 * phone and never covers the transcript editor, which is why the main column
 * carries bottom padding.
 */
import { useEffect, useState } from "react";
import { CaptureScreen } from "../screens/CaptureScreen";
import { InboxScreen } from "../screens/InboxScreen";
import { NotificationsScreen } from "../screens/NotificationsScreen";
import { SettingsScreen } from "../screens/SettingsScreen";
import { TasksScreen } from "../screens/TasksScreen";
import { TodayScreen } from "../screens/TodayScreen";
import { SessionProvider, useSession } from "./session";

const SURFACES = ["today", "capture", "inbox", "tasks", "alerts", "settings"] as const;
type Surface = (typeof SURFACES)[number];

const LABEL: Record<Surface, string> = {
  today: "Today",
  capture: "Capture",
  inbox: "Inbox",
  tasks: "Tasks",
  alerts: "Alerts",
  settings: "Settings",
};

function surfaceFromHash(): Surface {
  const value = window.location.hash.replace("#/", "");
  return (SURFACES as readonly string[]).includes(value) ? (value as Surface) : "today";
}

function Shell() {
  const session = useSession();
  const [surface, setSurface] = useState<Surface>(surfaceFromHash);

  useEffect(() => {
    const sync = () => setSurface(surfaceFromHash());
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  const go = (next: Surface) => {
    window.location.hash = `#/${next}`;
    setSurface(next);
  };

  const offline = !session.online || !session.reachable;

  return (
    <div className="min-h-dvh bg-canvas text-ink">
      <a
        href="#main"
        className="visually-hidden focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-10 focus:bg-surface focus:px-3 focus:py-2"
      >
        Skip to content
      </a>

      <header className="sticky top-0 z-10 border-b border-line bg-surface/95 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-4 py-3">
          <span className="text-sm font-semibold tracking-tight">Metis</span>
          <div className="flex items-center gap-2 text-xs">
            {offline && (
              <span className="rounded-full border border-warn/40 bg-warn-soft px-2 py-0.5 text-warn">
                <span aria-hidden="true">⛌ </span>
                {session.online ? "server unreachable" : "offline"}
              </span>
            )}
            {session.health?.adapters?.inference === "fixture" && (
              <span className="rounded-full border border-line bg-raised px-2 py-0.5 text-muted">
                fixture adapters
              </span>
            )}
            <button
              type="button"
              onClick={() => go("alerts")}
              className="rounded-full border border-line px-2 py-0.5 text-muted hover:text-ink"
            >
              {session.unreadCount > 0 ? `${session.unreadCount} unread` : "alerts"}
            </button>
          </div>
        </div>
      </header>

      <main id="main" className="mx-auto max-w-3xl px-4 pb-28 pt-4">
        {surface === "today" && <TodayScreen onOpenCapture={() => go("capture")} />}
        {surface === "capture" && <CaptureScreen />}
        {surface === "inbox" && <InboxScreen />}
        {surface === "tasks" && <TasksScreen />}
        {surface === "alerts" && <NotificationsScreen />}
        {surface === "settings" && <SettingsScreen />}
      </main>

      <nav
        aria-label="Surfaces"
        className="fixed inset-x-0 bottom-0 border-t border-line bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur"
      >
        <ul className="mx-auto flex max-w-3xl">
          {SURFACES.map((item) => (
            <li key={item} className="flex-1">
              <button
                type="button"
                aria-current={surface === item ? "page" : undefined}
                onClick={() => go(item)}
                className={`w-full px-1 py-3 text-xs font-medium ${
                  surface === item ? "text-accent underline decoration-2 underline-offset-4" : "text-muted"
                }`}
              >
                {LABEL[item]}
                {item === "alerts" && session.unreadCount > 0 ? ` (${session.unreadCount})` : ""}
              </button>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}

export function App() {
  return (
    <SessionProvider>
      <Shell />
    </SessionProvider>
  );
}
