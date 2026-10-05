# ADR 0002 — One on-demand DynamoDB table, mirrored by local SQLite

Date: 2026-10-05. Status: accepted.

## Context

Metis is personal-scale: one user's working set is small, but every access must
be owner-scoped, revision-checked and free of table scans. Local development
must work with no cloud account, and the two stores must not drift in
semantics.

## Decision

One on-demand table. `PK = USER#<authenticated-subject>` for every item;
`SK` is typed:

| Item | SK |
| --- | --- |
| entity | `<KIND>#<id>` |
| revision log entry | `REV#<KIND>#<id>#<rev>` |
| outbox event | `OBX#<id>` |
| outbox dedupe sentinel | `OBXKEY#<event_type>#<idempotency_key>` |
| idempotency record | `IDEM#<key>` (TTL 7 days) |
| occurrence claim | `CLAIM#<key>` (TTL 30 days) |
| user meta / planning epoch | `META` |
| daily usage rollup | `USAGE#<local_date>` |
| budget reservation | `BRES#<id>` |

Indexes:

* `gsi_due`: `gsi1pk = USER#<sub>#<KIND>`, `gsi1sk = <deadline or local_date>`.
  An accelerator only.
* `gsi_outbox`: `obx_state` / `available_at`. The dispatcher's ready queue.

`services/state/src/metis_state/schema.sql` uses `(user_id, kind, id)` for the
same purpose locally, with denormalised query columns in place of the GSIs.

## Access patterns

| Need | Query |
| --- | --- |
| one entity | `GetItem(PK, <KIND>#<id>)`, `ConsistentRead=True` |
| a user's tasks / events / availability | `Query(PK, begins_with(SK, '<KIND>#'))`, `ConsistentRead=True`, paginated |
| change history | `Query(PK, begins_with(SK, 'REV#<KIND>#<id>#'))` descending |
| ready outbox events | `Query(gsi_outbox, obx_state=:s AND available_at <= :now)` |
| today's usage | `GetItem(PK, USAGE#<date>)` |

## Transaction boundaries

One `TransactWriteItems` call carries: the entity `Put` (condition
`revision = :expected`), its `REV#` log entry, any outbox `Put`s with their
dedupe sentinels, and the `META` planning-epoch `ADD`. Nothing is sent until the
`transaction()` block exits cleanly, so a committed entity change always has its
side-effect request. The 100-item limit is enforced explicitly with a clear
error rather than a silent partial write.

## Consistency rules this pins down

* `gsi_due` is eventually consistent, so it never *defines* the planning
  obligation set. The planner paginates the authoritative `<KIND>#` range with
  `ConsistentRead=True` and filters current state in code.
* Pagination stops at a configured page budget (`METIS_PLAN_READ_PAGES`). When
  it truncates, the page is returned with `complete=False` and the plan may not
  claim complete coverage.
* `planning_epoch` is read before and after snapshot assembly; a change retries
  within `METIS_PLAN_EPOCH_RETRIES`.
* TTL is cleanup only. It is never the timer for a reminder or a budget release.

## Consequences

* No table scan exists in either adapter.
* A revision conflict and a duplicate idempotency key both surface as `409`;
  the caller re-reads the stored result, which is correct for both.
* Cost is one on-demand table plus two GSIs — no provisioned capacity and no
  idle database compute.
