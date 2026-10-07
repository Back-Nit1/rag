"""Desktop-local authentication: secret authenticates, install subject owns."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from metis_api.desktop import install_subject, require_loopback
from metis_contracts import JobState, reset_settings_cache
from metis_contracts.entities import Job

T0 = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
SECRET = "s" * 40
OTHER = "t" * 40


@pytest.fixture()
def desktop(client: TestClient, adapters, tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("METIS_DESKTOP_AUTH_SECRET", SECRET)
    monkeypatch.setenv("METIS_DESKTOP_IDENTITY_PATH", str(tmp_path / "id" / "install_id"))
    reset_settings_cache()
    return client


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def test_correct_secret_authenticates(desktop):
    assert desktop.get("/v1/tasks", headers=bearer(SECRET)).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        bearer(OTHER),
        bearer(""),
        {"Authorization": SECRET},
        {"Authorization": f"Basic {SECRET}"},
    ],
)
def test_missing_wrong_or_malformed_secret_rejected(desktop, headers):
    response = desktop.get("/v1/tasks", headers=headers)
    assert response.status_code == 401
    assert SECRET not in response.text


def test_random_bearer_never_reaches_cognito(desktop, monkeypatch):
    def boom(token):
        raise AssertionError("cognito must not be consulted")

    monkeypatch.setattr("metis_api.auth.verify_cognito_token", boom)
    assert desktop.get("/v1/tasks", headers=bearer("a.b.c")).status_code == 401


def _capture(client, secret, idem):
    body = {"kind": "text", "text": "call mom", "capture_timezone": "UTC", "captured_at": T0.isoformat()}
    response = client.post("/v1/captures", json=body, headers=bearer(secret) | {"Idempotency-Key": idem})
    assert response.status_code in (200, 201, 202), response.text
    return response.json()["capture_id"]


def test_owner_is_stable_install_subject_not_secret(desktop, adapters, tmp_path, monkeypatch):
    cid = _capture(desktop, SECRET, "k1")
    subject = (tmp_path / "id" / "install_id").read_text().strip()
    assert subject.startswith("desktop_") and SECRET not in subject
    assert adapters.state_store.get(subject, "source", cid) is not None
    # A new launch with a different secret keeps the same owner.
    monkeypatch.setenv("METIS_DESKTOP_AUTH_SECRET", OTHER)
    reset_settings_cache()
    assert desktop.get(f"/v1/captures/{cid}", headers=bearer(OTHER)).status_code == 200
    assert desktop.get(f"/v1/captures/{cid}", headers=bearer(SECRET)).status_code == 401


def test_client_supplied_user_id_ignored(desktop, adapters):
    response = desktop.get("/v1/tasks?user_id=someone", headers=bearer(SECRET) | {"X-User-Id": "someone"})
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_health_and_limits_stay_open(desktop):
    assert desktop.get("/v1/health").status_code == 200
    assert desktop.get("/v1/limits").status_code == 200


def test_fixture_identity_unchanged_when_secret_unset(client):
    assert client.get("/v1/tasks").status_code == 200


def test_short_secret_refused(monkeypatch):
    monkeypatch.setenv("METIS_DESKTOP_AUTH_SECRET", "short")
    reset_settings_cache()
    from metis_contracts import settings

    with pytest.raises(ValueError, match="32"):
        settings()
    reset_settings_cache()


def test_production_refuses_desktop_secret(monkeypatch):
    monkeypatch.setenv("METIS_ENV", "prod")
    monkeypatch.setenv("METIS_DESKTOP_AUTH_SECRET", SECRET)
    reset_settings_cache()
    from metis_contracts import settings

    with pytest.raises(ValueError):
        settings()
    reset_settings_cache()


def test_identity_file_created_once_and_corrupt_never_overwritten(tmp_path):
    path = str(tmp_path / "a" / "install_id")
    first = install_subject(path)
    assert install_subject(path) == first
    bad = tmp_path / "bad"
    bad.write_text("garbage")
    with pytest.raises(Exception, match="invalid"):
        install_subject(str(bad))
    assert bad.read_text() == "garbage"


def test_loopback_only():
    assert require_loopback("127.0.0.1") == "127.0.0.1"
    assert require_loopback("::1") == "::1"
    for host in ("0.0.0.0", "192.168.1.5", "localhost.evil", ""):
        with pytest.raises(ValueError):
            require_loopback(host)


def test_capture_detail_includes_only_this_captures_jobs(desktop, adapters):
    cid, other = _capture(desktop, SECRET, "c1"), _capture(desktop, SECRET, "c2")
    owner = _owner()
    now = adapters.clock.now()
    for jid, target in (("job_a", cid), ("job_b", other)):
        with adapters.state_store.transaction(owner) as tx:
            tx.put(
                "job",
                Job(
                    id=jid,
                    user_id=owner,
                    created_at=now,
                    updated_at=now,
                    job_type="transcribe",
                    state=JobState.FAILED,
                    target_id=target,
                    correlation_id="run_x",
                    idempotency_key=jid,
                ),
            )
    jobs = desktop.get(f"/v1/captures/{cid}", headers=bearer(SECRET)).json()["jobs"]
    assert [j["id"] for j in jobs] == ["job_a"]
    assert jobs[0]["state"] == "failed"


def _owner():
    return Path(os.environ["METIS_DESKTOP_IDENTITY_PATH"]).read_text().strip()
