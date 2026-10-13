"""Hold-to-dictate sessions for the warm speech server.

Audio is streamed as raw PCM16 mono 16 kHz chunks while the key is held and
buffered in memory only. Partials are provisional hints for the HUD; the final
text exists only after ``finish`` and is the only thing a client may insert.
Nothing here creates tasks, reminders or sources.
"""

from __future__ import annotations

import io
import secrets
import threading
import time
import wave
from dataclasses import dataclass, field

from metis_contracts import ErrorCode, FlowSettings, MetisError

RATE = 16_000
BYTES_PER_SECOND = RATE * 2
PARTIAL_EVERY_SECONDS = 2.0
IDLE_TTL_SECONDS = 120.0


def wav_bytes(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


@dataclass
class Session:
    id: str
    language: str | None
    app: str | None
    cfg: FlowSettings
    pcm: bytearray = field(default_factory=bytearray)
    partial_at: int = 0  # buffer length when the last partial was decoded
    touched: float = field(default_factory=time.monotonic)

    @property
    def seconds(self) -> float:
        return len(self.pcm) / BYTES_PER_SECOND

    def partial_due(self) -> bool:
        return len(self.pcm) - self.partial_at >= PARTIAL_EVERY_SECONDS * BYTES_PER_SECOND


class Sessions:
    """At most one live session: a new hold supersedes an abandoned one, which
    keeps decode concurrency at one without a queue."""

    def __init__(self, max_seconds: int) -> None:
        self._max_bytes = max_seconds * BYTES_PER_SECOND
        self._lock = threading.Lock()
        self._current: Session | None = None

    def create(self, language: str | None, app: str | None, cfg: FlowSettings) -> Session:
        with self._lock:
            self._current = Session(secrets.token_urlsafe(12), language, app, cfg)
            return self._current

    def get(self, session_id: str) -> Session:
        with self._lock:
            s = self._current
            if s is None or s.id != session_id or time.monotonic() - s.touched > IDLE_TTL_SECONDS:
                raise MetisError(ErrorCode.NOT_FOUND, "dictation session not found or expired")
            s.touched = time.monotonic()
            return s

    def append(self, session_id: str, pcm: bytes) -> Session:
        s = self.get(session_id)
        if len(pcm) % 2:
            raise MetisError(ErrorCode.VALIDATION_FAILED, "PCM16 chunks must have an even byte length")
        if len(s.pcm) + len(pcm) > self._max_bytes:
            raise MetisError(ErrorCode.LIMIT_EXCEEDED, "dictation is over the maximum recording length")
        s.pcm += pcm
        return s

    def end(self, session_id: str) -> None:
        with self._lock:
            if self._current is not None and self._current.id == session_id:
                self._current = None
