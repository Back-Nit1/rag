import type { ReactNode } from "react";

/** A small labelled control. Contextual, not part of a mandatory form. */
export function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: ReactNode;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-xs">
      <span className="font-medium text-muted">
        {label}
        {required && (
          <span className="ml-1 text-warn" title="Missing — nothing was guessed">
            needed
          </span>
        )}
      </span>
      {children}
      {hint && <span className="text-[11px] text-muted">{hint}</span>}
    </label>
  );
}

export const inputClass =
  "min-h-9 w-full rounded-lg border border-line bg-canvas px-2 py-1.5 text-sm text-ink placeholder:text-muted";
