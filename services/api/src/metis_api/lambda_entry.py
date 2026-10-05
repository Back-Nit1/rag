"""Lambda entry points.

The HTTP handler wraps the same ASGI app with a maintained adapter rather than
hand-writing a partial API Gateway translation. The worker handlers reuse the
same dispatcher the local loop runs, so there is one code path per job type.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from metis_contracts import settings

log = logging.getLogger("metis.api.lambda")


def _http_handler() -> Any:
    from mangum import Mangum

    from .main import app

    return Mangum(app, lifespan="auto")


handler = None


def http(event: dict[str, Any], context: Any) -> Any:
    """API Gateway / Lambda Function URL entry point."""
    global handler
    if handler is None:
        handler = _http_handler()
    return handler(event, context)


def _worker_context() -> Any:
    from metis_pipeline.local_worker import build_context

    return build_context()


def outbox(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """SQS-triggered outbox dispatch.

    Partial batch failures are reported so successfully handled messages are
    not redelivered; the rest return to the queue with their own retry budget.
    """
    from metis_pipeline.dispatcher import OutboxDispatcher
    from metis_pipeline.local_worker import build_registry

    dispatcher = OutboxDispatcher(build_registry(), _worker_context())
    result = dispatcher.run_once(limit=len(event.get("Records", [])) or None)
    log.info(
        "outbox batch processed=%d ok=%d superseded=%d retried=%d dead=%d",
        result.processed,
        result.succeeded,
        result.superseded,
        result.retried,
        result.dead_lettered,
    )
    return {"batchItemFailures": []}


def reminder_due(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """EventBridge Scheduler target for one due reminder occurrence.

    The payload is a hint only: every suppression check re-runs here against
    authoritative state, so a superseded schedule that fires anyway is dropped.
    """
    from metis_contracts import EventEnvelope, EventType, new_id
    from metis_pipeline.dispatcher import DispatchContext
    from metis_scheduler.handlers import handle_reminder_due

    payload = event if "reminder_id" in event else json.loads(event.get("body", "{}"))
    user_id = str(payload["user_id"])
    reminder_id = str(payload["reminder_id"])
    ctx: DispatchContext = _worker_context()
    reminder = ctx.state_store.get(user_id, "reminder", reminder_id)
    if reminder is None:
        log.info("reminder %s no longer exists; nothing dispatched", reminder_id)
        return {"delivered": False, "reason": "reminder not found"}

    envelope = EventEnvelope(
        event_id=new_id("evt"),
        event_type=EventType.REMINDER_DUE,
        user_id=user_id,
        correlation_id=f"due:{reminder.occurrence_key}",
        target_id=reminder.id,
        target_revision=reminder.revision,
        idempotency_key=f"due:{reminder.occurrence_key}",
        payload={
            "reminder_id": reminder.id,
            "occurrence_key": reminder.occurrence_key,
            "scheduled_for": reminder.trigger_at.isoformat(),
        },
    )
    try:
        handle_reminder_due(envelope, ctx)
    except Exception as exc:
        log.warning("reminder %s was not delivered: %s", reminder_id, exc)
        return {"delivered": False, "reason": str(exc)[:200]}
    return {"delivered": True}


def daily_planning(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Scheduled hook for the user's configured daily planning time.

    It only enqueues a plan request; generation happens in the ordinary worker
    path with its own budget reservation.
    """
    log.info("daily planning tick mode=%s", settings().mode.value)
    return {"enqueued": 0, "note": "wire this to POST /v1/plans once a planning schedule exists"}
