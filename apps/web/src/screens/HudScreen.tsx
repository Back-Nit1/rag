import { useEffect, useState } from "react";
import { HudView } from "../components/HudView";
import { IDLE_SNAPSHOT, parseSnapshot, tauriHudAdapter, type HudAdapter, type HudSnapshot } from "../lib/hud";

/** Rendered for `#/hud`. No auth, API or session providers: the HUD holds no credentials. */
export function HudScreen({ adapter }: { adapter?: HudAdapter }) {
  const [snapshot, setSnapshot] = useState<HudSnapshot>(IDLE_SNAPSHOT);
  const [openError, setOpenError] = useState(false);
  const [adapterRef] = useState(() => adapter ?? tauriHudAdapter());

  useEffect(() => {
    let off: (() => void) | undefined;
    let closed = false;
    const apply = (raw: unknown) => {
      const next = parseSnapshot(raw);
      if (next && !closed) setSnapshot(next);
    };
    // Listen first, then read the current state, so an event between the two is not lost.
    void (async () => {
      try {
        off = await adapterRef.listen(apply);
        if (closed) off();
        apply(await adapterRef.getSnapshot());
      } catch {
        /* outside Tauri or capability missing: stay on the honest idle view */
      }
    })();
    return () => {
      closed = true;
      off?.();
    };
  }, [adapterRef]);

  const open = () => {
    setOpenError(false);
    adapterRef.openMain(snapshot.captureId).catch(() => setOpenError(true));
  };

  return (
    <HudView
      snapshot={snapshot}
      onCancel={() => void adapterRef.cancel().catch(() => undefined)}
      onOpen={open}
      openError={openError}
    />
  );
}
