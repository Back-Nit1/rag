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
* Projects / resume, plan generation and regeneration, memory search and the
  export/delete confirmation flow are not built. Settings says so on the page
  rather than showing dead controls.
* Push is display-only: the service worker shows a push and the app can ask for
  permission, but `POST /v1/push-subscriptions` is not called yet (no VAPID key
  is exposed to the client).
* No component-level DOM tests; the logic is tested instead (`src/lib/*.test.ts`)
  and the UI was verified in a real browser.
