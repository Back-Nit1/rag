/**
 * Public landing page for the browser build. It explains what Metis is and how
 * it works, and nothing on it talks to the API. The product illustration is
 * labelled as an illustration: it is not output from a real account.
 */
import { useEffect } from "react";
import { Icon } from "../components/Icon";
import { ThemeToggle } from "../components/ThemeToggle";

const STEPS = [
  {
    title: "Capture",
    body: "Speak or type a thought the way it comes. Metis keeps your original words as the source, untouched.",
  },
  {
    title: "Review",
    body: "Metis proposes tasks, events and reminders. Every proposal points back to the exact words it came from.",
  },
  {
    title: "Commit",
    body: "You approve what is real. Anything Metis could not find, like a date or a time, stays blank until you fill it in.",
  },
  {
    title: "Plan",
    body: "Ask for a plan for your day. Fixed appointments stay fixed; flexible work fits around them, with estimates labelled as estimates.",
  },
] as const;

const PRINCIPLES = [
  {
    title: "Nothing is invented",
    body: "An unknown date stays unknown. Every fact Metis shows carries a link to the note it came from.",
  },
  {
    title: "Honest about reminders",
    body: "A reminder says “scheduled” only after the scheduler confirms it. Until then it says what is pending, or what failed.",
  },
  {
    title: "You stay in control",
    body: "Nothing becomes a task, event or reminder until you commit it. Edits are checked for conflicts, so nobody overwrites your changes.",
  },
  {
    title: "Spends only what you set",
    body: "Plain reminders use no AI at all. When a model is called, the cost is reserved against your daily budget first.",
  },
] as const;

function Wordmark() {
  return <span className="font-serif text-2xl font-semibold tracking-tight">Metis</span>;
}

/** An illustration of one capture becoming reviewable proposals. */
function Illustration() {
  return (
    <figure className="w-full max-w-xl">
      <div className="rise overflow-hidden rounded-3xl border border-line bg-surface shadow-lift">
        <div className="flex items-center gap-2 border-b border-line px-5 py-3 font-mono text-2xs uppercase tracking-[0.12em] text-muted">
          <span className="size-2 rounded-full bg-record" aria-hidden="true" />
          Note · 9:12 AM
        </div>
        <p className="px-5 pt-5 font-serif text-lg leading-relaxed">
          “Register for the Boston half marathon. Dentist <mark className="rounded bg-accent-soft px-1 text-ink">next Tuesday at 3</mark>.
          Remind me to call mom tonight.”
        </p>
        <ul className="mt-5 divide-y divide-line border-t border-line">
          {[
            ["Task", "Register for the Boston half marathon", "Due date not stated", "warn"],
            ["Event", "Dentist", "Tue 3:00 PM · fixed", "good"],
            ["Reminder", "Call mom", "Tonight · waiting for confirmation", "busy"],
          ].map(([kind, title, detail, tone], i) => (
            <li
              key={title}
              style={{ animationDelay: `${700 + i * 220}ms` }}
              className="rise flex items-start gap-3 px-5 py-3.5"
            >
              <span className="mt-0.5 rounded-full border border-line-strong px-2 py-0.5 font-mono text-2xs uppercase tracking-[0.1em] text-muted">
                {kind}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium">{title}</span>
                <span className={`block text-xs ${tone === "warn" ? "text-warn" : tone === "good" ? "text-good" : "text-muted"}`}>
                  {detail}
                </span>
              </span>
              <span className="rounded-full bg-accent px-3 py-1 text-xs font-semibold text-on-accent">Commit</span>
            </li>
          ))}
        </ul>
      </div>
      <figcaption className="mt-3 text-center font-mono text-2xs uppercase tracking-[0.12em] text-muted">
        Illustration, not real data
      </figcaption>
    </figure>
  );
}

