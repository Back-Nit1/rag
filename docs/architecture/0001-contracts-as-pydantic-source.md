# ADR 0001 — Pydantic models are the single source of contract truth

Date: 2026-10-05. Status: accepted.

## Context

The specification requires coordinator-owned versioned JSON Schemas plus
TypeScript and Pydantic contracts. Three hand-maintained copies of the same
shapes drift, and drift between the web client and the API is exactly the class
of bug the contracts barrier exists to prevent.

## Decision

`packages/contracts/src/metis_contracts/` holds the only hand-written
definitions. `python -m metis_contracts.generate` emits:

* `packages/contracts/schemas/*.json` — one JSON Schema per model plus a bundle
* `packages/contracts/generated/metis.ts` — enums, event-type union, error/status
  map and an interface per model

Both artifacts are committed. `make contracts-check` (and `make check`) re-runs
the generator with `--check` and fails if the committed artifacts are stale, so
CI proves the three representations agree.

## Consequences

* The web client consumes `metis.ts` with no Python toolchain.
* The generator uses only pydantic and the stdlib — no network fetch and no npm
  codegen dependency, so `make contracts` works offline.
* A breaking change is a coordinator-reviewed edit to the Pydantic models plus a
  schema-version bump; workers cannot fork a second schema because every service
  imports `metis_contracts`.
* The emitted TypeScript is intentionally plain (`string` for instants and
  dates). Branded date types were rejected as unrequested complexity.
