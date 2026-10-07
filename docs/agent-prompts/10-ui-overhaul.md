# Prompt: redesign the current Metis UI

```yaml
task_id: web-ui-overhaul
owner: frontend
base_sha: {{BASE_SHA}}
contract_version: 1
allowed_paths: [apps/web]
read_first:
  - CLAUDE.md
  - docs/spec/interface.md
  - docs/spec/product.md
  - apps/web/HANDOFF.md
dependencies: [coordinator-baseline]
```

You own a complete visual and interaction redesign of the existing Metis
React/Vite PWA. The current interface is functionally useful but visually weak
and generic. Make it feel like a deliberate, premium personal thinking tool,
not a template dashboard. Preserve every working behavior and honest-state
rule.

Before editing, inventory available skills. You must read and apply the actual
installed `frontend-design` skill. Also use `web-design-guidelines`,
`vercel-react-best-practices`, and `webapp-testing` if installed. State which
skills are present and how each affects the work. If one is missing, report it
and continue; do not pretend to have used it. Do not invoke Sites—the target is
this existing repository.

## Design direction

Design Metis as a calm, high-signal **voice notebook and daily command desk**:
editorial clarity, compact operational density, tactile capture controls, and
quiet confidence. Establish one coherent visual system before polishing
screens:

- purposeful typography and a clear type scale;
- restrained warm-neutral or cool-neutral surfaces with one unmistakable
  accent and semantic state colors;
- spacing, radius, border, elevation and motion tokens;
- strong hierarchy that works in grayscale;
- distinct fixed events, flexible tasks, unresolved items, evidence and system
  status;
- minimal, purposeful motion that honors reduced-motion preferences.
- deliberate light and dark palettes driven by the system preference, unless
  the integrated product already exposes an explicit theme choice.

Avoid generic AI/SaaS styling: no purple-blue gradient wash, glassmorphism,
decorative blobs, excessive rounded cards, statistic-card grids, emoji as the
primary icon system, or chrome that overwhelms the work. Do not import a second
UI framework. Do not add runtime font/CDN dependencies; desktop mode must work
offline. Any new npm dependency or lockfile change is a coordinator request,
not an edit outside `apps/web`.

## Required workflow

1. Run the current app and inspect every surface at narrow mobile and desktop
   widths. Capture before screenshots when tooling permits.
2. Write a short in-branch design note or code comment describing the chosen
   visual direction, hierarchy and token system. Do not create files outside
   `apps/web`.
3. Rework the shell/navigation and shared primitives first, then Today,
   Capture, Inbox, Tasks/Events, Alerts and Settings.
4. Use realistic fixture-backed content while designing. Empty, loading,
   offline, permission-denied, processing, conflict, failed and budget-limited
   states must look intentional—not like afterthoughts.
5. Preserve unfinished text and audio recovery behavior, source/provenance
   visibility, revision conflicts, and explicit fixture labeling.
6. Improve information architecture and copy where useful, but do not change
   server semantics or invent capabilities.
7. Perform a screenshot-driven visual review after implementation at roughly
   390px, 768px and 1440px widths. Fix clipping, weak hierarchy, awkward empty
   space, dense tap targets and inconsistent states you actually observe.

## Acceptance

- The interface has a recognizable, consistent point of view across every
  existing surface.
- Light and dark presentations both preserve hierarchy and semantic contrast.
- Capture remains the obvious primary action without making typed input second
  class.
- Today clearly separates appointments, focus work, overdue work, backlog and
  plan state.
- Status cannot be communicated by color alone; keyboard focus is obvious.
- No horizontal overflow at supported widths; primary mobile actions remain
  thumb reachable and never cover the transcript editor.
- Async status uses appropriate live regions without producing repetitive
  announcements.
- All existing capture, proposal, clarification, task, event, notification and
  preference behavior remains functional.
- The UI makes fixture mode, missing availability, unsaved/offline content and
  unsupported push explicit.
- No AWS/model credentials or provider SDKs enter the browser bundle.

## Validation

```bash
pnpm --filter @metis/web test
pnpm --filter @metis/web typecheck
pnpm --filter @metis/web build
```

Also complete a real-browser interaction pass and return before/after
screenshots or their workspace paths. If browser automation is unavailable,
document the exact manual checks instead of claiming visual verification.

Commit only explicit files under `apps/web/`. Return the standard handoff plus
the chosen design direction, screenshots, accessibility findings, new
dependency requests, and any backend behavior that prevented a complete UI.
