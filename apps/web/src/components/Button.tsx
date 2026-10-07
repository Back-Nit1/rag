import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "secondary" | "quiet" | "danger";

// Primary is the only filled control; everything else is outlined or text, so
// the single most important action per surface is obvious even in grayscale.
const VARIANT: Record<Variant, string> = {
  primary: "bg-accent text-on-accent border-accent font-semibold shadow-card hover:brightness-110 active:brightness-95",
  secondary: "bg-surface text-ink border-line-strong hover:bg-raised",
  quiet: "bg-transparent text-muted border-transparent hover:text-ink hover:bg-raised",
  danger: "bg-transparent text-bad border-bad/50 hover:bg-bad-soft",
};

export function Button({
  variant = "secondary",
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      type="button"
      {...rest}
      className={`inline-flex min-h-11 items-center justify-center gap-1.5 rounded-md border px-3.5 py-1.5 text-sm font-medium transition-[background-color,filter,color] duration-150 select-none disabled:cursor-not-allowed disabled:opacity-50 md:min-h-10 ${VARIANT[variant]} ${className}`}
    />
  );
}
