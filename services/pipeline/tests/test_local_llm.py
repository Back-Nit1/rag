"""LocalInferenceClient against a fake loopback Ollama. No model, no network."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from metis_contracts import AdapterMode, ErrorCode, MetisError, reset_settings_cache
from metis_pipeline.inference import FixtureInferenceClient, build_inference_client
from metis_pipeline.local_llm import LocalConfig, LocalInferenceClient

GOOD = {"items": [], "requires_clarification": False, "notes": "x"}


class FakeOllama:
    def __init__(self) -> None:
        self.chat_replies: list[dict] = []
        self.models = ["tiny:latest"]
        self.requests: list[tuple[str, dict]] = []
        owner = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): ...

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._send(200, {"models": [{"name": m} for m in owner.models]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append((self.path, body))
                if self.path == "/api/chat":
                    self._send(200, owner.chat_replies.pop(0))
                else:
                    self._send(404, {"error": "nope"})

        self.server = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()


def chat(content: str, **extra) -> dict:
    return {"message": {"content": content}, "prompt_eval_count": 10, "eval_count": 5, **extra}


@pytest.fixture()
def fake():
    f = FakeOllama()
    yield f
    f.close()


def client(fake, model="tiny") -> LocalInferenceClient:
    return LocalInferenceClient(LocalConfig(fake.endpoint, model, None))


def call(c, **kw):
    return c.complete_json(
        prompt_version="v",
        system="s",
        user_content="note",
        json_schema={"type": "object"},
        max_input_tokens=1000,
        max_output_tokens=100,
        **kw,
    )


def test_success_reports_local_mode_and_schema_constraint(fake):
    fake.chat_replies = [chat(json.dumps(GOOD))]
    payload, usage = call(client(fake))
    assert payload == GOOD
    assert usage["adapter_mode"] == "local" and usage["model_id"] == "tiny"
    assert usage["input_tokens"] == 10 and usage["micro_usd"] == 0
    path, body = fake.requests[0]
    assert path == "/api/chat" and body["format"] == {"type": "object"} and body["stream"] is False


def test_one_bounded_repair_then_success(fake):
    fake.chat_replies = [chat("not json"), chat(json.dumps(GOOD))]
    _, usage = call(client(fake))
    assert usage["attempts"] == 2


def test_malformed_twice_fails_honestly(fake):
    fake.chat_replies = [chat("nope"), chat("still nope")]
    with pytest.raises(MetisError) as e:
        call(client(fake))
    assert e.value.code == ErrorCode.SEMANTIC_VALIDATION_FAILED
    assert len(fake.requests) == 2


def test_truncation_is_an_error(fake):
    fake.chat_replies = [chat('{"items": [', done_reason="length")]
    with pytest.raises(MetisError, match="truncated"):
        call(client(fake))


def test_oversize_input_rejected_before_call(fake):
    with pytest.raises(MetisError) as e:
        client(fake).complete_json(
            prompt_version="v",
            system="s",
            user_content="x" * 4000,
            json_schema={},
            max_input_tokens=10,
            max_output_tokens=10,
        )
    assert e.value.code == ErrorCode.LIMIT_EXCEEDED and not fake.requests


def test_health_reports_missing_model_and_down_runtime(fake):
    assert client(fake).health()[0] is True
    assert client(fake, "other").health()[0] is False
    fake.close()
    ok, detail = client(fake).health()
    assert not ok and "unreachable" in detail


def test_unreachable_runtime_is_provider_unavailable(fake):
    fake.close()
    with pytest.raises(MetisError) as e:
        call(client(fake))
    assert e.value.code == ErrorCode.PROVIDER_UNAVAILABLE


def test_model_must_be_explicit_and_endpoint_loopback(monkeypatch):
    with pytest.raises(MetisError, match="METIS_LOCAL_LLM_MODEL"):
        LocalInferenceClient(LocalConfig("http://127.0.0.1:1", None, None))
    monkeypatch.setenv("METIS_LOCAL_LLM_ENDPOINT", "http://example.com:11434")
    with pytest.raises(ValueError, match="loopback"):
        LocalConfig.from_env()


def test_engine_selection(monkeypatch):
    monkeypatch.delenv("METIS_INFERENCE_ENGINE", raising=False)
    reset_settings_cache()
    assert isinstance(build_inference_client(), FixtureInferenceClient)
    monkeypatch.setenv("METIS_INFERENCE_ENGINE", "local")
    monkeypatch.setenv("METIS_LOCAL_LLM_MODEL", "tiny")
    c = build_inference_client()
    assert c.mode is AdapterMode.LOCAL
    monkeypatch.setenv("METIS_INFERENCE_ENGINE", "bogus")
    with pytest.raises(ValueError):
        build_inference_client()
