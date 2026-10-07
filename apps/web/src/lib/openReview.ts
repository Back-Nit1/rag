/**
 * Desktop: the HUD asks the main window to show a capture for review
 * (`hud:open-review`). The id waits here until the Capture screen takes it.
 */
import { useEffect, useSyncExternalStore } from "react";
import { isDesktop } from "./desktop";
import { OPEN_REVIEW_EVENT, parseOpenReview, tauriListen } from "./hud";

let pending: string | null = null;
const subs = new Set<() => void>();

export const requestReview = (id: string): void => {
  pending = id;
  subs.forEach((s) => s());
};
export const takePendingReview = (): string | null => {
  const id = pending;
  pending = null;
  return id;
};
export const subscribeReview = (s: () => void) => (subs.add(s), () => void subs.delete(s));
export const peekPendingReview = (): string | null => pending;
export const usePendingReview = (): string | null => useSyncExternalStore(subscribeReview, peekPendingReview);

/** Valid events with an id go to Capture; null-id or malformed events do nothing but show the window. */
export function handleOpenReview(raw: unknown, go: () => void): void {
  const parsed = parseOpenReview(raw);
  if (!parsed?.captureId) return;
  requestReview(parsed.captureId);
  go();
}

export function useOpenReviewListener(go: () => void): void {
  useEffect(() => {
    if (!isDesktop()) return;
    let off: (() => void) | undefined;
    let closed = false;
    tauriListen(OPEN_REVIEW_EVENT, (raw) => handleOpenReview(raw, go))
      .then((u) => (closed ? u() : (off = u)))
      .catch(() => undefined);
    return () => {
      closed = true;
      off?.();
    };
  }, [go]);
}
