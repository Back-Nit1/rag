"""Warm loopback speech server for the desktop shell.

One transcriber is loaded once (in a background thread, so the port is bound and
reportable immediately) and reused for sequential decodes. Standard library
only: the surface is two endpoints.

    GET  /v1/ready       -> 200 {"state":"ready",...} | 503 {"state":"loading"|"failed"}
    POST /v1/transcribe  -> body = raw audio bytes (never a path or URL)
                            headers: X-Metis-Language, X-Metis-Glossary (JSON list)

Every request needs ``Authorization: Bearer $METIS_SPEECH_SECRET``. Audio lives
in memory only, so nothing is written to disk. Audio and transcripts are never
logged.

Sidecar protocol: stdout carries exactly one JSON line once the socket listens,
``{"event":"listening","protocol":1,"host":"127.0.0.1","port":N,"pid":P}``.
Model readiness is polled via ``/v1/ready``. SIGTERM/SIGINT, or stdin EOF with
``--exit-on-stdin-eof`` (parent died), shuts down cleanly.
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from metis_contracts import HTTP_STATUS, ErrorCode, MetisError, settings

log = logging.getLogger("metis.speech.server")

PROTOCOL = 1
MIN_SECRET_LEN = 32
MAX_GLOSSARY_HEADER = 2048
REQUEST_TIMEOUT_SECONDS = 30  # socket read bound: a stalled client cannot pin a thread
MAX_ERROR_CHARS = 300


class SpeechServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, transcriber: Any, secret: str) -> None:
        if len(secret) < MIN_SECRET_LEN:
            raise ValueError(f"METIS_SPEECH_SECRET must be at least {MIN_SECRET_LEN} characters")
        super().__init__(("127.0.0.1", 0), _Handler)  # loopback only, OS-assigned port
        self.transcriber = transcriber
        self.secret = secret.encode()
        self.state = "loading"
        self.load_error = ""
        self.busy = threading.Lock()  # one decode at a time
        self.ready_event = threading.Event()

    def load_model(self) -> None:
        try:
            if hasattr(self.transcriber, "warm"):
                self.transcriber.warm()
            self.state = "ready"
        except Exception as exc:  # surfaced via /v1/ready, never crashes the server
            self.load_error = str(exc)[:MAX_ERROR_CHARS]
            self.state = "failed"
            log.error("model load failed")
        self.ready_event.set()

    def start_loading(self) -> threading.Thread:
        thread = threading.Thread(target=self.load_model, name="model-load", daemon=True)
        thread.start()
        return thread


class _Handler(BaseHTTPRequestHandler):
    server: SpeechServer
    timeout = REQUEST_TIMEOUT_SECONDS
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        pass  # the default logger prints client addresses and paths; we log nothing per request

    def _send(self, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def _error(self, code: ErrorCode, message: str, **extra: Any) -> None:
        body: dict[str, Any] = {"code": code.value, "message": message[:MAX_ERROR_CHARS]}
        body.update(extra)
        self._send(HTTP_STATUS[code], body)

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = b"Bearer " + self.server.secret
        if hmac.compare_digest(supplied.encode(), expected):
            return True
        self._error(ErrorCode.UNAUTHENTICATED, "missing or invalid credentials")
        return False

    def _not_ready(self) -> bool:
        state = self.server.state
        if state == "ready":
            return False
        self._send(
            503,
            {
                "code": ErrorCode.PROVIDER_UNAVAILABLE.value,
                "message": "the speech model failed to load"
                if state == "failed"
                else "the speech model is still loading",
                "state": state,
                "retry_after_seconds": None if state == "failed" else 2,
            },
        )
        return True

    def do_GET(self) -> None:
        if not self._authorized():
            return
        if self.path != "/v1/ready":
            return self._error(ErrorCode.NOT_FOUND, "not found")
        if self._not_ready():
            return
        cfg = settings().speech
        self._send(200, {"state": "ready", "model_id": cfg.model_id, "protocol": PROTOCOL})

    def do_POST(self) -> None:
        if not self._authorized():
            return
        if self.path != "/v1/transcribe":
            return self._error(ErrorCode.NOT_FOUND, "not found")
        if self._not_ready():
            return
        limits = settings().limits
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._error(ErrorCode.VALIDATION_FAILED, "Content-Length is required")
        if length <= 0:
            return self._error(ErrorCode.UNSUPPORTED_MEDIA, "the recording is empty")
        if length > limits.max_upload_bytes:  # rejected before a byte is read
            return self._error(
                ErrorCode.UPLOAD_TOO_LARGE, f"recording is over the {limits.max_upload_bytes} byte limit"
            )
        glossary = self._glossary()
        if glossary is None:
            return
        if not self.server.busy.acquire(blocking=False):
            return self._error(ErrorCode.RATE_LIMITED, "a decode is already running", retry_after_seconds=1)
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                return self._error(ErrorCode.VALIDATION_FAILED, "request body was truncated")
            text, segments, meta = self.server.transcriber.transcribe(
                body,
                language=self.headers.get("X-Metis-Language") or None,
                glossary=glossary,
                max_seconds=limits.max_audio_seconds,
            )
        except MetisError as exc:
            return self._error(exc.code, exc.message)
        except TimeoutError:
            return self._error(ErrorCode.VALIDATION_FAILED, "timed out reading the request body")
        except Exception:
            log.exception("decode failed")  # traceback only; no audio or text is in scope of the log line
            return self._error(ErrorCode.INTERNAL, "decoding failed")
        finally:
            self.server.busy.release()
        # The decoder runs on this machine: label it local, not cloud.
        meta["adapter_mode"] = "local" if meta.get("adapter_mode") == "cloud" else meta["adapter_mode"]
        self._send(
            200,
            {"text": text, "segments": [s.model_dump(mode="json") for s in segments], "speech": meta},
        )

    def _glossary(self) -> tuple[str, ...] | None:
        raw = self.headers.get("X-Metis-Glossary")
        if not raw:
            return ()
        try:
            if len(raw) > MAX_GLOSSARY_HEADER:
                raise ValueError
            terms = json.loads(raw)
            if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
                raise ValueError
        except ValueError:
            self._error(ErrorCode.VALIDATION_FAILED, "X-Metis-Glossary must be a short JSON list of strings")
            return None
        return tuple(terms[:32])


def serve(server: SpeechServer, *, exit_on_stdin_eof: bool = False) -> None:
    """Announce, then serve until SIGTERM/SIGINT/stdin EOF."""
    stopping = threading.Event()

    def _stop(*_: object) -> None:
        if not stopping.is_set():
            stopping.set()
            # shutdown() blocks until serve_forever exits, so never call it inline.
            threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    if exit_on_stdin_eof:

        def _watch() -> None:
            sys.stdin.read()  # blocks until the parent closes the pipe or dies
            _stop()

        threading.Thread(target=_watch, name="parent-watch", daemon=True).start()

    record = {
        "event": "listening",
        "protocol": PROTOCOL,
        "host": "127.0.0.1",
        "port": server.server_address[1],
        "pid": os.getpid(),
    }
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()
    server.start_loading()
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Metis warm loopback speech server")
    parser.add_argument("--exit-on-stdin-eof", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=settings().log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if settings().desktop.speech_engine == "remote":
        sys.stderr.write("METIS_SPEECH_ENGINE=remote would make the server call itself; refusing\n")
        return 2
    secret = os.environ.get("METIS_SPEECH_SECRET", "")
    from .transcriber import FasterWhisperTranscriber

    try:
        server = SpeechServer(FasterWhisperTranscriber(), secret)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    serve(server, exit_on_stdin_eof=args.exit_on_stdin_eof)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
