"""API test harness: real app, fixture adapters, controllable clock."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from metis_contracts import FixedClock, reset_settings_cache

T0 = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)  # 10:00 in New York
NY = "America/New_York"
USER = "demo_authenticated_subject"


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
    monkeypatch.setenv("METIS_RATE_PER_MINUTE", "10000")
    reset_settings_cache()

    from metis_api.deps import Adapters
    from metis_pipeline.inference import FixtureInferenceClient
    from metis_retrieval import FixtureEmbedder, SqliteMemoryStore
    from metis_scheduler import FixtureTransport, LocalScheduleStore
    from metis_speech import FixtureTranscriber
    from metis_state import LocalSourceStore, SqliteStateStore

    state_store = SqliteStateStore(str(tmp_path / "state.sqlite"), clock=clock)
    transport = FixtureTransport()
    adapters = Adapters(
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
    yield adapters
    reset_settings_cache()


@pytest.fixture()
def client(adapters) -> TestClient:
    from metis_api.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        # Replace the lifespan-built adapters with the fixture set.
        app.state.adapters = adapters
        yield test_client


@pytest.fixture()
def worker(adapters):
    """Drains the outbox with every service's handlers registered."""
    from metis_pipeline.dispatcher import DispatchContext, OutboxDispatcher
    from metis_pipeline.local_worker import build_registry

    context = DispatchContext(
        state_store=adapters.state_store,
        source_store=adapters.source_store,
        clock=adapters.clock,
        inference=adapters.inference,
        transcriber=adapters.transcriber,
        schedule_store=adapters.schedule_store,
        notification_transports=adapters.notification_transports,
        memory_store=adapters.memory_store,
        embedder=adapters.embedder,
        worker_id="test-worker",
    )
    dispatcher = OutboxDispatcher(build_registry(), context)

    def drain(cycles: int = 6):
        results = []
        for _ in range(cycles):
            result = dispatcher.run_once(limit=25)
            results.append(result)
            if result.processed == 0:
                break
        return results

    return drain
