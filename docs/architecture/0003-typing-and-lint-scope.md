# ADR 0003 — mypy --strict covers production sources, not tests

Date: 2026-10-05. Status: accepted.

## Context

`make check` runs `mypy packages services` under `strict = true`. At the
coordinator-01 freeze the run reported 503 errors in 40 files. 400 of those
were in `*/tests/**`: missing annotations on fixture builders and test
functions (`no-untyped-def`, `no-untyped-call`) rather than anything about
behaviour. The remaining 103 were in production code and included four call
sites raising a non-existent `ErrorCode` member, a protocol whose declared
return type did not match either implementation, and a `Job` built with a
`JobState` where an `AdapterMode` belongs.

Two ways to make the gate meaningful: annotate every test helper, or scope the
typecheck. The question had to be settled before workers branch, because
whatever the base does is what five parallel workers will copy.

## Decision

`exclude` gains `/tests/`, so `mypy packages services` checks production
sources only. Production strictness is unchanged: no rule disabled, no module
made permissive, no `ignore_errors`, and the 103 production errors were fixed
rather than configured away.

Three narrow exceptions stay, each justified in `pyproject.toml`:

* `ignore_missing_imports` for `faster_whisper`, `av`, `mangum`, `numpy` and
  `pywebpush` — optional dependencies imported lazily inside the adapter that
  needs them, with no published stubs.
* `disallow_untyped_decorators = false` — FastAPI's route decorators.
* Ruff `T20` per file for `metis_speech.worker`, `metis_speech.evaluate`,
  `generate.py`, `local_worker.py` and `scripts/` — processes whose
  stdout/stderr *is* the interface. Ruff's T201 autofix deletes the print
  statement, which in `worker.py` would have removed the job-result JSON its
  caller parses.

`py.typed` is present in all eight packages. Without it, a worker running mypy
on only their own service saw every `metis_*` import as untyped and got no
checking at all.

## Consequences

* Tests are verified by pytest, not by mypy. A type error reachable only from a
  test is caught as a test failure.
* A worker can run `uv run mypy services/<theirs>` and get a clean,
  fast signal scoped to their own paths.
* Re-including tests later is one line, and the annotation debt is bounded to
  test files. Revisit if a test-only type error ever causes a production
  misunderstanding.
* Typing the adapter seams (`Adapters`, `DispatchContext`,
  `RequestContext.state`) against the ports is now load-bearing: it is what
  makes protocol drift a type error. Replacing any of them with `Any` re-opens
  every class of bug this ADR's fixes closed.
