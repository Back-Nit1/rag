"""SpeechTranscriber client for the warm loopback server (``server.py``)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from metis_contracts import AdapterMode, ErrorCode, MetisError, ObjectRef, TranscriptSegment, settings

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_ERROR_CHARS = 300


class RemoteTranscriber:
    """Calls the local warm server. The endpoint is config-validated loopback
    only; callers cannot supply a URL or a path."""

    mode = AdapterMode.LOCAL

    def __init__(self, endpoint: str, secret: str, *, timeout: float | None = None) -> None:
        self._url = endpoint.rstrip("/") + "/v1/transcribe"
        self._secret = secret
        self._timeout = timeout or float(settings().limits.speech_wall_clock_seconds)

    def transcribe(
        self,
        audio: bytes | ObjectRef,
        *,
        language: str | None = None,
        glossary: tuple[str, ...] = (),
        max_seconds: int = 300,
    ) -> tuple[str, list[TranscriptSegment], dict[str, Any]]:
        if isinstance(audio, ObjectRef):
            raise MetisError(ErrorCode.VALIDATION_FAILED, "pass audio bytes; object references are not sent")
        headers = {"Authorization": f"Bearer {self._secret}", "Content-Type": "application/octet-stream"}
        if language:
            headers["X-Metis-Language"] = language
        if glossary:
            headers["X-Metis-Glossary"] = json.dumps(list(glossary[:32]), ensure_ascii=True)
        req = urllib.request.Request(self._url, data=audio, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                payload = json.loads(resp.read(MAX_RESPONSE_BYTES))
        except urllib.error.HTTPError as exc:
            raise _error_from(exc) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MetisError(
                ErrorCode.PROVIDER_UNAVAILABLE, "the local speech server is unreachable or timed out"
            ) from exc
        except ValueError as exc:
            raise MetisError(ErrorCode.INTERNAL, "the speech server returned an invalid response") from exc
        segments = [TranscriptSegment.model_validate(s) for s in payload["segments"]]
        return payload["text"], segments, payload["speech"]


def _error_from(exc: urllib.error.HTTPError) -> MetisError:
    try:
        body = json.loads(exc.read(MAX_RESPONSE_BYTES))
        code = ErrorCode(body["code"])
        message = str(body.get("message", ""))[:MAX_ERROR_CHARS]
        retry = body.get("retry_after_seconds")
        return MetisError(code, message, retry_after_seconds=retry if isinstance(retry, int) else None)
    except (ValueError, KeyError, TypeError):
        return MetisError(ErrorCode.PROVIDER_UNAVAILABLE, f"speech server returned HTTP {exc.code}")
