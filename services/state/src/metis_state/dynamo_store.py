"""DynamoDB implementation of :class:`metis_contracts.ports.StateStore`.

Table design (one on-demand table, documented in
``docs/architecture/0002-single-table-dynamodb.md``):

| Item                | PK                  | SK                        |
|---------------------|---------------------|---------------------------|
| entity              | ``USER#<sub>``      | ``<KIND>#<id>``           |
| revision log entry  | ``USER#<sub>``      | ``REV#<KIND>#<id>#<rev>`` |
| outbox event        | ``USER#<sub>``      | ``OBX#<id>``              |
| idempotency record  | ``USER#<sub>``      | ``IDEM#<key>``            |
| occurrence claim    | ``USER#<sub>``      | ``CLAIM#<key>``           |
| user meta / epoch   | ``USER#<sub>``      | ``META``                  |
| daily usage rollup  | ``USER#<sub>``      | ``USAGE#<local_date>``    |
| budget reservation  | ``USER#<sub>``      | ``BRES#<id>``             |

Indexes:

* ``gsi_due`` (``gsi1pk`` = ``USER#<sub>#TASK``, ``gsi1sk`` = deadline date) —
  accelerates deadline queries. It is eventually consistent, so it never
  *defines* the planning obligation set; the planner paginates the authoritative
  ``<KIND>#`` range with ``ConsistentRead`` and filters in code.
* ``gsi_outbox`` (``obx_state``, ``available_at``) — the dispatcher's ready
  queue, scanned per state partition rather than over the whole table.

Every read is pinned to one ``USER#<sub>`` partition; there is no code path that
scans the table to find an id.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from metis_contracts import (
    AdapterMode,
    ErrorCode,
    Event,
    JobState,
    MetisError,
    OutboxEvent,
    Page,
    Preferences,
    SystemClock,
    Task,
    TaskState,
    UsageDay,
    new_id,
    settings,
)
from metis_contracts.entities import ENTITY_MODELS, AvailabilityBlock, Job, Notification, Reminder

from .sqlite_store import PLANNING_RELEVANT_KINDS

if TYPE_CHECKING:  # pragma: no cover
    from mypy_boto3_dynamodb.service_resource import Table

MAX_TRANSACT_ITEMS = 100  # DynamoDB TransactWriteItems hard limit


def _pk(user_id: str) -> str:
    return f"USER#{user_id}"


def _sk(kind: str, entity_id: str) -> str:
    return f"{kind.upper()}#{entity_id}"


def _to_dynamo(value: Any) -> Any:
    """DynamoDB has no float type; JSON round-tripping keeps one representation."""
    return json.loads(json.dumps(value), parse_float=Decimal)


def _from_dynamo(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    return value


class _Tx:
    """Collects items for one ``TransactWriteItems`` call.

    Nothing is sent until the context manager exits cleanly, so an entity
    change can never be committed without its outbox event.
    """

    def __init__(self, store: DynamoStateStore, user_id: str) -> None:
        self._store = store
        self.user_id = user_id
        self.items: list[dict[str, Any]] = []
        self._epoch_bumped = False
        self._results: list[tuple[str, Any]] = []

    def put(self, kind: str, record: Any, *, expected_revision: int | None = None) -> Any:
        if record.user_id != self.user_id:
            raise MetisError(ErrorCode.FORBIDDEN, "record owner does not match the transaction owner")
        now = self._store._clock.now()
        new_revision = 1 if expected_revision in (None, 0) else expected_revision + 1
        created_at = record.created_at or now
        stored = record.model_copy(
            update={"revision": new_revision, "created_at": created_at, "updated_at": now}
        )
        body = stored.model_dump(mode="json")
        item: dict[str, Any] = {
            "pk": _pk(self.user_id),
            "sk": _sk(kind, stored.id),
            "kind": kind,
            "entity_id": stored.id,
            "revision": new_revision,
            "updated_at": now.isoformat(),
            "body": _to_dynamo(body),
        }
        if deadline := body.get("deadline_local_date"):
            item["gsi1pk"] = f"{_pk(self.user_id)}#{kind.upper()}"
            item["gsi1sk"] = str(deadline)
        elif local_date := body.get("local_date"):
            item["gsi1pk"] = f"{_pk(self.user_id)}#{kind.upper()}"
            item["gsi1sk"] = str(local_date)

        if expected_revision in (None, 0):
            condition = "attribute_not_exists(pk) AND attribute_not_exists(sk)"
            values: dict[str, Any] = {}
        else:
            condition = "revision = :expected AND attribute_not_exists(tombstoned_at)"
            values = {":expected": expected_revision}

        self.items.append(
            {
                "Put": {
                    "TableName": self._store.table_name,
                    "Item": item,
                    "ConditionExpression": condition,
                    **({"ExpressionAttributeValues": values} if values else {}),
                }
            }
        )
        # Immutable revision-log entry, written in the same transaction.
        self.items.append(
            {
                "Put": {
                    "TableName": self._store.table_name,
                    "Item": {
                        "pk": _pk(self.user_id),
                        "sk": f"REV#{kind.upper()}#{stored.id}#{new_revision:010d}",
                        "kind": "revision",
                        "recorded_at": now.isoformat(),
                        "body": _to_dynamo(body),
                    },
                }
            }
        )
        if kind in PLANNING_RELEVANT_KINDS:
            self.bump_planning_epoch(self.user_id)
        self._results.append((kind, stored))
        return stored

    def append_outbox(self, event: OutboxEvent) -> None:
        if event.user_id != self.user_id:
            raise MetisError(ErrorCode.FORBIDDEN, "outbox event owner mismatch")
        body = event.model_dump(mode="json")
        self.items.append(
            {
                "Put": {
                    "TableName": self._store.table_name,
                    "Item": {
                        "pk": _pk(self.user_id),
                        "sk": f"OBX#{event.id}",
                        "kind": "outbox",
                        "obx_state": event.state.value if hasattr(event.state, "value") else str(event.state),
                        "available_at": event.available_at.isoformat(),
                        "dedupe": f"{event.event_type}#{event.idempotency_key}",
                        "body": _to_dynamo(body),
                    },
                    # Dedupe sentinel: a retry with the same (type, key) is a
                    # no-op rather than a second logical side effect.
                    "ConditionExpression": "attribute_not_exists(sk)",
                }
            }
        )
        self.items.append(
            {
                "Put": {
                    "TableName": self._store.table_name,
                    "Item": {
                        "pk": _pk(self.user_id),
                        "sk": f"OBXKEY#{event.event_type}#{event.idempotency_key}",
                        "kind": "outbox_key",
                        "outbox_id": event.id,
                    },
                    "ConditionExpression": "attribute_not_exists(sk)",
                }
            }
        )

    def bump_planning_epoch(self, user_id: str) -> int:
        if self._epoch_bumped:
            return -1  # already included in this transaction
        self._epoch_bumped = True
        self.items.append(
            {
                "Update": {
                    "TableName": self._store.table_name,
                    "Key": {"pk": _pk(user_id), "sk": "META"},
                    "UpdateExpression": "ADD planning_epoch :one SET updated_at = :now",
                    "ExpressionAttributeValues": {
                        ":one": 1,
                        ":now": self._store._clock.now().isoformat(),
                    },
                }
            }
        )
        return -1

    def remember_idempotent(self, key: str, response: dict[str, Any]) -> None:
        self.items.append(
            {
                "Put": {
                    "TableName": self._store.table_name,
                    "Item": {
                        "pk": _pk(self.user_id),
                        "sk": f"IDEM#{key}",
                        "kind": "idempotency",
                        "response": _to_dynamo(response),
                        "created_at": self._store._clock.now().isoformat(),
                        "ttl": int((self._store._clock.now() + timedelta(days=7)).timestamp()),
                    },
                    "ConditionExpression": "attribute_not_exists(sk)",
                }
            }
        )


class DynamoStateStore:
    """Cloud authoritative state. Same contract as :class:`SqliteStateStore`."""

    mode = AdapterMode.CLOUD

    def __init__(self, *, table_name: str, region: str | None = None, clock: Any | None = None) -> None:
        import boto3  # imported lazily so local mode needs no boto3

        self.table_name = table_name
        self._clock = clock or SystemClock()
        self._ddb = boto3.resource("dynamodb", region_name=region or settings().aws.region)
        self._client = self._ddb.meta.client
        self._table: Table = self._ddb.Table(table_name)

    # ------------------------------------------------------------------ #
    # transaction
    # ------------------------------------------------------------------ #

    @contextmanager
    def transaction(self, user_id: str) -> Iterator[_Tx]:
        tx = _Tx(self, user_id)
        yield tx
        if not tx.items:
            return
        if len(tx.items) > MAX_TRANSACT_ITEMS:
            raise MetisError(
                ErrorCode.VALIDATION_FAILED,
                f"transaction has {len(tx.items)} items, over the {MAX_TRANSACT_ITEMS} limit; "
                "split the change into smaller units of work",
            )
        try:
            self._client.transact_write_items(TransactItems=tx.items)
        except self._client.exceptions.TransactionCanceledException as exc:
            reasons = [r.get("Code") for r in exc.response.get("CancellationReasons", [])]
            if "ConditionalCheckFailed" in reasons:
                # Either a revision conflict or a duplicate idempotency/outbox
                # key. Both are safe to surface as a conflict: the caller
                # reloads and re-reads the stored result.
                raise MetisError(
                    ErrorCode.REVISION_CONFLICT,
                    "a concurrent change or duplicate request was detected",
                    details={"reasons": ",".join(r or "" for r in reasons)},
                ) from exc
            raise

    # ------------------------------------------------------------------ #
    # reads
    # ------------------------------------------------------------------ #

    def _hydrate(self, kind: str, item: dict[str, Any]) -> Any:
        return ENTITY_MODELS[kind].model_validate(_from_dynamo(item["body"]))

    def get(self, user_id: str, kind: str, entity_id: str) -> Any | None:
        resp = self._table.get_item(
            Key={"pk": _pk(user_id), "sk": _sk(kind, entity_id)}, ConsistentRead=True
        )
        item = resp.get("Item")
        if not item or item.get("tombstoned_at"):
            return None
        return self._hydrate(kind, item)

    def _query_kind(
        self, user_id: str, kind: str, *, cursor: str | None, limit: int, page_limit: int | None = None
    ) -> tuple[list[Any], str | None, bool]:
        """Paginate the authoritative ``<KIND>#`` range with strong consistency.

        Returns ``(items, next_cursor, complete)``. ``complete`` is ``False``
        when the configured page budget was reached before the range ended — the
        caller must then not claim complete coverage.
        """
        bounded = max(1, min(limit, 500))
        budget = page_limit or settings().limits.planning_read_page_limit
        items: list[Any] = []
        start_key: dict[str, Any] | None = {"pk": _pk(user_id), "sk": cursor} if cursor else None
        pages = 0
        while True:
            kwargs: dict[str, Any] = {
                "KeyConditionExpression": "pk = :pk AND begins_with(sk, :prefix)",
                "ExpressionAttributeValues": {":pk": _pk(user_id), ":prefix": f"{kind.upper()}#"},
                "ConsistentRead": True,
                "Limit": bounded,
            }
            if start_key:
                kwargs["ExclusiveStartKey"] = start_key
            resp = self._table.query(**kwargs)
            for item in resp.get("Items", []):
                if item.get("tombstoned_at"):
                    continue
                items.append(self._hydrate(kind, item))
            start_key = resp.get("LastEvaluatedKey")
            pages += 1
            if not start_key or len(items) >= bounded or pages >= budget:
                break
        next_cursor = start_key["sk"] if start_key else None
        complete = start_key is None
        return items[:bounded], next_cursor, complete

    def list_tasks(
        self,
        user_id: str,
        *,
        states: Sequence[TaskState] | None = None,
        project_id: str | None = None,
        due_before: date | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> Page[Task]:
        items, next_cursor, complete = self._query_kind(user_id, "task", cursor=cursor, limit=limit)
        wanted = {s.value if hasattr(s, "value") else str(s) for s in states} if states else None
        filtered = [
            t
            for t in items
            if (wanted is None or t.state.value in wanted)
            and (project_id is None or t.project_id == project_id)
            and (due_before is None or (t.deadline_local_date and t.deadline_local_date <= due_before))
        ]
        return Page(items=filtered, next_cursor=next_cursor, complete=complete)

    def list_events(
        self,
        user_id: str,
        *,
        local_date: date | None = None,
        until: date | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> Page[Event]:
        items, next_cursor, complete = self._query_kind(user_id, "event", cursor=cursor, limit=limit)
        lo, hi = local_date, until or local_date
        filtered = [
            e for e in items if lo is None or (e.local_date and lo <= e.local_date <= (hi or lo))
        ]
        return Page(items=filtered, next_cursor=next_cursor, complete=complete)

    def list_availability(self, user_id: str, local_date: date) -> list[AvailabilityBlock]:
        items, _, _ = self._query_kind(user_id, "availability", cursor=None, limit=200)
        return [a for a in items if a.local_date == local_date]

    def list_notifications(
        self, user_id: str, *, cursor: str | None = None, limit: int = 50
    ) -> Page[Notification]:
        items, next_cursor, complete = self._query_kind(user_id, "notification", cursor=cursor, limit=limit)
        return Page(items=items, next_cursor=next_cursor, complete=complete)

    def list_memories(
        self, user_id: str, *, project_id: str | None = None, cursor: str | None = None, limit: int = 100
    ) -> Page[Any]:
        items, next_cursor, complete = self._query_kind(user_id, "memory", cursor=cursor, limit=limit)
        filtered = [m for m in items if project_id is None or m.project_id == project_id]
        return Page(items=filtered, next_cursor=next_cursor, complete=complete)

    def list_reminders_for_target(self, user_id: str, target_id: str) -> list[Reminder]:
        items, _, _ = self._query_kind(user_id, "reminder", cursor=None, limit=200)
        return [r for r in items if r.target_id == target_id]

    def get_preferences(self, user_id: str) -> Preferences:
        existing = self.get(user_id, "preferences", f"pref_{user_id}")
        if existing is not None:
            return existing
        now = self._clock.now()
        return Preferences(
            id=f"pref_{user_id}", user_id=user_id, created_at=now, updated_at=now,
            timezone=settings().default_timezone,
        )

    def get_planning_epoch(self, user_id: str) -> int:
        resp = self._table.get_item(Key={"pk": _pk(user_id), "sk": "META"}, ConsistentRead=True)
        item = resp.get("Item") or {}
        return int(item.get("planning_epoch", 0))

    def get_revisions(self, user_id: str, kind: str, entity_id: str) -> list[dict[str, Any]]:
        resp = self._table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :prefix)",
            ExpressionAttributeValues={":pk": _pk(user_id), ":prefix": f"REV#{kind.upper()}#{entity_id}#"},
            ScanIndexForward=False,
            Limit=50,
        )
        return [
            {"revision": int(i["sk"].rsplit("#", 1)[-1]), "recorded_at": i["recorded_at"]}
            for i in resp.get("Items", [])
        ]

    # ------------------------------------------------------------------ #
    # outbox
    # ------------------------------------------------------------------ #

    def claim_outbox(self, *, owner: str, limit: int, lease_seconds: int) -> list[OutboxEvent]:
        """Lease ready events from the dispatcher index with a conditional update."""
        now = self._clock.now()
        expires = now + timedelta(seconds=lease_seconds)
        claimed: list[OutboxEvent] = []
        for state in ("pending", "failed", "leased"):
            if len(claimed) >= limit:
                break
            resp = self._table.query(
                IndexName="gsi_outbox",
                KeyConditionExpression="obx_state = :s AND available_at <= :now",
                ExpressionAttributeValues={":s": state, ":now": now.isoformat()},
                Limit=limit - len(claimed),
            )
            for item in resp.get("Items", []):
                event = OutboxEvent.model_validate(_from_dynamo(item["body"]))
                if event.lease_expires_at and event.lease_expires_at > now and event.lease_owner != owner:
                    continue
                updated = event.model_copy(
                    update={
                        "state": "leased",
                        "lease_owner": owner,
                        "lease_expires_at": expires,
                        "attempts": event.attempts + 1,
                        "updated_at": now,
                    }
                )
                try:
                    self._table.update_item(
                        Key={"pk": _pk(event.user_id), "sk": f"OBX#{event.id}"},
                        UpdateExpression=(
                            "SET body = :body, obx_state = :leased, lease_owner = :owner, "
                            "lease_expires_at = :exp"
                        ),
                        ConditionExpression=(
                            "attribute_not_exists(lease_expires_at) OR lease_expires_at <= :now "
                            "OR lease_owner = :owner"
                        ),
                        ExpressionAttributeValues={
                            ":body": _to_dynamo(updated.model_dump(mode="json")),
                            ":leased": "leased",
                            ":owner": owner,
                            ":exp": expires.isoformat(),
                            ":now": now.isoformat(),
                        },
                    )
                except self._client.exceptions.ConditionalCheckFailedException:
                    continue  # another dispatcher won it
                claimed.append(updated)
        return claimed

    def complete_outbox(
        self,
        event_id: str,
        *,
        user_id: str,
        state: str,
        failure: str | None = None,
        error: str | None = None,
        retry_in_seconds: int | None = None,
    ) -> None:
        """``failure`` is a FailureCategory value; ``error`` is the readable detail."""
        now = self._clock.now()
        available_at = now + timedelta(seconds=retry_in_seconds or 0)
        self._table.update_item(
            Key={"pk": _pk(user_id), "sk": f"OBX#{event_id}"},
            UpdateExpression=(
                "SET obx_state = :s, available_at = :avail, body.#st = :s, "
                "body.failure = :cat, body.error_message = :err "
                "REMOVE lease_owner, lease_expires_at"
            ),
            ExpressionAttributeNames={"#st": "state"},
            ExpressionAttributeValues={
                ":s": state,
                ":avail": available_at.isoformat(),
                ":cat": failure,
                ":err": error,
            },
        )

    def list_dead_letters(self, limit: int = 50) -> list[OutboxEvent]:
        resp = self._table.query(
            IndexName="gsi_outbox",
            KeyConditionExpression="obx_state = :s",
            ExpressionAttributeValues={":s": "dead_letter"},
            Limit=limit,
        )
        return [OutboxEvent.model_validate(_from_dynamo(i["body"])) for i in resp.get("Items", [])]

    # ------------------------------------------------------------------ #
    # jobs
    # ------------------------------------------------------------------ #

    def claim_job(self, job_id: str, *, user_id: str, owner: str, lease_seconds: int) -> Job | None:
        job = self.get(user_id, "job", job_id)
        if job is None:
            return None
        now = self._clock.now()
        if job.lease_expires_at and job.lease_expires_at > now and job.lease_owner != owner:
            return None
        leased = job.model_copy(
            update={"lease_owner": owner, "lease_expires_at": now + timedelta(seconds=lease_seconds)}
        )
        try:
            with self.transaction(user_id) as tx:
                return tx.put("job", leased, expected_revision=job.revision)
        except MetisError as exc:
            if exc.code is ErrorCode.REVISION_CONFLICT:
                return None
            raise

    def set_job_state(
        self,
        job_id: str,
        *,
        user_id: str,
        state: JobState,
        failure: str | None = None,
        error_message: str | None = None,
        result_refs: dict[str, str] | None = None,
        metrics: dict[str, float] | None = None,
    ) -> Job:
        job = self.get(user_id, "job", job_id)
        if job is None:
            raise MetisError(ErrorCode.NOT_FOUND, f"job {job_id} not found")
        now = self._clock.now()
        update: dict[str, Any] = {"state": state}
        if failure:
            update["failure"] = failure
        if error_message:
            update["error_message"] = error_message[:1000]
        if result_refs:
            update["result_refs"] = {**job.result_refs, **result_refs}
        if metrics:
            update["metrics"] = {**job.metrics, **metrics}
        if state in (JobState.READY, JobState.FAILED, JobState.CANCELED):
            update.update({"finished_at": now, "lease_owner": None, "lease_expires_at": None})
        elif job.started_at is None:
            update["started_at"] = now
        with self.transaction(user_id) as tx:
            return tx.put("job", job.model_copy(update=update), expected_revision=job.revision)

    # ------------------------------------------------------------------ #
    # idempotency / claims
    # ------------------------------------------------------------------ #

    def remember_idempotent(self, user_id: str, key: str, response: dict[str, Any]) -> None:
        try:
            self._table.put_item(
                Item={
                    "pk": _pk(user_id),
                    "sk": f"IDEM#{key}",
                    "kind": "idempotency",
                    "response": _to_dynamo(response),
                    "created_at": self._clock.now().isoformat(),
                    "ttl": int((self._clock.now() + timedelta(days=7)).timestamp()),
                },
                ConditionExpression="attribute_not_exists(sk)",
            )
        except self._client.exceptions.ConditionalCheckFailedException:
            pass  # first response wins

    def recall_idempotent(self, user_id: str, key: str) -> dict[str, Any] | None:
        resp = self._table.get_item(Key={"pk": _pk(user_id), "sk": f"IDEM#{key}"}, ConsistentRead=True)
        item = resp.get("Item")
        return _from_dynamo(item["response"]) if item else None

    def claim_once(self, user_id: str, claim_key: str, owner: str) -> bool:
        """Single-winner conditional claim. TTL is cleanup, never the timer."""
        try:
            self._table.put_item(
                Item={
                    "pk": _pk(user_id),
                    "sk": f"CLAIM#{claim_key}",
                    "kind": "claim",
                    "owner": owner,
                    "claimed_at": self._clock.now().isoformat(),
                    "ttl": int((self._clock.now() + timedelta(days=30)).timestamp()),
                },
                ConditionExpression="attribute_not_exists(sk)",
            )
            return True
        except self._client.exceptions.ConditionalCheckFailedException:
            return False

    # ------------------------------------------------------------------ #
    # budget
    # ------------------------------------------------------------------ #

    def reserve_budget(
        self, user_id: str, *, local_date: date, micro_usd: int, operation: str, correlation_id: str
    ) -> str:
        """Atomic reserve-then-check using a conditional ADD.

        The condition is evaluated server-side against the stored counters, so
        two concurrent workers cannot both observe the same remaining budget.
        """
        cap = settings().limits.daily_micro_usd
        now = self._clock.now()
        reservation_id = new_id("bres")
        try:
            self._table.update_item(
                Key={"pk": _pk(user_id), "sk": f"USAGE#{local_date.isoformat()}"},
                UpdateExpression=(
                    "ADD reserved_micro_usd :amt, model_calls :one SET updated_at = :now"
                ),
                ConditionExpression=(
                    "attribute_not_exists(reserved_micro_usd) OR "
                    "reserved_micro_usd + if_not_exists(settled_micro_usd, :zero) <= :headroom"
                ),
                ExpressionAttributeValues={
                    ":amt": micro_usd,
                    ":one": 1,
                    ":zero": 0,
                    ":headroom": cap - micro_usd,
                    ":now": now.isoformat(),
                },
            )
        except self._client.exceptions.ConditionalCheckFailedException as exc:
            raise MetisError(
                ErrorCode.BUDGET_EXCEEDED,
                "daily budget would be exceeded",
                details={"operation": operation, "cap_micro_usd": str(cap)},
            ) from exc
        self._table.put_item(
            Item={
                "pk": _pk(user_id),
                "sk": f"BRES#{reservation_id}",
                "kind": "budget_reservation",
                "local_date": local_date.isoformat(),
                "operation": operation,
                "reserved_micro_usd": micro_usd,
                "state": "reserved",
                "correlation_id": correlation_id,
                "created_at": now.isoformat(),
            }
        )
        return reservation_id

    def settle_budget(
        self,
        reservation_id: str,
        *,
        user_id: str,
        actual_micro_usd: int | None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        uncertain: bool = False,
    ) -> None:
        key = {"pk": _pk(user_id), "sk": f"BRES#{reservation_id}"}
        resp = self._table.get_item(Key=key, ConsistentRead=True)
        item = resp.get("Item")
        if not item or item.get("state") != "reserved":
            return  # idempotent
        reserved = int(item["reserved_micro_usd"])
        if uncertain:
            state, charge = "retained_uncertain", reserved
        elif actual_micro_usd is None:
            state, charge = "released", 0
        else:
            state, charge = "settled", actual_micro_usd
        try:
            self._table.update_item(
                Key=key,
                UpdateExpression=(
                    "SET #st = :st, actual_micro_usd = :actual, input_tokens = :in, "
                    "output_tokens = :out, settled_at = :now"
                ),
                ConditionExpression="#st = :reserved",
                ExpressionAttributeNames={"#st": "state"},
                ExpressionAttributeValues={
                    ":st": state,
                    ":actual": charge,
                    ":in": input_tokens,
                    ":out": output_tokens,
                    ":now": self._clock.now().isoformat(),
                    ":reserved": "reserved",
                },
            )
        except self._client.exceptions.ConditionalCheckFailedException:
            return  # someone else settled it first
        self._table.update_item(
            Key={"pk": _pk(user_id), "sk": f"USAGE#{item['local_date']}"},
            UpdateExpression="ADD reserved_micro_usd :neg, settled_micro_usd :charge SET updated_at = :now",
            ExpressionAttributeValues={
                ":neg": -reserved,
                ":charge": charge,
                ":now": self._clock.now().isoformat(),
            },
        )

    def record_speech_usage(self, user_id: str, *, local_date: date, audio_seconds: float) -> None:
        self._table.update_item(
            Key={"pk": _pk(user_id), "sk": f"USAGE#{local_date.isoformat()}"},
            UpdateExpression="ADD audio_seconds :secs, speech_jobs :one SET updated_at = :now",
            ExpressionAttributeValues={
                ":secs": Decimal(str(audio_seconds)),
                ":one": 1,
                ":now": self._clock.now().isoformat(),
            },
        )

    def get_usage(self, user_id: str, local_date: date) -> UsageDay:
        now = self._clock.now()
        resp = self._table.get_item(
            Key={"pk": _pk(user_id), "sk": f"USAGE#{local_date.isoformat()}"}, ConsistentRead=True
        )
        item = {k: _from_dynamo(v) for k, v in (resp.get("Item") or {}).items()}
        return UsageDay(
            id=f"usage_{user_id}_{local_date.isoformat()}",
            user_id=user_id,
            created_at=now,
            updated_at=now,
            local_date=local_date,
            reserved_micro_usd=max(0, int(item.get("reserved_micro_usd", 0))),
            settled_micro_usd=max(0, int(item.get("settled_micro_usd", 0))),
            model_calls=int(item.get("model_calls", 0)),
            audio_seconds=float(item.get("audio_seconds", 0.0)),
            speech_jobs=int(item.get("speech_jobs", 0)),
        )

    # ------------------------------------------------------------------ #
    # deletion
    # ------------------------------------------------------------------ #

    def tombstone(self, user_id: str, kind: str, entity_id: str) -> None:
        self._table.update_item(
            Key={"pk": _pk(user_id), "sk": _sk(kind, entity_id)},
            UpdateExpression="SET tombstoned_at = :now",
            ExpressionAttributeValues={":now": self._clock.now().isoformat()},
        )

    def purge(self, user_id: str, kind: str, entity_id: str) -> None:
        self._table.delete_item(Key={"pk": _pk(user_id), "sk": _sk(kind, entity_id)})

    def due_reminder_ids(self, *, now: datetime, limit: int = 50) -> list[str]:
        """Not used in cloud mode: EventBridge Scheduler invokes the due handler
        directly. Returning an empty list keeps the port total."""
        return []
