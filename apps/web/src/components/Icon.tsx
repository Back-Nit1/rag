/** A small stroke icon set (no icon dependency). Decorative: always paired with text. */
const PATHS = {
  today: "M4 6h16M4 12h16M4 18h10",
  mic: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3ZM6 11a6 6 0 0 0 12 0M12 17v4",
  inbox: "M4 13l2-8h12l2 8M4 13v6h16v-6M4 13h5l1 2h4l1-2h5",
  tasks: "M9 6h11M9 12h11M9 18h11M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2",
  bell: "M6 17V11a6 6 0 0 1 12 0v6l1.5 2h-15L6 17ZM10 21h4",
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM19 12l2-1.5-2-3.5-2.3.7a7 7 0 0 0-1.7-1L14.5 4h-5l-.5 2.7a7 7 0 0 0-1.7 1L5 7 3 10.5 5 12a7 7 0 0 0 0 2l-2 1.5L5 19l2.3-.7a7 7 0 0 0 1.7 1l.5 2.7h5l.5-2.7a7 7 0 0 0 1.7-1l2.3.7 2-3.5-2-1.5a7 7 0 0 0 0-2Z",
  check: "M5 12.5l4.5 4.5L19 7.5",
  alert: "M12 4l9 16H3L12 4ZM12 10v4M12 17h.01",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 11v5M12 8h.01",
  pin: "M9 4h6l-1 6 3 3H7l3-3-1-6ZM12 13v7",
  wifiOff: "M3 3l18 18M8.5 8.8A10 10 0 0 0 3 12M5.6 15.2a10 10 0 0 1 4.3-2.4M12 20h.01M16 12.4A10 10 0 0 1 21 12",
  stop: "M7 7h10v10H7z",
  pause: "M9 6v12M15 6v12",
  send: "M4 12l16-8-6 16-3-7-7-1Z",
  search: "M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13ZM15.5 15.5L20 20",
  cloud: "M7 18a4 4 0 0 1-.5-8A6 6 0 0 1 18 9.5 4.3 4.3 0 0 1 17.5 18H7Z",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className="shrink-0"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
