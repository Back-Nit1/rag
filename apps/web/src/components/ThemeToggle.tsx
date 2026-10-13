import { useTheme } from "../lib/theme";
import { Icon } from "./Icon";

/** One control, one job: switch between light and dark. */
export function ThemeToggle({ className = "", label = false }: { className?: string; label?: boolean }) {
  const [theme, toggle] = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${next} mode`}
      title={`Switch to ${next} mode`}
      className={`press inline-flex items-center gap-2 rounded-full ${className}`}
    >
      <span key={theme} className="turn grid">
        <Icon name={theme === "dark" ? "sun" : "moon"} />
      </span>
      {label && <span>{theme === "dark" ? "Light mode" : "Dark mode"}</span>}
    </button>
  );
}
