import pytest
from metis_contracts.config import DesktopConfig, Settings

SECRET = "s" * 32


def test_defaults_are_off(monkeypatch):
    for k in ("METIS_DESKTOP_AUTH_SECRET", "METIS_SPEECH_ENGINE"):
        monkeypatch.delenv(k, raising=False)
    cfg = DesktopConfig()
    assert not cfg.enabled and cfg.speech_engine == "auto"


def test_short_secret_rejected(monkeypatch):
    monkeypatch.setenv("METIS_DESKTOP_AUTH_SECRET", "short")
    with pytest.raises(ValueError, match="32"):
        DesktopConfig()


@pytest.mark.parametrize("endpoint", [None, "http://10.0.0.5:9", "https://127.0.0.1:9", "http://127.0.0.1"])
def test_remote_requires_loopback_endpoint(monkeypatch, endpoint):
    monkeypatch.setenv("METIS_SPEECH_ENGINE", "remote")
    monkeypatch.setenv("METIS_SPEECH_SECRET", SECRET)
    if endpoint:
        monkeypatch.setenv("METIS_SPEECH_ENDPOINT", endpoint)
    else:
        monkeypatch.delenv("METIS_SPEECH_ENDPOINT", raising=False)
    with pytest.raises(ValueError, match="ENDPOINT"):
        DesktopConfig()


def test_remote_ok_and_unknown_engine(monkeypatch):
    monkeypatch.setenv("METIS_SPEECH_ENGINE", "remote")
    monkeypatch.setenv("METIS_SPEECH_ENDPOINT", "http://127.0.0.1:5123")
    monkeypatch.setenv("METIS_SPEECH_SECRET", SECRET)
    assert DesktopConfig().speech_engine == "remote"
    monkeypatch.setenv("METIS_SPEECH_ENGINE", "bogus")
    with pytest.raises(ValueError, match="METIS_SPEECH_ENGINE"):
        DesktopConfig()


def test_prod_refuses_desktop_secret(monkeypatch):
    monkeypatch.setenv("METIS_ENV", "prod")
    monkeypatch.setenv("METIS_MODE", "cloud")
    monkeypatch.setenv("METIS_ALLOW_FIXTURE_IDENTITY", "false")
    monkeypatch.setenv("METIS_TABLE_NAME", "t")
    monkeypatch.setenv("METIS_SOURCES_BUCKET", "b")
    monkeypatch.setenv("METIS_COGNITO_USER_POOL_ID", "p")
    monkeypatch.setenv("METIS_COGNITO_CLIENT_ID", "c")
    monkeypatch.setenv("METIS_DESKTOP_AUTH_SECRET", SECRET)
    with pytest.raises(ValueError, match="DESKTOP"):
        Settings()
