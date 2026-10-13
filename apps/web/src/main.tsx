import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app/App";
import { HudScreen } from "./screens/HudScreen";
import { initTheme } from "./lib/theme";
import "./styles.css";

initTheme();

const root = document.getElementById("root");
if (!root) throw new Error("the #root element is missing from index.html");

createRoot(root).render(
  <StrictMode>
    {window.location.hash.startsWith("#/hud") ? <HudScreen /> : <App />}
  </StrictMode>,
);

// The service worker only caches the app shell and displays push messages.
// Registration failing is not an error the user needs to see: the app works
// without it and the in-app feed is still the delivery record.
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  });
}
