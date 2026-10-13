"""Regression tests for findings from the open-source security review.

Each test pins one attack that worked before the fix.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from metis_contracts import MetisError, reset_settings_cache

NY = "America/New_York"
T0 = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)

AUDIO = {
    "kind": "audio",
    "content_type": "audio/webm",
    "expected_bytes": 10,
    "capture_timezone": NY,
    "captured_at": T0.isoformat(),
}


def _audio_capture(client) -> dict:
    return client.post("/v1/captures", json=AUDIO).json()


# --- request edge ------------------------------------------------------------ #


def test_dns_rebound_host_header_is_refused(client):
    assert client.get("/v1/captures", headers={"Host": "evil.example.com"}).status_code == 403
    assert client.get("/v1/captures", headers={"Host": "localhost:8000"}).status_code == 200
    assert client.get("/v1/captures", headers={"Host": "[::1]:8000"}).status_code == 200


def test_oversized_json_body_is_refused_while_streaming(client):
    response = client.post("/v1/tasks", content=b'{"title":"' + b"a" * (3 * 1024 * 1024) + b'"}')
    assert response.status_code == 413


def test_oversized_upload_is_refused_before_it_is_buffered(client, monkeypatch):
    monkeypatch.setenv("METIS_MAX_UPLOAD_BYTES", "1000")
    reset_settings_cache()
    capture = _audio_capture(client)
    response = client.put(
        capture["upload"]["url"], content=b"\0" * 5000, headers={"Content-Type": "audio/webm"}
    )
    assert response.status_code == 413


def test_correlation_id_is_a_short_safe_token(client):
    hostile = "A" * 5000 + "\x1b[31m"
    echoed = client.get("/v1/captures", headers={"X-Correlation-Id": "x" * 65}).headers["x-correlation-id"]
    assert echoed.startswith("run_")
    assert hostile not in echoed
    assert (
        client.get("/v1/captures", headers={"X-Correlation-Id": "trace-1.ok"}).headers["x-correlation-id"]
        == "trace-1.ok"
    )


def test_idempotency_key_is_bounded(client):
    response = client.post("/v1/tasks", json={"title": "t"}, headers={"Idempotency-Key": "k" * 129})
    assert response.status_code == 422


# --- uploads ----------------------------------------------------------------- #


def test_upload_must_match_the_declared_media_type(client):
    capture = _audio_capture(client)
    response = client.put(capture["upload"]["url"], content=b"x", headers={"Content-Type": "text/html"})
    assert response.status_code == 415


def test_a_completed_recording_cannot_be_overwritten(client):
    capture = _audio_capture(client)
    url = capture["upload"]["url"]
    headers = {"Content-Type": "audio/webm"}
    assert client.put(url, content=b"original", headers=headers).status_code == 204
    done = client.post(
        f"/v1/captures/{capture['capture_id']}/complete",
        json={"expected_revision": capture["revision"], "actual_bytes": 8},
        headers={"Idempotency-Key": "complete-1"},
    )
    assert done.status_code == 200, done.text
    assert client.put(url, content=b"swapped!", headers=headers).status_code == 422


# --- push endpoints (SSRF) --------------------------------------------------- #


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://169.254.169.254/latest/meta-data/",
        "https://169.254.169.254/",
        "https://localhost/push",
        "https://evil.example.com/fcm.googleapis.com",
        "https://fcm.googleapis.com.evil.example/x",
        "https://fcm.googleapis.com:8443/x",
        "file:///etc/passwd",
    ],
)
def test_push_registration_rejects_non_push_service_endpoints(client, endpoint):
    response = client.post("/v1/push-subscriptions", json={"endpoint": endpoint, "p256dh": "k", "auth": "a"})
    assert response.status_code == 422, endpoint


def test_push_registration_accepts_a_real_push_service(client):
    response = client.post(
        "/v1/push-subscriptions",
        json={"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "p256dh": "k", "auth": "a"},
    )
    assert response.status_code == 201


# --- identity ---------------------------------------------------------------- #


def test_fixture_identity_cannot_be_enabled_in_cloud_mode_even_in_staging(monkeypatch):
    monkeypatch.setenv("METIS_MODE", "cloud")
    monkeypatch.setenv("METIS_ENV", "staging")
    monkeypatch.setenv("METIS_ALLOW_FIXTURE_IDENTITY", "true")
    reset_settings_cache()
    from metis_contracts import settings

    with pytest.raises(ValueError, match="FIXTURE_IDENTITY"):
        settings()
    reset_settings_cache()


def _verify(monkeypatch, claims: dict, *, key=None):
    from metis_api import auth

    monkeypatch.setenv("METIS_COGNITO_USER_POOL_ID", "us-east-1_TEST")
    monkeypatch.setenv("METIS_COGNITO_CLIENT_ID", "clientA")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    reset_settings_cache()
    signer = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class Key:
        key = signer.public_key()

    class Client:
        def get_signing_key_from_jwt(self, _token):
            return Key()

    monkeypatch.setattr(auth, "_jwks", lambda: Client())
    now = int(time.time())
    iss = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TEST"
    token = jwt.encode(
        {"iss": iss, "iat": now, "exp": now + 600, "sub": "u1", **claims}, key or signer, algorithm="RS256"
    )
    return auth.verify_cognito_token(token)


def test_cognito_access_token_for_this_client_is_accepted(monkeypatch):
    # Access tokens carry client_id and no aud; they were rejected before.
    principal = _verify(monkeypatch, {"token_use": "access", "client_id": "clientA"})
    assert principal.user_id == "u1"


@pytest.mark.parametrize(
    "claims",
    [
        {"token_use": "access", "client_id": "clientB"},
        {"token_use": "id", "aud": "clientB"},
        {"token_use": "refresh", "client_id": "clientA"},
    ],
)
def test_tokens_for_another_client_or_type_are_rejected(monkeypatch, claims):
    with pytest.raises(MetisError):
        _verify(monkeypatch, claims)


def test_token_signed_by_another_key_is_rejected(monkeypatch):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    from metis_api import auth

    monkeypatch.setenv("METIS_COGNITO_USER_POOL_ID", "us-east-1_TEST")
    monkeypatch.setenv("METIS_COGNITO_CLIENT_ID", "clientA")
    reset_settings_cache()
    trusted = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class Client:
        def get_signing_key_from_jwt(self, _t):
            return type("K", (), {"key": trusted.public_key()})()

    monkeypatch.setattr(auth, "_jwks", lambda: Client())
    now = int(time.time())
    forged = jwt.encode(
        {
            "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TEST",
            "iat": now,
            "exp": now + 600,
            "sub": "victim",
            "token_use": "access",
            "client_id": "clientA",
        },
        other,
        algorithm="RS256",
    )
    with pytest.raises(MetisError):
        auth.verify_cognito_token(forged)
