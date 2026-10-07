/**
 * The shell: six surfaces, one status area, keyboard reachable throughout.
 *
 * Routing is the URL hash — a reload keeps the surface and a link can point at
 * one, without a router dependency.
 *
 * Wide screens get a left rail (wordmark, surfaces, system status). Phones and
 * tablets get a thumb-reach tab bar with Capture raised in the middle; the
 * main column carries bottom padding so the bar never covers the transcript
 * editor. Settings sits in the header on small screens.
 */
import type { ReactNode } from "react";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { connectDesktop, getDesktopState, subscribeDesktop } from "../lib/desktop";
import { Icon, type IconName } from "../components/Icon";
import { Notice } from "../components/Card";
import { CaptureScreen } from "../screens/CaptureScreen";
import { InboxScreen } from "../screens/InboxScreen";
import { NotificationsScreen } from "../screens/NotificationsScreen";
import { ProjectsScreen } from "../screens/ProjectsScreen";
import { SearchScreen } from "../screens/SearchScreen";
import { SettingsScreen } from "../screens/SettingsScreen";
import { TasksScreen } from "../screens/TasksScreen";
import { TodayScreen } from "../screens/TodayScreen";
import { useOpenReviewListener } from "../lib/openReview";
import { AuthGate } from "./AuthGate";
import { SessionProvider, useSession } from "./session";

const SURFACES = ["today", "capture", "inbox", "tasks", "projects", "search", "alerts", "settings"] as const;
type Surface = (typeof SURFACES)[number];

const LABEL: Record<Surface, string> = {
  today: "Today",
  capture: "Capture",
  inbox: "Inbox",
  tasks: "Tasks",
  projects: "Projects",
  search: "Search",
  alerts: "Alerts",
  settings: "Settings",
};

const ICON: Record<Surface, IconName> = {
  today: "today",
  capture: "mic",
  inbox: "inbox",
  tasks: "tasks",
  projects: "pin",
  search: "search",
  alerts: "bell",
  settings: "gear",
};

const BAR: Surface[] = ["today", "inbox", "capture", "tasks", "alerts"];

function surfaceFromHash(): Surface {
  const value = window.location.hash.replace("#/", "");
  return (SURFACES as readonly string[]).includes(value) ? (value as Surface) : "today";
}

