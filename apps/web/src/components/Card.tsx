import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

/** A flat panel: a hairline border, no shadow. Elevation is reserved for floating controls. */
export function Card({
  children,
  as: Tag = "section",
  className = "",
}: {
  children: ReactNode;
  as?: "section" | "article" | "li" | "div";
  className?: string;
}) {
  return <Tag className={`rounded-lg border border-line bg-surface p-4 md:p-5 ${className}`}>{children}</Tag>;
}

/**
 * Section title as a ruled eyebrow: small mono label, a hairline rule, then the
 * plain-language qualifier. Reads as a notebook tab and stays scannable in grayscale.
 */
export function SectionHeading({
  children,
  hint,
  id,
  as: Tag = "h2",
}: {
  children: ReactNode;
  hint?: string;
  id?: string;
  as?: "h2" | "h3";
}) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-0.5">
      <Tag id={id} className="font-mono text-xs font-semibold uppercase tracking-[0.12em] whitespace-nowrap text-ink">
        {children}
      </Tag>
      <span aria-hidden="true" className="h-px min-w-8 flex-1 bg-line-strong" />
      {hint && <span className="text-xs text-muted max-sm:order-last max-sm:basis-full">{hint}</span>}
    </div>
  );
}

/** An empty state says what is true and, when there is one, offers the next move. */
export function Empty({
  children,
  title,
  action,
}: {
  children: ReactNode;
  title?: string;
  action?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-line-strong bg-surface/40 px-4 py-6 text-sm text-muted">
      {title && <p className="mb-0.5 font-serif text-base font-semibold text-ink">{title}</p>}
      <p className="max-w-prose">{children}</p>
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

type NoticeTone = "info" | "warn" | "bad" | "good";

const NOTICE_TONE: Record<NoticeTone, { box: string; icon: IconName; word: string }> = {
  info: { box: "border-line-strong bg-raised text-ink", icon: "info", word: "Note" },
  warn: { box: "border-warn/50 bg-warn-soft text-warn", icon: "alert", word: "Needs attention" },
  bad: { box: "border-bad/50 bg-bad-soft text-bad", icon: "alert", word: "Problem" },
  good: { box: "border-good/50 bg-good-soft text-good", icon: "check", word: "Done" },
};

/**
 * An inline message. Always carries an icon and, for screen readers, the word
 * for its tone — colour is never the only signal. Pass `live` only for a
 * message that appears as the result of an action, so it is announced once.
 */
export function Notice({
  tone = "info",
  children,
  live = false,
  action,
  className = "",
}: {
  tone?: NoticeTone;
  children: ReactNode;
  live?: boolean;
  action?: ReactNode;
  className?: string;
}) {
  const t = NOTICE_TONE[tone];
  return (
    <div
      role={live ? (tone === "bad" ? "alert" : "status") : undefined}
      className={`flex items-start gap-2.5 rounded-md border px-3 py-2.5 text-sm ${t.box} ${className}`}
    >
      <span className="mt-0.5">
        <Icon name={t.icon} size={16} />
      </span>
      <div className="min-w-0 flex-1">
        <span className="visually-hidden">{t.word}: </span>
        {children}
        {action && <div className="mt-2">{action}</div>}
      </div>
    </div>
  );
}

/** Skeleton rows for first load; announced as loading, not as content. */
export function Loading({ label, rows = 3 }: { label: string; rows?: number }) {
  return (
    <div role="status" aria-label={label} className="flex flex-col gap-3">
      <span className="visually-hidden">{label}</span>
      <div className="skeleton h-8 w-2/3" />
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton h-16 w-full" />
      ))}
    </div>
  );
}

/** A mono, low-key chip for provenance and revisions. */
export function Chip({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className="inline-flex items-center rounded-sm border border-line bg-raised px-1.5 py-0.5 font-mono text-2xs text-muted"
    >
      {children}
    </span>
  );
}

/** The surface's one h1: serif, left aligned, with an optional plain subtitle and action. */
export function PageHeader({
  title,
  sub,
  action,
}: {
  title: ReactNode;
  sub?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
      <div className="min-w-0 border-l-2 border-accent pl-3.5">
        <h1 className="font-serif text-2xl font-semibold leading-tight tracking-tight text-ink md:text-[2rem]">{title}</h1>
        {sub && <p className="mt-0.5 text-sm text-muted">{sub}</p>}
      </div>
      {action}
    </header>
  );
}
