"""E2E harness: real FastAPI app in-process, fixture adapters, controllable clock.

Everything here is a FIXTURE adapter (fixture inference, fixture transcriber,
local schedule store, SQLite). Passing this suite is local evidence only; it
says nothing about real ASR, Bedrock, DynamoDB, EventBridge or web push.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from metis_contracts import FixedClock, reset_settings_cache

from e2e_support import T0


class Harness:
    """One app + worker loop. `tick()` is a bounded local-worker iteration."""

    def __init__(self, adapters, client, clock):
        from metis_pipeline.dispatcher import DispatchContext, OutboxDispatcher
        from metis_pipeline.local_worker import build_registry

        self.adapters = adapters
        self.client = client
        self.clock = clock
        self.state = adapters.state_store
        self.context = DispatchContext(
            state_store=adapters.state_store,
            source_store=adapters.source_store,
            clock=clock,
            inference=adapters.inference,
            transcriber=adapters.transcriber,
            schedule_store=adapters.schedule_store,
            notification_transports=adapters.notification_transports,
            memory_store=adapters.memory_store,
            embedder=adapters.embedder,
            worker_id="e2e-worker",
        )
        self.dispatcher = OutboxDispatcher(build_registry(), self.context)

    def drain(self, cycles: int = 8):
        """Drain the outbox until idle (bounded)."""
        results = []
        for _ in range(cycles):
            result = self.dispatcher.run_once(limit=25)
            results.append(result)
            if result.processed == 0:
                break
        return results

    def tick(self, cycles: int = 8) -> int:
        """Outbox drain plus the local due-reminder and daily-plan timers."""
        from metis_pipeline.local_worker import _fire_due_daily_plans, _fire_due_reminders

        fired = 0
        for _ in range(cycles):
            self.drain(cycles=2)
            due = _fire_due_reminders(self.context) + _fire_due_daily_plans(self.context)
            fired += due
            if not due:
                break
        self.drain()
        return fired

    def advance(self, **delta) -> None:
        self.clock.advance(**delta)

    def as_user(self, user_id: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {user_id}"}


@pytest.fixture()
def clock() -> FixedClock:
    return FixedClock(T0)


@pytest.fixture()
def adapters(tmp_path, clock, monkeypatch):
    monkeypatch.setenv("METIS_MODE", "local")
    monkeypatch.setenv("METIS_ENV", "dev")
    monkeypatch.setenv("METIS_SQLITE_PATH", str(tmp_path / "state.sqlite"))
    monkeypatch.setenv("METIS_LOCAL_OBJECTS_DIR", str(tmp_path / "objects"))
    monkeypatch.setenv("METIS_ALLOW_FIXTURE_IDENTITY", "true")
    monkeypatch.setenv("METIS_DAILY_MICRO_USD", "1000000")
    monkeypatch.setenv("METIS_RATE_PER_MINUTE", "100000")
    reset_settings_cache()

    from metis_api.deps import Adapters
    from metis_pipeline.inference import FixtureInferenceClient
    from metis_retrieval import FixtureEmbedder, SqliteMemoryStore
    from metis_scheduler import FixtureTransport, LocalScheduleStore
    from metis_speech import FixtureTranscriber
    from metis_state import LocalSourceStore, SqliteStateStore

    state_store = SqliteStateStore(str(tmp_path / "state.sqlite"), clock=clock)
    transport = FixtureTransport()
    yield Adapters(
        clock=clock,
        state_store=state_store,
        source_store=LocalSourceStore(str(tmp_path / "objects"), clock=clock),
        inference=FixtureInferenceClient(),
        transcriber=FixtureTranscriber(),
        schedule_store=LocalScheduleStore(state_store=state_store, clock=clock),
        memory_store=SqliteMemoryStore(":memory:"),
        embedder=FixtureEmbedder(),
        notification_transports={"in_app": transport, "fixture": transport},
    )
    reset_settings_cache()


@pytest.fixture()
def h(adapters, clock, monkeypatch) -> Harness:
    from metis_api import deps
    from metis_api.auth import Principal
    from metis_api.main import create_app

    real = deps.principal_from_header

    def principal(authorization):
        # Tests choose a second authenticated subject with `Bearer <subject>`;
        # ownership still comes from the (stubbed) verified token, never a body.
        if authorization and authorization.startswith("Bearer "):
            return Principal(user_id=authorization.split(" ", 1)[1], source="cognito")
        return real(authorization)

    monkeypatch.setattr(deps, "principal_from_header", principal)
    app = create_app()
    with TestClient(app) as client:
        app.state.adapters = adapters
        yield Harness(adapters, client, clock)
