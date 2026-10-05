import type { ReactNode } from "react";

export function Card({
  children,
  as: Tag = "section",
  className = "",
}: {
  children: ReactNode;
  as?: "section" | "article" | "li" | "div";
  className?: string;
}) {
  return (
    <Tag className={`rounded-xl border border-line bg-surface p-4 ${className}`}>{children}</Tag>
  );
}

export function SectionHeading({ children, hint }: { children: ReactNode; hint?: string }) {
  return (
    <div className="mb-3 flex items-baseline justify-between gap-3">
      <h2 className="text-sm font-semibold tracking-wide text-ink uppercase">{children}</h2>
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-xl border border-dashed border-line px-4 py-6 text-sm text-muted">{children}</p>;
}
