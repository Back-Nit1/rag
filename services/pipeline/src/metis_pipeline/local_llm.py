"""Local model runtime (Ollama) behind the existing inference/embedding ports.

Chosen runtime: Ollama's loopback HTTP API. It gives schema-constrained output
(`format` = JSON Schema), a separate embeddings endpoint, and a model listing
we use for identity and health, all over plain HTTP so a test can stand in a
fake server. Metis never calls `/api/pull`: a missing model is reported, not
fetched.

Configuration (all env, none required in fixture mode):

* ``METIS_INFERENCE_ENGINE`` — ``auto`` (default: mode-based), ``fixture``,
  ``local`` or ``cloud``.
* ``METIS_LOCAL_LLM_ENDPOINT`` — loopback ``http://127.0.0.1:<port>`` only.
* ``METIS_LOCAL_LLM_MODEL`` / ``METIS_LOCAL_EMBED_MODEL`` — explicit, no default.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from metis_contracts import AdapterMode, ErrorCode, MetisError, settings

from .inference import extract_json, rough_token_count

ENGINES = ("auto", "fixture", "local", "cloud")
DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def inference_engine() -> str:
    """Resolve the engine: explicit selection, else the METIS_MODE default."""
    engine = (os.environ.get("METIS_INFERENCE_ENGINE") or "auto").strip().lower()
    if engine not in ENGINES:
        raise ValueError(f"METIS_INFERENCE_ENGINE must be one of {ENGINES}; got {engine!r}")
    if engine == "auto":
        return "cloud" if settings().uses_real_inference else "fixture"
    return engine


@dataclass(frozen=True)
class LocalConfig:
    endpoint: str
    llm_model: str | None
    embed_model: str | None

    @classmethod
    def from_env(cls) -> LocalConfig:
        endpoint = (os.environ.get("METIS_LOCAL_LLM_ENDPOINT") or DEFAULT_ENDPOINT).rstrip("/")
        parts = urlsplit(endpoint)
        if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost") or not parts.port:
            raise ValueError("METIS_LOCAL_LLM_ENDPOINT must be http://127.0.0.1:<port> (loopback only)")
        return cls(
            endpoint=endpoint,
            llm_model=os.environ.get("METIS_LOCAL_LLM_MODEL") or None,
            embed_model=os.environ.get("METIS_LOCAL_EMBED_MODEL") or None,
        )


class OllamaHttp:
    """Thin bounded JSON-over-HTTP caller. Every failure becomes a MetisError."""

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    def call(self, path: str, body: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            self.endpoint + path,
            data=data,
            method="GET" if body is None else "POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            detail = exc.read(500).decode(errors="replace")
            if exc.code == 404:
                raise MetisError(
                    ErrorCode.PROVIDER_UNAVAILABLE,
                    f"local runtime has no such model (not pulled; Metis never downloads): {detail}",
                ) from exc
            raise MetisError(
                ErrorCode.PROVIDER_UNAVAILABLE, f"local runtime returned HTTP {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MetisError(
                ErrorCode.PROVIDER_UNAVAILABLE, f"local runtime unreachable at {self.endpoint}: {exc}"
            ) from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise MetisError(ErrorCode.SEMANTIC_VALIDATION_FAILED, "local runtime response too large")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MetisError(
                ErrorCode.SEMANTIC_VALIDATION_FAILED, f"local runtime returned non-JSON: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise MetisError(ErrorCode.SEMANTIC_VALIDATION_FAILED, "local runtime returned a non-object")
        return parsed

    def installed_models(self, timeout: float = 2.0) -> set[str]:
        tags = self.call("/api/tags", None, timeout)
        return {str(m.get("name")) for m in tags.get("models", []) if isinstance(m, dict)}


def _require_model(model: str | None, env_name: str) -> str:
    if not model:
        raise MetisError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            f"{env_name} is not set; choose a locally installed model explicitly",
        )
    return model


def _has_model(installed: set[str], model: str) -> bool:
    return model in installed or f"{model}:latest" in installed


class LocalInferenceClient:
    """Ollama chat with schema-constrained JSON. Same port as Bedrock/fixture."""

    mode = AdapterMode.LOCAL

    def __init__(self, config: LocalConfig | None = None) -> None:
        self._cfg = config or LocalConfig.from_env()
        self.model_id = _require_model(self._cfg.llm_model, "METIS_LOCAL_LLM_MODEL")
        self._http = OllamaHttp(self._cfg.endpoint)

    def health(self) -> tuple[bool, str]:
        """(ok, detail) without running inference. Never raises."""
        try:
            installed = self._http.installed_models()
        except MetisError as exc:
            return False, exc.message
        if not _has_model(installed, self.model_id):
            return False, f"model {self.model_id!r} is not installed locally"
        return True, f"model {self.model_id!r} installed"

    def complete_json(
        self,
        *,
        prompt_version: str,
        system: str,
        user_content: str,
        json_schema: dict[str, Any],
        max_input_tokens: int,
        max_output_tokens: int,
        timeout_seconds: float = 30.0,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        estimated_in = rough_token_count(system) + rough_token_count(user_content)
        if estimated_in > max_input_tokens:
            raise MetisError(
                ErrorCode.LIMIT_EXCEEDED,
                f"input is ~{estimated_in} tokens, over the {max_input_tokens} cap; split the source",
            )
        started = time.monotonic()
        max_attempts = 1 + max(0, settings().limits.extraction_repair_attempts)
        content = user_content
        last_error = ""
        in_tokens = out_tokens = 0
        for attempt in range(1, max_attempts + 1):
            reply = self._http.call(
                "/api/chat",
                {
                    "model": self.model_id,
                    "stream": False,
                    "format": json_schema,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": content},
                    ],
                    "options": {"temperature": 0, "num_predict": max_output_tokens},
                },
                timeout_seconds,
            )
            in_tokens += int(reply.get("prompt_eval_count") or estimated_in)
            out_tokens += int(reply.get("eval_count") or 0)
            if reply.get("done_reason") == "length":
                raise MetisError(
                    ErrorCode.SEMANTIC_VALIDATION_FAILED,
                    "the model response hit the output cap and was truncated",
                )
            text = (reply.get("message") or {}).get("content", "")
            try:
                parsed = extract_json(str(text))
            except MetisError as exc:
                last_error = exc.message
                content = (
                    f"{user_content}\n\n---\nThe previous response was rejected: {exc.message}. "
                    "Return only a JSON object matching the schema."
                )
                continue
            return parsed, {
                "model_id": self.model_id,
                "adapter_mode": self.mode.value,
                "prompt_version": prompt_version,
                "input_tokens": in_tokens,
                "output_tokens": out_tokens,
                "cache_read_tokens": 0,
                "attempts": attempt,
                "latency_seconds": round(time.monotonic() - started, 4),
                "micro_usd": 0,
                "structured_output": True,
            }
        raise MetisError(
            ErrorCode.SEMANTIC_VALIDATION_FAILED,
            f"the local model did not return valid JSON after {max_attempts} attempts: {last_error}",
        )

    def estimate_micro_usd(self, *, input_tokens: int, output_tokens: int) -> int:
        return 0
