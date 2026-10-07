"""LocalEmbedder against a fake loopback Ollama."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from metis_contracts import AdapterMode, ErrorCode, MetisError
from metis_pipeline.local_llm import LocalConfig
from metis_retrieval.embedder import LocalEmbedder


@pytest.fixture()
def server():
    dims = {"n": 3}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): ...

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            out = json.dumps({"embeddings": [[3.0] + [0.0] * (dims["n"] - 1) for _ in body["input"]]})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(out.encode())

    s = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_port}", dims
    s.shutdown()


def test_embeds_normalizes_and_learns_dimension(server):
    url, _ = server
    e = LocalEmbedder(LocalConfig(url, None, "emb"))
    assert e.mode is AdapterMode.LOCAL
    assert e.embed(["a", "b"]) == [[1.0, 0.0, 0.0]] * 2
    assert e.dimensions == 3


def test_dimension_change_rejected(server):
    url, dims = server
    e = LocalEmbedder(LocalConfig(url, None, "emb"))
    e.embed(["a"])
    dims["n"] = 5
    with pytest.raises(MetisError) as err:
        e.embed(["a"])
    assert err.value.code == ErrorCode.VALIDATION_FAILED


def test_embed_model_required():
    with pytest.raises(MetisError, match="METIS_LOCAL_EMBED_MODEL"):
        LocalEmbedder(LocalConfig("http://127.0.0.1:1", None, None))
