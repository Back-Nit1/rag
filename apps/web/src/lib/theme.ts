/** Light/dark preference. Light is the default; the choice persists per device. */
import { useSyncExternalStore } from "react";

export type Theme = "light" | "dark";
const KEY = "metis.theme";
const listeners = new Set<() => void>();

function read(): Theme {
  try {
    return localStorage.getItem(KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

let current: Theme = read();

function apply(theme: Theme): void {
  const root = document.documentElement;
  root.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#0a0a0a" : "#ffffff");
}

/** Call once before first render so there is no flash of the wrong theme. */
export function initTheme(): void {
  apply(current);
}

export function setTheme(next: Theme): void {
  current = next;
  try {
    localStorage.setItem(KEY, next);
  } catch {
    /* private mode: the choice lasts for this session only */
  }
  apply(next);
  listeners.forEach((l) => l());
}

export function useTheme(): [Theme, () => void] {
  const theme = useSyncExternalStore(
    (l) => (listeners.add(l), () => void listeners.delete(l)),
    () => current,
  );
  return [theme, () => setTheme(theme === "dark" ? "light" : "dark")];
}
