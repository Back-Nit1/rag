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
      <span className="font-medium text-ink">
        {label}
        {required && (
          <span className="ml-1.5 font-normal text-warn" title="Missing — nothing was guessed">
            (needed)
          </span>
        )}
      </span>
      {children}
      {hint && <span className="text-2xs text-muted">{hint}</span>}
    </label>
  );
}

export const inputClass = "control";
