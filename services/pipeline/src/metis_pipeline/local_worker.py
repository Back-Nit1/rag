"""Local worker loop: outbox dispatch plus the local due-reminder timer.

In the cloud these are separate things — a Lambda consumes SQS for the outbox,
and EventBridge Scheduler invokes the due handler directly. Locally one process
polls both so `make dev` needs no AWS account.

    python -m metis_pipeline.local_worker [--once] [--interval 2]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from types import FrameType
from typing import Any

from metis_contracts import SystemClock, settings

log = logging.getLogger("metis.pipeline.worker")

#: Bump when the readiness record's shape changes; the desktop supervisor checks it.
READINESS_PROTOCOL = 1

_stop = False


def _request_stop(signum: int, _frame: FrameType | None) -> None:
    global _stop
    log.info("received signal %s; finishing the current batch then stopping", signum)
    _stop = True


def build_context() -> Any:
    """Wire the adapters for the configured mode. Fixtures stay clearly labelled."""
    from metis_state import build_source_store, build_state_store

    from .dispatcher import DispatchContext
    from .inference import build_inference_client

    clock = SystemClock()
    state_store = build_state_store(clock=clock)
    context = DispatchContext(
        state_store=state_store,
        source_store=build_source_store(clock=clock),
        clock=clock,
        inference=build_inference_client(),
    )

    from .transcription import build_asr_launcher

    context.asr_launcher = build_asr_launcher()  # None outside cloud mode

    try:
        from metis_speech import build_transcriber

        context.transcriber = build_transcriber()
    except ImportError:
        log.warning("metis_speech is not installed; audio captures cannot be transcribed")

    try:
        from metis_scheduler import build_notification_transports, build_schedule_store

        context.schedule_store = build_schedule_store(clock=clock)
        context.notification_transports = build_notification_transports(state_store, clock=clock)
    except ImportError:
        log.warning("metis_scheduler is not installed; reminders will not be dispatched")

    try:
        from metis_retrieval import build_embedder, build_memory_store

        context.memory_store = build_memory_store()
        context.embedder = build_embedder()
    except ImportError:
        log.warning("metis_retrieval is not installed; semantic indexing is disabled")

    return context


def build_registry() -> Any:
    """Merge every service's handlers into one registry.

    Each service owns its own handlers; the worker only composes them, so two
    services can never register the same event type without failing loudly.
    """
    from .dispatcher import HandlerRegistry
    from .handlers import registry as pipeline_registry

    registry = HandlerRegistry()
    registry.merge(pipeline_registry)

    try:
        from metis_scheduler.handlers import registry as scheduler_registry

        registry.merge(scheduler_registry)
    except ImportError:
        pass

    try:
        from metis_retrieval.handlers import registry as retrieval_registry

        registry.merge(retrieval_registry)
    except ImportError:
        pass

    try:
        from metis_planner.handlers import registry as planner_registry

        registry.merge(planner_registry)
    except ImportError:
        pass

    return registry


def emit_readiness() -> None:
    """One JSON line on stdout once adapters and handlers are wired, before the loop.

    Logs go to stderr; stdout carries only this record so a supervisor can read it.
    """
    record = {"event": "started", "service": "worker", "protocol": READINESS_PROTOCOL, "pid": os.getpid()}
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()


def run(*, once: bool = False, interval: float = 2.0) -> int:
    logging.basicConfig(level=settings().log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    from .dispatcher import OutboxDispatcher

    context = build_context()
    registry = build_registry()
    dispatcher = OutboxDispatcher(registry, context)
    log.info(
        "worker started mode=%s handlers=%d worker_id=%s",
        settings().mode.value,
        len(registry.event_types),
        context.worker_id,
    )

    emit_readiness()
    idle_cycles = 0
    while True:
        result = dispatcher.run_once()
        due = _fire_due_reminders(context) + _fire_due_daily_plans(context)
        if result.processed or due:
            idle_cycles = 0
            log.info(
                "batch processed=%d ok=%d superseded=%d retried=%d dead=%d due=%d",
                result.processed,
                result.succeeded,
                result.superseded,
                result.retried,
                result.dead_lettered,
                due,
            )
        else:
            idle_cycles += 1
        if once or _stop:
            return 0
        # Back off while idle so a dev loop is not a busy spin.
        time.sleep(min(interval * (2 if idle_cycles > 5 else 1), 10.0))


def _fire_due_reminders(context: Any) -> int:
    """Local stand-in for EventBridge Scheduler.

    It only *raises* the due event; the scheduler's own handler still performs
    the full freshness and quiet-hours validation, so the local and cloud paths
    exercise the same suppression logic.
    """
    state = context.state_store
    lister = getattr(state, "due_reminder_ids", None)
    if lister is None:
        return 0
    try:
        from metis_scheduler.service import raise_due_event
    except ImportError:
        return 0

    fired = 0
    for composite in lister(now=context.clock.now(), limit=25):
        user_id, _, reminder_id = composite.partition("/")
        if raise_due_event(state, user_id=user_id, reminder_id=reminder_id, clock=context.clock):
            fired += 1
    return fired


def _fire_due_daily_plans(context: Any) -> int:
    """Local stand-in for the EventBridge daily-plan schedule.

    metis_api owns the due logic (freshness, dedupe, plan.requested). The API
    depends on this package, so it is imported lazily here, as the worker does
    for the other services; absent API or schedule store means nothing to do.
    """
    if context.schedule_store is None:
        return 0
    try:
        from metis_api.planning import run_local_daily_due
    except ImportError:
        return 0
    results = run_local_daily_due(context.state_store, context.clock, context.schedule_store)
    return sum(r["enqueued"] for r in results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Metis local worker loop")
    parser.add_argument("--once", action="store_true", help="process one batch then exit")
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between polls")
    args = parser.parse_args(argv)
    return run(once=args.once, interval=args.interval)


if __name__ == "__main__":
    sys.exit(main())
