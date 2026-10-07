"""Warm server + remote client, with a fake transcriber (no model, no audio libs)."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest
from metis_contracts import AdapterMode, ErrorCode, MetisError, TranscriptSegment
from metis_speech.remote import RemoteTranscriber
from metis_speech.server import SpeechServer
from metis_speech.transcriber import FasterWhisperTranscriber, FixtureTranscriber, build_transcriber

SECRET = "s" * 40


class Fake:
    def __init__(self, *, gate: threading.Event | None = None, error: Exception | None = None) -> None:
        self.warm_calls = 0
        self.decodes = 0
        self.gate = gate
        self.error = error
        self.load_gate: threading.Event | None = None

    def warm(self) -> None:
        if self.load_gate:
            self.load_gate.wait(5)
        self.warm_calls += 1

    def transcribe(self, audio, *, language=None, glossary=(), max_seconds=300):
        self.decodes += 1
        if self.gate:
            self.gate.wait(5)
        if self.error:
            raise self.error
        seg = TranscriptSegment(index=0, start_ms=0, end_ms=500, text="hello there")
        return "hello there", [seg], {"adapter_mode": "cloud", "model_id": "fake", "glossary": list(glossary)}


def start(fake: Fake, *, load: bool = True):
    server = SpeechServer(fake, SECRET)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if load:
        server.start_loading()
        assert server.ready_event.wait(5)
    return server


def url(server, path):
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


def call(server, path="/v1/transcribe", *, body=b"x", secret=SECRET, headers=None):
    h = {"Authorization": f"Bearer {secret}", **(headers or {})}
    req = urllib.request.Request(url(server, path), data=body if path != "/v1/ready" else None, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture
def servers():
    made = []

    def _make(fake, **kw):
        s = start(fake, **kw)
        made.append(s)
        return s

    yield _make
    for s in made:
        s.shutdown()
        s.server_close()


def test_binds_loopback_only(servers):
    s = servers(Fake())
    assert s.server_address[0] == "127.0.0.1" and s.server_address[1] > 0


def test_short_secret_refused():
    with pytest.raises(ValueError):
        SpeechServer(Fake(), "short")


def test_warm_once_and_repeated_decodes(servers):
    fake = Fake()
    s = servers(fake)
    for _ in range(3):
        status, body = call(s)
        assert status == 200 and body["text"] == "hello there"
    assert fake.warm_calls == 1 and fake.decodes == 3


def test_adapter_mode_is_local_not_cloud_or_fixture(servers):
    _, body = call(servers(Fake()))
    assert body["speech"]["adapter_mode"] == "local"


def test_not_ready_while_loading(servers):
    fake = Fake()
    fake.load_gate = threading.Event()
    s = servers(fake, load=False)
    s.start_loading()
    status, body = call(s, "/v1/ready")
    assert status == 503 and body["state"] == "loading"
    status, body = call(s)
    assert status == 503 and fake.decodes == 0
    fake.load_gate.set()
    assert s.ready_event.wait(5)
    assert call(s, "/v1/ready")[0] == 200


def test_load_failure_reported_not_hung(servers):
    class Broken(Fake):
        def warm(self):
            raise MetisError(ErrorCode.PROVIDER_UNAVAILABLE, "no weights")

    s = servers(Broken())
    status, body = call(s, "/v1/ready")
    assert status == 503 and body["state"] == "failed"


@pytest.mark.parametrize("secret", ["", "wrong" * 10])
def test_invalid_auth(servers, secret):
    fake = Fake()
    s = servers(fake)
    assert call(s, secret=secret)[0] == 401
    assert call(s, "/v1/ready", secret=secret)[0] == 401
    assert fake.decodes == 0


def test_oversize_rejected_before_read(servers, monkeypatch):
    from metis_contracts import settings

    s = servers(Fake())
    over = settings().limits.max_upload_bytes + 1
    req = urllib.request.Request(
        url(s, "/v1/transcribe"),
        data=b"x",
        headers={"Authorization": f"Bearer {SECRET}", "Content-Length": str(over)},
    )
    with pytest.raises((urllib.error.HTTPError, urllib.error.URLError, OSError)) as e:
        urllib.request.urlopen(req, timeout=5)
    if isinstance(e.value, urllib.error.HTTPError):
        assert e.value.code == 413


def test_empty_body_is_unsupported_media(servers):
    status, _ = call(servers(Fake()), body=b"")
    assert status in (415, 422)


@pytest.mark.parametrize(
    "error,status",
    [
        (MetisError(ErrorCode.UNSUPPORTED_MEDIA, "corrupt"), 415),
        (MetisError(ErrorCode.LIMIT_EXCEEDED, "too long"), 413),
        (RuntimeError("secret transcript text"), 500),
    ],
)
def test_decode_errors_mapped_without_leaking(servers, error, status):
    s = servers(Fake(error=error))
    code, body = call(s)
    assert code == status
    assert "secret transcript text" not in json.dumps(body)


def test_silent_audio_returns_empty_text(servers):
    class Silent(Fake):
        def transcribe(self, audio, **kw):
            return "", [], {"adapter_mode": "cloud", "no_speech": True}

    status, body = call(servers(Silent()))
    assert status == 200 and body["text"] == "" and body["segments"] == []


def test_busy_server_rejects_second_decode(servers):
    gate = threading.Event()
    s = servers(Fake(gate=gate))
    first = threading.Thread(target=call, args=(s,))
    first.start()
    time.sleep(0.3)
    status, body = call(s)
    gate.set()
    first.join(5)
    assert status == 429 and body["code"] == ErrorCode.RATE_LIMITED.value


def test_bad_glossary_header(servers):
    status, _ = call(servers(Fake()), headers={"X-Metis-Glossary": "not json"})
    assert status == 422


def test_unknown_path_404(servers):
    assert call(servers(Fake()), "/etc/passwd")[0] == 404


# ---- remote client -------------------------------------------------------- #


def test_remote_client_roundtrip(servers):
    s = servers(Fake())
    port = s.server_address[1]
    text, segs, meta = RemoteTranscriber(f"http://127.0.0.1:{port}", SECRET).transcribe(
        b"audio", glossary=("Bloomberg",)
    )
    assert text == "hello there" and segs[0].text == "hello there"
    assert meta["glossary"] == ["Bloomberg"] and meta["adapter_mode"] == "local"


def test_remote_client_maps_errors(servers):
    s = servers(Fake(error=MetisError(ErrorCode.UNSUPPORTED_MEDIA, "corrupt")))
    client = RemoteTranscriber(f"http://127.0.0.1:{s.server_address[1]}", SECRET)
    with pytest.raises(MetisError) as exc:
        client.transcribe(b"x")
    assert exc.value.code is ErrorCode.UNSUPPORTED_MEDIA
    bad = RemoteTranscriber(f"http://127.0.0.1:{s.server_address[1]}", "w" * 40)
    with pytest.raises(MetisError) as exc:
        bad.transcribe(b"x")
    assert exc.value.code is ErrorCode.UNAUTHENTICATED


def test_remote_client_unreachable():
    with pytest.raises(MetisError) as exc:
        RemoteTranscriber("http://127.0.0.1:9", SECRET, timeout=1).transcribe(b"x")
    assert exc.value.code is ErrorCode.PROVIDER_UNAVAILABLE


# ---- build_transcriber ---------------------------------------------------- #


def _engine(monkeypatch, engine, **env):
    from metis_contracts import config

    monkeypatch.setenv("METIS_SPEECH_ENGINE", engine)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    if hasattr(config.settings, "cache_clear"):
        config.settings.cache_clear()


def test_engine_selection_independent_of_mode(monkeypatch):
    monkeypatch.setenv("METIS_MODE", "local")
    _engine(monkeypatch, "faster_whisper")
    assert isinstance(build_transcriber(), FasterWhisperTranscriber)
    _engine(monkeypatch, "fixture")
    assert isinstance(build_transcriber(), FixtureTranscriber)
    _engine(
        monkeypatch,
        "remote",
        METIS_SPEECH_ENDPOINT="http://127.0.0.1:5555",
        METIS_SPEECH_SECRET=SECRET,
    )
    t = build_transcriber()
    assert isinstance(t, RemoteTranscriber) and t.mode is AdapterMode.LOCAL
    _engine(monkeypatch, "auto")
    assert isinstance(build_transcriber(), FixtureTranscriber)


def test_server_main_refuses_remote_engine(monkeypatch):
    _engine(monkeypatch, "remote", METIS_SPEECH_ENDPOINT="http://127.0.0.1:5555", METIS_SPEECH_SECRET=SECRET)
    from metis_speech.server import main

    assert main([]) == 2


def test_sigterm_shuts_down_cleanly():
    env = {"METIS_SPEECH_SECRET": SECRET, "PATH": "", "METIS_MODE": "local"}
    import os

    env = {**os.environ, **env}
    env.pop("METIS_SPEECH_ENGINE", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "metis_speech.server"], stdout=subprocess.PIPE, env=env, text=True
    )
    assert proc.stdout is not None
    record = json.loads(proc.stdout.readline())
    assert record["event"] == "listening" and record["host"] == "127.0.0.1" and record["port"] > 0
    assert SECRET not in json.dumps(record)
    proc.terminate()
    assert proc.wait(10) == 0
