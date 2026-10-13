"""Polish rules and hold-to-dictate sessions (fake decoder; no model, no audio libs)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest
from metis_contracts import DictionaryEntry, FlowSettings, Snippet, TranscriptSegment
from metis_speech.flow import BYTES_PER_SECOND
from metis_speech.polish import polish
from metis_speech.server import SpeechServer

SECRET = "s" * 40
CFG = FlowSettings(
    dictionary=[DictionaryEntry(term="Cerno", aliases=["sirno", "serno"]), DictionaryEntry(term="iPhone")],
    snippets=[Snippet(trigger="closing remark", expansion="Best regards,\nNit")],
)


@pytest.mark.parametrize(
    ("raw", "app", "want"),
    [
        ("um so i think uh we should ship", None, "So I think we should ship."),
        ("ask sirno about it", None, "Ask Cerno about it."),
        ("my iphone is dead. iphone again", None, "My iPhone is dead. iPhone again."),
        ("meet at nine. scratch that. meet at ten", None, "Meet at ten."),
        ("hello new paragraph bye", None, "Hello\n\nBye."),
        ("sounds good.", "Slack", "Sounds good"),
        ("git status", "iTerm2", "git status"),
        ("I do not agree", None, "I do not agree."),  # negation untouched
        ("closing remark", None, "Best regards,\nNit"),
        ("", None, ""),
    ],
)
def test_polish(raw: str, app: str | None, want: str) -> None:
    assert polish(raw, CFG, app=app).text == want


def test_fillers_can_be_kept() -> None:
    cfg = FlowSettings(remove_fillers=False)
    assert polish("um okay", cfg).text == "Um okay."


class Fake:
    def __init__(self, text: str = "um hello sirno") -> None:
        self.text, self.sizes = text, []

    def transcribe(self, audio, *, language=None, glossary=(), max_seconds=300):
        self.sizes.append(len(audio))
        self.glossary = glossary
        seg = TranscriptSegment(index=0, start_ms=0, end_ms=500, text=self.text)
        return self.text, [seg], {"adapter_mode": "cloud", "model_id": "fake"}


def call(server: SpeechServer, method: str, path: str, body: bytes = b"", auth: bool = True):
    req = urllib.request.Request(
        f"http://127.0.0.1:{server.server_address[1]}{path}", data=body, method=method
    )
    if auth:
        req.add_header("Authorization", f"Bearer {SECRET}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture
def server():
    srv = SpeechServer(Fake(), SECRET)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.start_loading()
    assert srv.ready_event.wait(5)
    yield srv
    srv.shutdown()
    srv.server_close()


def test_dictation_roundtrip(server: SpeechServer) -> None:
    status, body = call(
        server,
        "POST",
        "/v1/flow/sessions",
        json.dumps({"app": "Notes", "settings": CFG.model_dump()}).encode(),
    )
    assert status == 200
    base = f"/v1/flow/sessions/{body['session_id']}"
    second = b"\x01\x00" * BYTES_PER_SECOND  # 1 s of PCM16 per chunk is 2*16000 samples
    _, ack = call(server, "POST", f"{base}/audio", second[:BYTES_PER_SECOND])
    assert ack["partial"] is None  # under the partial interval
    _, ack = call(server, "POST", f"{base}/audio", second[: BYTES_PER_SECOND * 2])
    assert ack["partial"]["provisional"] is True
    status, done = call(server, "POST", f"{base}/finish")
    assert status == 200
    assert done["text"] == "Hello Cerno."
    assert done["raw_text"] == "um hello sirno"
    assert done["speech"]["adapter_mode"] == "local"
    assert call(server, "POST", f"{base}/finish")[0] == 404  # session consumed


def test_flow_guards(server: SpeechServer) -> None:
    assert call(server, "POST", "/v1/flow/sessions", auth=False)[0] == 401
    sid = call(server, "POST", "/v1/flow/sessions", b"{}")[1]["session_id"]
    assert call(server, "POST", f"/v1/flow/sessions/{sid}/audio", b"\x00")[0] == 422  # odd length
    assert call(server, "POST", "/v1/flow/sessions", b'{"settings": {"nope": 1}}')[0] == 422
    assert call(server, "DELETE", f"/v1/flow/sessions/{sid}")[0] == 200
    assert call(server, "POST", f"/v1/flow/sessions/{sid}/audio", b"\x00\x00")[0] == 404
