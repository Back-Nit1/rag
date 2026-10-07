# apps/web — handoff and coordinator requests

Frontend worker. Allowed paths: `apps/web/`. Everything below that touches a
path this worker does not own is a **request**, not a change.

## State

The capture slice runs end to end against the local API with fixture adapters:

```
make api & make worker &        # or: make dev   (now also starts the web client)
pnpm --filter @metis/web dev    # http://localhost:5173
```

Verified in a browser against the running local stack on 2026-10-05: typed
capture → extraction proposal → per-item edit of the missing fields →
`Scheduling reminder` → `Reminder scheduled` after the scheduler confirmed →
task/event/reminder records → in-app notification feed.

## Coordinator requests

### 1. The TypeScript generator emits invalid identifiers (blocking)

`packages/contracts/generated/metis.ts` declares

```ts
export interface Page[Event] { … }
```

for each `Page[T]` alias in `api.py`. That is not valid TypeScript, so the
generated file cannot be parsed at all. The generator skips the bare `Page`
but not its parameterisations.

Requested fix in `metis_contracts/generate.py`: emit the alias names already
defined in `api.py` (`TaskPage`, `EventPage`, `NotificationPage`,
`ReminderPage`, `ProjectPage`, `CapturePage`) — e.g. map
`Page[Task] -> TaskPage` when collecting models, or sanitise `[`/`]` out of
the emitted name.

Interim: `apps/web/src/contracts/metis.ts` carries exactly that rename. It is
the file `make contracts` overwrites, so **the next `make contracts` run
re-breaks the web build** until the generator is fixed.

### 2. A contract model named `Record` shadows TypeScript's `Record<K, V>`

`common.Record` is emitted as `export interface Record { … }` in the same file
that uses `Record<string, string>` for mapping fields, so every mapping type in
the generated file resolves to the contract entity and fails to compile.

Requested fix: emit mapping fields as an index signature
(`{ [key: string]: string }`) rather than `Record<…>`, or rename the base model.
Interim: the same index-signature rewrite is applied in the web copy, with the
same overwrite risk as above.

### 3. Root lockfile

`apps/web/package.json` is new, so `pnpm install` updated the root
`pnpm-lock.yaml`. That file is coordinator-owned and is **left uncommitted**
for review. Added, all pinned by caret/tilde:

| package | version | why |
| --- | --- | --- |
| react, react-dom | ^19.3.0 | the stack named in the spec |
| vite | ^8.3.2 | build/dev server |
| @vitejs/plugin-react | ^6.1.2 | required by vite 8 |
| tailwindcss, @tailwindcss/vite | ^4.3.3 | the spec's CSS system, no second UI kit |
| typescript | ~5.9.3 | infra pins ~5.6.3; web stays on 5.x, not the 7.x rewrite |
| vitest | ^5.0.3 | unit tests, node environment |
| @types/react, @types/react-dom | ^19.3.0 | types only |

No ESLint: `tsc --noEmit` is the lint gate, which keeps `make check` honest
without seven more dev dependencies.

## Known gaps in this build

* Audio capture is implemented (MediaRecorder, pause/cancel/stop, duration and
  limit) but has only been exercised through unit tests — a real microphone
  recording has not been run in this environment.
* The offline outbox holds typed captures only. Audio stays in memory for an
  explicit retry and is never written to localStorage.
* Push registration is not built: no route exposes a VAPID public key (see Product gaps).
* No component-level DOM tests; the logic is tested instead (`src/lib/*.test.ts`)
  and the UI was verified in a real browser.

## UI overhaul (ui/10-ui-overhaul)

Visual system and rationale live in the header comment of `src/styles.css`
(cool-neutral paper, one cobalt accent, record-red only for the live mic,
serif for notes/titles, system fonts only so desktop works offline). Shell is a
left rail on wide screens and a tab bar with a raised Capture control on small
ones. No new dependencies, no behaviour or API changes. Not covered: audio
recording was only exercised with Chromium's fake media device; no plan
accept/adjust/regenerate or push-subscription UI exists to restyle.

## Product gaps (web/21-product-gaps)

Built against existing contract v1 routes: Cognito PKCE gate (`lib/auth.ts`,
`app/AuthGate.tsx`; only `VITE_METIS_COGNITO_{DOMAIN,CLIENT_ID,REDIRECT_URI}`;
none set or desktop = no login; partial = refuses to open; tokens in
sessionStorage), plans (request/poll/review/pin/shift/accept/regenerate with
epoch-stale handling), manual availability, projects + resume packet +
timeline, evidence-backed search, source viewer with delete + purge job state,
editable preferences with the daily-plan schedule state, export job with
authenticated Blob download, reminder snooze/cancel.

Backend gaps (not invented around; each flow stops at an honest state):

| Route | Gap |
| --- | --- |
| push | `METIS_VAPID_PUBLIC_KEY` exists in settings but no route/health field exposes it; no browser subscription possible |
| `POST /v1/reminders/{id}/...` | no reschedule route (only snooze/cancel) |
| `GET /v1/reminders` | no list; controls reachable only from a notification's `reminder_id` or a just-created reminder |
| `/v1/availability` | no PATCH/DELETE; edits re-POST the same id, blocks cannot be removed |
| `/v1/projects` | no PATCH (no update/archive); POST needs server-owned fields in the body, so the UI does not create projects; captures cannot be attached to a project |
| purge job | `purge_capture` job stays `queued` after the worker logs the purge, so the UI can never show "purged" |
| evidence | fixture memories cite `span_ref` values (e.g. `span_attempt`) absent from `source.spans`; UI falls back to full text and says so |
| `POST /v1/projects/{id}/resume` | no response model in the contract (UI types it locally as `ResumePacket`); returns no next action/completion criterion |
| `GET /v1/captures` | no list; deleted sources are reachable only via evidence links |
| CORS/auth | local API rejects a Cognito-style bearer (401) as designed; sign-out confirm path could not be exercised in a browser without a real Cognito |

## UI overhaul (web-ui-overhaul)

**Direction: field notebook on a work desk.** Green-grey paper / near-black ink,
one deep-teal accent (mint in dark), record-red reserved for the live mic,
squared radii (2/4/6px). Signature: ruled mono eyebrow section headings and a
margin line on page headers. Tokens and rationale: top of `src/styles.css`.
Light/dark follow the system preference; no theme switch exists, none added.

**Skills:** present and used — `frontend-design` (direction, critique),
`vercel-web-design-guidelines` / `vercel-react-best-practices` (focus, live
regions, tap targets, no layout shift — checked against existing code, no new
patterns needed), `anthropic-webapp-testing` (Playwright not installed here, so
Chrome automation was used instead). Sites skill not invoked.

**Verified (Chrome, dark system theme):** Today/Capture/Inbox/Tasks/Alerts/
Settings at 390 and 768/1440 via same-origin iframes (the window cannot be
resized by the tool); no horizontal overflow on 22 of 24 surface×width checks
(alerts/settings at 1440 not reached before the sweep timed out); typed
capture → transcript review works. Fixed: eyebrow heading wrapping and
clipped time inputs in Settings at 360px.

**Not verified:** light theme visually (contrast computed from tokens only),
real microphone, Projects/Search visual review, screenshots not archived in-repo.

**Dependencies:** none. **Backend blockers:** none.