export function Landing({ onOpen }: { onOpen: () => void }) {
  useEffect(() => {
    const els = document.querySelectorAll<HTMLElement>("[data-reveal]");
    if (!("IntersectionObserver" in window)) {
      els.forEach((el) => el.classList.add("in"));
      return;
    }
    const io = new IntersectionObserver(
      (entries) =>
        entries.forEach((e) => {
          if (e.isIntersecting) {
            e.target.classList.add("in");
            io.unobserve(e.target);
          }
        }),
      { threshold: 0.15, rootMargin: "0px 0px -8% 0px" },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);

  const cta =
    "inline-flex h-12 items-center justify-center gap-2 rounded-full bg-accent px-6 text-[15px] font-semibold text-on-accent transition-opacity hover:opacity-85";
  return (
    <div className="min-h-dvh bg-canvas text-ink">
      <header className="sticky top-0 z-20 border-b border-line bg-canvas/85 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-6 px-5">
          <Wordmark />
          <nav aria-label="Page" className="hidden items-center gap-7 text-sm font-medium text-muted md:flex">
            <a className="hover:text-ink" href="#how">
              How it works
            </a>
            <a className="hover:text-ink" href="#principles">
              Principles
            </a>
            <a className="hover:text-ink" href="#where">
              Where it runs
            </a>
          </nav>
          <div className="flex items-center gap-2">
            <ThemeToggle className="grid size-10 place-items-center text-muted hover:text-ink" />
            <button type="button" onClick={onOpen} className="inline-flex h-10 items-center rounded-full bg-accent px-5 text-sm font-semibold text-on-accent hover:opacity-85">
              Open Metis
            </button>
          </div>
        </div>
      </header>

      <main>
        <section className="mx-auto grid max-w-6xl items-center gap-14 px-5 pb-24 pt-16 lg:grid-cols-[1.1fr_1fr] lg:pt-24">
          <div>
            <p className="font-mono text-xs uppercase tracking-[0.14em] text-muted">Voice-first personal assistant</p>
            <h1 className="mt-5 text-[clamp(2.75rem,7vw,5.5rem)] font-medium leading-[0.98] tracking-[-0.035em]">
              Say it once.
              <br />
              Metis keeps track.
            </h1>
            <p className="mt-6 max-w-lg text-lg leading-relaxed text-muted">
              Talk or type the way you think. Metis turns messy notes into tasks, events and reminders you can check
              before they count, then helps you plan the day around them.
            </p>
            <div className="mt-9 flex flex-wrap items-center gap-3">
              <button type="button" onClick={onOpen} className={cta}>
                Open Metis <Icon name="arrow" size={18} />
              </button>
              <a
                href="#how"
                className="inline-flex h-12 items-center rounded-full border border-line-strong px-6 text-[15px] font-medium hover:bg-raised"
              >
                See how it works
              </a>
            </div>
          </div>
          <Illustration />
        </section>

        <section id="how" className="scroll-mt-16 border-y border-line bg-raised">
          <div className="mx-auto max-w-6xl px-5 py-24">
            <p className="font-mono text-xs uppercase tracking-[0.14em] text-muted">How it works</p>
            <h2 data-reveal className="mt-4 max-w-2xl text-[clamp(2rem,4.5vw,3.25rem)] font-medium leading-[1.05] tracking-[-0.03em]">
              From a passing thought to a plan you can trust.
            </h2>
            <ol className="mt-14 grid gap-5 md:grid-cols-2 lg:grid-cols-4">
              {STEPS.map((step, i) => (
                <li
                  key={step.title}
                  data-reveal
                  style={{ "--d": `${i * 90}ms` } as React.CSSProperties}
                  className="rounded-2xl border border-line bg-surface p-6"
                >
                  <span className="font-mono text-xs text-muted">Step {i + 1}</span>
                  <h3 className="mt-3 text-xl font-medium">{step.title}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-muted">{step.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section id="principles" className="scroll-mt-16 mx-auto max-w-6xl px-5 py-24">
          <p className="font-mono text-xs uppercase tracking-[0.14em] text-muted">Principles</p>
          <h2 data-reveal className="mt-4 max-w-2xl text-[clamp(2rem,4.5vw,3.25rem)] font-medium leading-[1.05] tracking-[-0.03em]">
            An assistant that tells you what it knows, and what it doesn’t.
          </h2>
          <dl className="mt-14 grid gap-x-12 gap-y-10 md:grid-cols-2">
            {PRINCIPLES.map((p, i) => (
              <div key={p.title} data-reveal
                style={{ "--d": `${(i % 2) * 90}ms` } as React.CSSProperties}
                className="border-t border-line-strong pt-5">
                <dt className="text-xl font-medium">{p.title}</dt>
                <dd className="mt-2 max-w-md text-[15px] leading-relaxed text-muted">{p.body}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section id="where" className="scroll-mt-16 border-t border-line bg-raised">
          <div className="mx-auto grid max-w-6xl gap-10 px-5 py-24 md:grid-cols-2">
            <div>
              <p className="font-mono text-xs uppercase tracking-[0.14em] text-muted">Where it runs</p>
              <h2 className="mt-4 text-[clamp(2rem,4.5vw,3.25rem)] font-medium leading-[1.05] tracking-[-0.03em]">
                In your browser, or on your desktop.
              </h2>
            </div>
            <div data-reveal className="space-y-6 text-[15px] leading-relaxed text-muted">
              <p>
                <strong className="font-medium text-ink">Browser.</strong> Installable as an app on your phone or
                laptop. Capture works offline: notes wait on your device until you reconnect, and Metis says so.
              </p>
              <p>
                <strong className="font-medium text-ink">Desktop.</strong> The same app in its own window, with
                its local services running on your machine.
              </p>
            </div>
          </div>
        </section>

        <section className="mx-auto max-w-6xl px-5 py-28 text-center">
          <h2 data-reveal className="mx-auto max-w-3xl text-[clamp(2.25rem,5.5vw,4rem)] font-medium leading-[1] tracking-[-0.035em]">
            Capture your first thought.
          </h2>
          <button type="button" onClick={onOpen} className={`${cta} mt-9`}>
            Open Metis <Icon name="arrow" size={18} />
          </button>
        </section>
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-5 py-8 text-sm text-muted">
          <Wordmark />
          <p>Voice-first. Nothing invented.</p>
        </div>
      </footer>
    </div>
  );
}
