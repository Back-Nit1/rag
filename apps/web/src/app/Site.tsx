/**
 * Public site for the browser build: home, principles, where it runs. It
 * explains what Metis is and how it works, and nothing on it talks to the API. The product illustration is
 * labelled as an illustration: it is not output from a real account.
 */
import { useEffect } from "react";
import { Icon } from "../components/Icon";
import { ThemeToggle } from "../components/ThemeToggle";

export type SiteRoute = "home" | "principles" | "where";
export const SITE_HASH: Record<string, SiteRoute> = {
  "": "home",
  "#": "home",
  "#how": "home",
  "#/principles": "principles",
  "#/where": "where",
  // Earlier in-page anchors, kept so old links land on the right page.
  "#principles": "principles",
  "#where": "where",
};

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

const PRINCIPLE_DETAILS = [
  {
    title: "Nothing is invented",
    body: "Metis only states what your words support. If a date, time or registration status was not said, it stays blank and the interface says “not stated” instead of guessing.",
    seen: "A task reads “Due date not stated” until you set one.",
  },
  {
    title: "Every fact has a source",
    body: "Each task, event or reminder links back to the exact words in the note it came from, so you can always check why Metis believes something.",
    seen: "A “from note” link next to every proposal.",
  },
  {
    title: "Honest about reminders",
    body: "A reminder is “scheduled” only after the scheduler confirms it. While that is pending, or after it fails, Metis says exactly that. Uploaded audio is not “transcribed” until it is.",
    seen: "Reminder states: scheduling, scheduled, schedule failed.",
  },
  {
    title: "You commit, Metis proposes",
    body: "Nothing becomes real until you commit it. Every change carries the version it was based on, so two devices can never silently overwrite each other.",
    seen: "A review step before anything is saved.",
  },
  {
    title: "Estimates are labelled",
    body: "A plan fits flexible work around fixed appointments, and any time estimate is marked as an estimate. When Metis does not know your free time, it says so rather than claiming a gap.",
    seen: "“Free time is unknown” until a calendar or availability is added.",
  },
  {
    title: "Test data never poses as real",
    body: "Results from test adapters are labelled as fixtures. Plain reminders use no AI at all, and when a model is called its cost is reserved against your daily budget first.",
    seen: "A “Fixture mode” badge whenever results are not production.",
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

const CTA =
  "inline-flex h-12 items-center justify-center gap-2 rounded-full bg-accent px-6 text-[15px] font-semibold text-on-accent transition-opacity hover:opacity-85";
const H2 = "mt-4 max-w-2xl text-[clamp(2rem,4.5vw,3.25rem)] font-medium leading-[1.05] tracking-[-0.03em]";
const EYEBROW = "font-mono text-xs uppercase tracking-[0.14em] text-muted";

function PageIntro({ eyebrow, title, children }: { eyebrow: string; title: string; children: React.ReactNode }) {
  return (
    <section className="mx-auto max-w-6xl px-5 pb-16 pt-16 lg:pt-24">
      <p className={EYEBROW}>{eyebrow}</p>
      <h1 className="rise mt-5 max-w-3xl text-[clamp(2.5rem,6vw,4.5rem)] font-medium leading-[1] tracking-[-0.035em]">
        {title}
      </h1>
      <p className="mt-6 max-w-xl text-lg leading-relaxed text-muted">{children}</p>
    </section>
  );
}

function CtaBand({ onOpen, title }: { onOpen: () => void; title: string }) {
  return (
    <section className="mx-auto max-w-6xl px-5 py-28 text-center">
      <h2 data-reveal className="mx-auto max-w-3xl text-[clamp(2.25rem,5.5vw,4rem)] font-medium leading-[1] tracking-[-0.035em]">
        {title}
      </h2>
      <button type="button" onClick={onOpen} className={`${CTA} mt-9`}>
        Open Metis <Icon name="arrow" size={18} />
      </button>
    </section>
  );
}

function PrinciplesPage({ onOpen }: { onOpen: () => void }) {
  return (
    <>
      <PageIntro eyebrow="Principles" title="An assistant that tells you what it knows, and what it doesn’t.">
        Metis is built around a few rules that the interface keeps in plain sight. Each one is something you can see
        on screen, not a promise in a footnote.
      </PageIntro>
      <section className="mx-auto max-w-6xl px-5 pb-8">
        <ol className="divide-y divide-line border-y border-line">
          {PRINCIPLE_DETAILS.map((p, i) => (
            <li key={p.title} data-reveal className="grid gap-4 py-9 md:grid-cols-[1fr_1.4fr_1fr] md:gap-10">
              <h2 className="text-2xl font-medium tracking-[-0.02em]">{p.title}</h2>
              <p className="text-[15px] leading-relaxed text-muted">{p.body}</p>
              <p className="rounded-xl border border-line bg-raised px-4 py-3 text-sm text-muted md:self-start">
                <span className="mb-1 block font-mono text-2xs uppercase tracking-[0.12em]">What you’ll see</span>
                {p.seen}
              </p>
              <span className="visually-hidden">Principle {i + 1}</span>
            </li>
          ))}
        </ol>
      </section>
      <CtaBand onOpen={onOpen} title="See it for yourself." />
    </>
  );
}

const PLACES = [
  {
    name: "In the browser",
    lines: [
      "Open it on any laptop or phone and install it like an app.",
      "Capture works offline. Typed notes wait on your device and Metis tells you they are not saved yet.",
      "Sign in once; your notes, tasks and reminders follow you between devices.",
    ],
  },
  {
    name: "On your desktop",
    lines: [
      "The same interface in its own window, not a different product.",
      "Its supporting services start on your machine when the app opens, and the window waits until they are ready.",
      "No cloud account is needed for the local setup.",
    ],
  },
] as const;

function WherePage({ onOpen }: { onOpen: () => void }) {
  return (
    <>
      <PageIntro eyebrow="Where it runs" title="One app, in your browser or on your desktop.">
        Pick the place that suits the moment. Both are the same Metis, with the same screens and the same rules about
        what it will and will not claim.
      </PageIntro>
      <section className="mx-auto grid max-w-6xl gap-5 px-5 pb-8 md:grid-cols-2">
        {PLACES.map((place, i) => (
          <article
            key={place.name}
            data-reveal
            style={{ "--d": `${i * 90}ms` } as React.CSSProperties}
            className="rounded-3xl border border-line bg-surface p-8"
          >
            <h2 className="text-2xl font-medium tracking-[-0.02em]">{place.name}</h2>
            <ul className="mt-5 space-y-4 text-[15px] leading-relaxed text-muted">
              {place.lines.map((line) => (
                <li key={line} className="flex gap-3">
                  <span aria-hidden="true" className="mt-2.5 size-1.5 shrink-0 rounded-full bg-ink" />
                  {line}
                </li>
              ))}
            </ul>
          </article>
        ))}
      </section>
      <CtaBand onOpen={onOpen} title="Start where you are." />
    </>
  );
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

function HomePage({ onOpen }: { onOpen: () => void }) {
  return (
    <>
      <section className="mx-auto grid max-w-6xl items-center gap-14 px-5 pb-24 pt-16 lg:grid-cols-[1.1fr_1fr] lg:pt-24">
        <div>
          <p className={EYEBROW}>Voice-first personal assistant</p>
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
            <button type="button" onClick={onOpen} className={CTA}>
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
          <p className={EYEBROW}>How it works</p>
          <h2 data-reveal className={H2}>
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

      <section className="mx-auto grid max-w-6xl gap-5 px-5 py-24 md:grid-cols-2">
        {[
          {
            href: "#/principles",
            eyebrow: "Principles",
            title: "It tells you what it knows, and what it doesn’t.",
            body: PRINCIPLES.map((p) => p.title).join(" · "),
          },
          {
            href: "#/where",
            eyebrow: "Where it runs",
            title: "In your browser, or on your desktop.",
            body: "Same app, same rules, wherever you open it.",
          },
        ].map((card, i) => (
          <a
            key={card.href}
            href={card.href}
            data-reveal
            style={{ "--d": `${i * 90}ms` } as React.CSSProperties}
            className="group flex flex-col justify-between gap-10 rounded-3xl border border-line bg-surface p-8 transition-colors hover:border-line-strong"
          >
            <div>
              <p className={EYEBROW}>{card.eyebrow}</p>
              <h2 className="mt-4 text-2xl font-medium leading-snug tracking-[-0.02em]">{card.title}</h2>
              <p className="mt-3 text-[15px] leading-relaxed text-muted">{card.body}</p>
            </div>
            <span className="inline-flex items-center gap-2 text-sm font-semibold">
              Read more
              <span className="transition-transform duration-200 group-hover:translate-x-1">
                <Icon name="arrow" size={16} />
              </span>
            </span>
          </a>
        ))}
      </section>

      <CtaBand onOpen={onOpen} title="Capture your first thought." />
    </>
  );
}

const NAV: { label: string; href: string; route: SiteRoute | null }[] = [
  { label: "How it works", href: "#how", route: null },
  { label: "Principles", href: "#/principles", route: "principles" },
  { label: "Where it runs", href: "#/where", route: "where" },
];

export function Site({ route, onOpen }: { route: SiteRoute; onOpen: () => void }) {
  // Each page starts at the top and reveals its own blocks as they scroll in.
  useEffect(() => {
    if (route !== "home" || !window.location.hash.startsWith("#how")) window.scrollTo({ top: 0 });
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
  }, [route]);

  return (
    <div className="min-h-dvh bg-canvas text-ink">
      <header className="sticky top-0 z-20 border-b border-line bg-canvas/85 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-6 px-5">
          <a href="#" aria-label="Metis home">
            <Wordmark />
          </a>
          <nav aria-label="Site" className="hidden items-center gap-7 text-sm font-medium md:flex">
            {NAV.map((item) => (
              <a
                key={item.label}
                href={item.href}
                aria-current={item.route === route ? "page" : undefined}
                className={item.route === route ? "text-ink" : "text-muted hover:text-ink"}
              >
                {item.label}
              </a>
            ))}
          </nav>
          <div className="flex items-center gap-2">
            <ThemeToggle className="grid size-10 place-items-center text-muted hover:text-ink" />
            <button
              type="button"
              onClick={onOpen}
              className="inline-flex h-10 items-center rounded-full bg-accent px-5 text-sm font-semibold text-on-accent hover:opacity-85"
            >
              Open Metis
            </button>
          </div>
        </div>
      </header>

      <main key={route}>
        {route === "home" && <HomePage onOpen={onOpen} />}
        {route === "principles" && <PrinciplesPage onOpen={onOpen} />}
        {route === "where" && <WherePage onOpen={onOpen} />}
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-5 py-8 text-sm text-muted">
          <Wordmark />
          <nav aria-label="Footer" className="flex gap-6">
            {NAV.map((item) => (
              <a key={item.label} href={item.href} className="hover:text-ink">
                {item.label}
              </a>
            ))}
          </nav>
        </div>
      </footer>
    </div>
  );
}
