# Backend job prompt group

Do not dispatch this file as one broad assignment. It is an ordering guide for
the path-safe prompts beside it:

1. The coordinator publishes required contract/config/port changes.
2. `22e-state-port-cleanup.md` may run with `22a-export-api.md` only if their
   exact API files do not overlap; otherwise serialize them.
3. `22a-export-api.md` and `22c-daily-scheduler.md` can run concurrently.
4. Integrate the export API, then run `22b-export-worker.md`.
5. After no other agent is writing `services/api`, run
   `22d-daily-planning-api.md`.
6. Run the targeted service suites and then the full local gate.

The split preserves the repository's named ownership boundaries and prevents
two agents from editing `services/api` simultaneously.