function Shell() {
  const session = useSession();
  const [surface, setSurface] = useState<Surface>(surfaceFromHash);
  const main = useRef<HTMLElement>(null);
  const first = useRef(true);

  useEffect(() => {
    const sync = () => setSurface(surfaceFromHash());
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  // After a navigation, move focus to the new surface so keyboard and screen
  // reader users land on it; skip the very first render.
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    main.current?.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }, [surface]);

  const go = (next: Surface) => {
    window.location.hash = `#/${next}`;
    setSurface(next);
  };

  useOpenReviewListener(() => go("capture"));

  const offline = !session.online || !session.reachable;
  const fixtures = Object.entries(session.health?.adapters ?? {}).filter(([, v]) => v === "fixture");
  const badge = (item: Surface) => (item === "alerts" && session.unreadCount > 0 ? session.unreadCount : 0);

  const link = (item: Surface, tone: "rail" | "bar") => {
    const active = surface === item;
    const count = badge(item);
    const label = (
      <>
        {LABEL[item]}
        {count > 0 && (
          <span className="ml-1.5 rounded-full bg-accent px-1.5 py-px font-mono text-2xs text-on-accent">
            {count}
            <span className="visually-hidden"> unread</span>
          </span>
        )}
      </>
    );
    if (tone === "rail") {
      return (
        <button
          type="button"
          aria-current={active ? "page" : undefined}
          onClick={() => go(item)}
          className={`flex min-h-10 w-full items-center gap-3 rounded-md px-3 text-left text-sm font-medium transition-colors ${
            active ? "bg-surface font-semibold text-ink shadow-[inset_3px_0_0_var(--color-accent)]" : "text-muted hover:bg-raised hover:text-ink"
          }`}
        >
          <Icon name={ICON[item]} />
          <span className="flex items-center">{label}</span>
        </button>
      );
    }
    const isCapture = item === "capture";
    return (
      <button
        type="button"
        aria-current={active ? "page" : undefined}
        onClick={() => go(item)}
        className={`flex w-full flex-col items-center justify-end gap-0.5 pb-1.5 pt-1 text-2xs font-medium ${
          active ? "text-accent" : "text-muted"
        }`}
      >
        {isCapture ? (
          <span className="-mt-6 mb-0.5 grid size-14 place-items-center rounded-full border-4 border-canvas bg-accent text-on-accent shadow-lift">
            <Icon name="mic" size={24} />
          </span>
        ) : (
          <span
            className={`grid h-7 w-12 place-items-center rounded-full transition-colors ${active ? "bg-accent-soft" : ""}`}
          >
            <Icon name={ICON[item]} size={20} />
          </span>
        )}
        <span className="flex items-center">{label}</span>
      </button>
    );
  };

  return (
    <div className="min-h-dvh bg-canvas text-ink lg:grid lg:grid-cols-[15.5rem_minmax(0,1fr)]">
      <a
        href="#main"
        className="visually-hidden focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:bg-surface focus:px-3 focus:py-2"
      >
        Skip to content
      </a>

      {/* wide: rail */}
      <aside className="sticky top-0 hidden h-dvh flex-col gap-6 border-r border-line bg-rail px-3 py-5 lg:flex">
        <div className="px-3">
          <p className="font-serif text-2xl font-semibold leading-none tracking-tight">Metis</p>
          <p className="mt-1.5 font-mono text-2xs uppercase tracking-[0.14em] text-muted">Notebook · Desk</p>
        </div>
        <nav aria-label="Surfaces" className="flex flex-col gap-1">
          {SURFACES.map((item) => (
            <div key={item}>{link(item, "rail")}</div>
          ))}
        </nav>
        <div className="mt-auto flex flex-col gap-2 px-3 text-xs text-muted">
          <p className="flex items-center gap-2">
            <Icon name={offline ? "wifiOff" : "cloud"} size={14} />
            {!session.online ? "Offline" : !session.reachable ? "Server unreachable" : "Connected"}
          </p>
          {fixtures.length > 0 && (
            <p className="rounded-md border border-dashed border-line-strong bg-surface px-2.5 py-2 text-ink">
              <span className="block font-mono text-2xs font-semibold uppercase tracking-[0.12em]">Fixture mode</span>
              Results come from test adapters, not production.
            </p>
          )}
        </div>
      </aside>

      <div className="min-w-0">
        {/* small: header */}
        <header className="sticky top-0 z-20 border-b border-line bg-surface lg:hidden">
          <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-4 py-2.5">
            <span className="font-serif text-xl font-semibold tracking-tight">Metis</span>
            <div className="flex items-center gap-2 text-xs">
              {fixtures.length > 0 && (
                <span
                  className="rounded-full border border-line-strong bg-raised px-2 py-0.5 font-medium text-ink"
                  title="Results come from test adapters, not production"
                >
                  Fixture mode
                </span>
              )}
              {(["projects", "search"] as const).map((item) => (
                <button
                  key={item}
                  type="button"
                  aria-label={LABEL[item]}
                  aria-current={surface === item ? "page" : undefined}
                  onClick={() => go(item)}
                  className={`grid size-11 place-items-center rounded-full ${surface === item ? "bg-accent-soft text-accent" : "text-muted"}`}
                >
                  <Icon name={ICON[item]} />
                </button>
              ))}
              <button
                type="button"
                aria-label="Settings"
                aria-current={surface === "settings" ? "page" : undefined}
                onClick={() => go("settings")}
                className={`grid size-11 place-items-center rounded-full ${surface === "settings" ? "bg-accent-soft text-accent" : "text-muted"}`}
              >
                <Icon name="gear" />
              </button>
            </div>
          </div>
        </header>

        <main
          id="main"
          ref={main}
          tabIndex={-1}
          className="mx-auto w-full max-w-3xl px-4 pb-32 pt-5 outline-none md:px-6 lg:max-w-5xl lg:px-10 lg:pb-16 lg:pt-8"
        >
          {offline && (
            <Notice tone="warn" className="mb-4">
              {!session.online
                ? "You are offline. Typed notes are held on this device until you reconnect; they are not saved yet."
                : "The Metis server is unreachable. Nothing new is being saved; typed notes are held on this device."}
            </Notice>
          )}
          {surface === "today" && <TodayScreen onOpenCapture={() => go("capture")} />}
          {surface === "capture" && <CaptureScreen />}
          {surface === "inbox" && <InboxScreen />}
          {surface === "tasks" && <TasksScreen />}
          {surface === "projects" && <ProjectsScreen />}
          {surface === "search" && <SearchScreen />}
          {surface === "alerts" && <NotificationsScreen />}
          {surface === "settings" && <SettingsScreen />}
        </main>
      </div>

      {/* small: tab bar */}
      <nav
        aria-label="Surfaces"
        className="fixed inset-x-0 bottom-0 z-20 border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] lg:hidden"
      >
        <ul className="mx-auto flex max-w-xl items-end">
          {BAR.map((item) => (
            <li key={item} className="flex-1">
              {link(item, "bar")}
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}

/** In the desktop shell nothing renders (or requests) until the loopback API is known. */
function DesktopGate({ children }: { children: ReactNode }) {
  const desktop = useSyncExternalStore(subscribeDesktop, getDesktopState);
  useEffect(() => {
    if (desktop.phase === "starting") void connectDesktop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (desktop.phase === "browser" || desktop.phase === "ready") return <>{children}</>;
  return (
    <main className="grid min-h-dvh place-items-center bg-canvas p-6 text-ink">
      <div role="status" className="max-w-sm text-center">
        <p className="font-serif text-xl font-semibold">Metis</p>
        {desktop.phase === "starting" ? (
          <p className="mt-2 text-sm text-muted">Starting local services…</p>
        ) : (
          <>
            <p className="mt-2 text-sm">{desktop.reason}</p>
            <button
              type="button"
              onClick={() => void connectDesktop()}
              className="mt-4 min-h-11 rounded-md border border-line-strong bg-surface px-4 text-sm font-medium"
            >
              Try again
            </button>
          </>
        )}
      </div>
    </main>
  );
}

export function App() {
  return (
    <DesktopGate>
      <AuthGate>
        <SessionProvider>
          <Shell />
        </SessionProvider>
      </AuthGate>
    </DesktopGate>
  );
}
