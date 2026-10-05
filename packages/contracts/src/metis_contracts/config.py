"""Environment-driven settings, validated once at process start.

Limits live here rather than scattered across workers so a single place
answers "what is the maximum upload?" for the browser, the API and the ASR
worker. `METIS_MODE` selects the adapter set:

* ``local``   — SQLite state, filesystem objects, fixture inference, fixture ASR
* ``hybrid``  — local state, real Bedrock/ASR; for development against AWS
* ``cloud``   — DynamoDB, S3, Bedrock, ECS ASR, EventBridge Scheduler

Production refuses fixture identity and fixture inference: a mock must never
masquerade as a completed production result.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from typing import Literal


class Mode(StrEnum):
    LOCAL = "local"
    HYBRID = "hybrid"
    CLOUD = "cloud"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return default if raw is None else float(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Limits:
    """Validated centrally; the browser reads these through /v1/health."""

    max_audio_seconds: int = field(default_factory=lambda: _env_int("METIS_MAX_AUDIO_SECONDS", 300))
    max_upload_bytes: int = field(default_factory=lambda: _env_int("METIS_MAX_UPLOAD_BYTES", 25 * 1024 * 1024))
    max_text_chars: int = field(default_factory=lambda: _env_int("METIS_MAX_TEXT_CHARS", 100_000))
    upload_url_ttl_seconds: int = field(default_factory=lambda: _env_int("METIS_UPLOAD_TTL_SECONDS", 900))

    extraction_max_input_tokens: int = field(default_factory=lambda: _env_int("METIS_EXTRACT_IN_TOKENS", 4000))
    extraction_max_output_tokens: int = field(default_factory=lambda: _env_int("METIS_EXTRACT_OUT_TOKENS", 1200))
    extraction_repair_attempts: int = field(default_factory=lambda: _env_int("METIS_EXTRACT_REPAIRS", 1))
    extraction_max_ranges: int = field(default_factory=lambda: _env_int("METIS_EXTRACT_MAX_RANGES", 6))

    planning_max_input_tokens: int = field(default_factory=lambda: _env_int("METIS_PLAN_IN_TOKENS", 8000))
    planning_max_output_tokens: int = field(default_factory=lambda: _env_int("METIS_PLAN_OUT_TOKENS", 2000))
    planning_max_candidate_tasks: int = field(default_factory=lambda: _env_int("METIS_PLAN_CANDIDATES", 8))
    planning_max_tool_iterations: int = field(default_factory=lambda: _env_int("METIS_PLAN_TOOL_ITERS", 4))
    planning_read_page_limit: int = field(default_factory=lambda: _env_int("METIS_PLAN_READ_PAGES", 20))
    planning_epoch_retries: int = field(default_factory=lambda: _env_int("METIS_PLAN_EPOCH_RETRIES", 3))

    retrieval_candidates: int = field(default_factory=lambda: _env_int("METIS_RETRIEVAL_CANDIDATES", 20))
    retrieval_evidence_per_task: int = field(default_factory=lambda: _env_int("METIS_EVIDENCE_PER_TASK", 6))
    retrieval_evidence_token_budget: int = field(default_factory=lambda: _env_int("METIS_EVIDENCE_TOKENS", 3000))

    model_timeout_seconds: float = field(default_factory=lambda: _env_float("METIS_MODEL_TIMEOUT", 30.0))
    speech_wall_clock_seconds: int = field(default_factory=lambda: _env_int("METIS_SPEECH_TIMEOUT", 900))
    speech_concurrency: int = field(default_factory=lambda: _env_int("METIS_SPEECH_CONCURRENCY", 1))

    job_max_attempts: int = field(default_factory=lambda: _env_int("METIS_JOB_MAX_ATTEMPTS", 5))
    job_lease_seconds: int = field(default_factory=lambda: _env_int("METIS_JOB_LEASE_SECONDS", 300))
    outbox_batch: int = field(default_factory=lambda: _env_int("METIS_OUTBOX_BATCH", 10))

    daily_micro_usd: int = field(default_factory=lambda: _env_int("METIS_DAILY_MICRO_USD", 500_000))
    monthly_micro_usd: int = field(default_factory=lambda: _env_int("METIS_MONTHLY_MICRO_USD", 10_000_000))

    api_rate_per_minute: int = field(default_factory=lambda: _env_int("METIS_RATE_PER_MINUTE", 120))

    source_retention_days: int = field(default_factory=lambda: _env_int("METIS_SOURCE_RETENTION_DAYS", 30))

    allowed_audio_types: tuple[str, ...] = (
        "audio/webm",
        "audio/webm;codecs=opus",
        "audio/ogg",
        "audio/ogg;codecs=opus",
        "audio/mp4",
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
    )

    def audio_type_allowed(self, content_type: str | None) -> bool:
        if not content_type:
            return False
        base = content_type.split(";")[0].strip().lower()
        return any(allowed.split(";")[0] == base for allowed in self.allowed_audio_types)


@dataclass(frozen=True)
class ModelConfig:
    """Extraction, planning and embedding model ids are independent on purpose.

    Prices are configuration, never hard-coded facts: verify the region's rate
    card before trusting an estimate derived from them.
    """

    extraction_model_id: str = field(default_factory=lambda: _env("METIS_EXTRACTION_MODEL_ID", "amazon.nova-micro-v1:0") or "")
    escalation_model_id: str | None = field(default_factory=lambda: _env("METIS_ESCALATION_MODEL_ID"))
    planning_model_id: str = field(default_factory=lambda: _env("METIS_PLANNING_MODEL_ID", "amazon.nova-lite-v1:0") or "")
    embedding_model_id: str = field(default_factory=lambda: _env("METIS_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0") or "")
    embedding_dimensions: int = field(default_factory=lambda: _env_int("METIS_EMBEDDING_DIMENSIONS", 512))
    embedding_normalize: bool = field(default_factory=lambda: _env_bool("METIS_EMBEDDING_NORMALIZE", True))
    allow_escalation: bool = field(default_factory=lambda: _env_bool("METIS_ALLOW_ESCALATION", False))

    input_micro_usd_per_1k: int = field(default_factory=lambda: _env_int("METIS_INPUT_MICRO_USD_PER_1K", 35))
    output_micro_usd_per_1k: int = field(default_factory=lambda: _env_int("METIS_OUTPUT_MICRO_USD_PER_1K", 140))
    embedding_micro_usd_per_1k: int = field(default_factory=lambda: _env_int("METIS_EMBED_MICRO_USD_PER_1K", 2))

    prompt_version_extraction: str = "extract/v1"
    prompt_version_planning: str = "plan/v1"


@dataclass(frozen=True)
class SpeechConfig:
    model_id: str = field(default_factory=lambda: _env("METIS_SPEECH_MODEL_ID", "small.en") or "")
    model_dir: str = field(default_factory=lambda: _env("METIS_SPEECH_MODEL_DIR", "/opt/models") or "")
    artifact_revision: str = field(default_factory=lambda: _env("METIS_SPEECH_ARTIFACT_REVISION", "unpinned") or "")
    compute_type: str = field(default_factory=lambda: _env("METIS_SPEECH_COMPUTE_TYPE", "int8") or "")
    beam_size: int = field(default_factory=lambda: _env_int("METIS_SPEECH_BEAM_SIZE", 1))
    temperature: float = field(default_factory=lambda: _env_float("METIS_SPEECH_TEMPERATURE", 0.0))
    vad_enabled: bool = field(default_factory=lambda: _env_bool("METIS_SPEECH_VAD", True))
    condition_on_previous_text: bool = field(default_factory=lambda: _env_bool("METIS_SPEECH_CONDITION_PREV", False))
    cpu_threads: int = field(default_factory=lambda: _env_int("METIS_SPEECH_CPU_THREADS", 0))
    language: str | None = field(default_factory=lambda: _env("METIS_SPEECH_LANGUAGE", "en"))


@dataclass(frozen=True)
class AwsConfig:
    region: str = field(default_factory=lambda: _env("AWS_REGION", "us-east-1") or "")
    table_name: str | None = field(default_factory=lambda: _env("METIS_TABLE_NAME"))
    sources_bucket: str | None = field(default_factory=lambda: _env("METIS_SOURCES_BUCKET"))
    vector_bucket: str | None = field(default_factory=lambda: _env("METIS_VECTOR_BUCKET"))
    vector_index: str | None = field(default_factory=lambda: _env("METIS_VECTOR_INDEX"))
    ingest_queue_url: str | None = field(default_factory=lambda: _env("METIS_INGEST_QUEUE_URL"))
    scheduler_group: str | None = field(default_factory=lambda: _env("METIS_SCHEDULER_GROUP"))
    scheduler_role_arn: str | None = field(default_factory=lambda: _env("METIS_SCHEDULER_ROLE_ARN"))
    reminder_target_arn: str | None = field(default_factory=lambda: _env("METIS_REMINDER_TARGET_ARN"))
    speech_state_machine_arn: str | None = field(default_factory=lambda: _env("METIS_SPEECH_STATE_MACHINE_ARN"))
    cognito_user_pool_id: str | None = field(default_factory=lambda: _env("METIS_COGNITO_USER_POOL_ID"))
    cognito_client_id: str | None = field(default_factory=lambda: _env("METIS_COGNITO_CLIENT_ID"))
    vapid_public_key: str | None = field(default_factory=lambda: _env("METIS_VAPID_PUBLIC_KEY"))
    vapid_private_key_param: str | None = field(default_factory=lambda: _env("METIS_VAPID_PRIVATE_KEY_PARAM"))


@dataclass(frozen=True)
class Settings:
    mode: Mode = field(default_factory=lambda: Mode(_env("METIS_MODE", "local") or "local"))
    environment: Literal["dev", "staging", "prod"] = field(
        default_factory=lambda: _env("METIS_ENV", "dev")  # type: ignore[return-value]
    )
    allow_fixture_identity: bool = field(default_factory=lambda: _env_bool("METIS_ALLOW_FIXTURE_IDENTITY", True))
    fixture_user_id: str = field(default_factory=lambda: _env("METIS_FIXTURE_USER_ID", "demo_authenticated_subject") or "")
    sqlite_path: str = field(default_factory=lambda: _env("METIS_SQLITE_PATH", ".metis/metis.sqlite") or "")
    local_objects_dir: str = field(default_factory=lambda: _env("METIS_LOCAL_OBJECTS_DIR", ".metis/objects") or "")
    default_timezone: str = field(default_factory=lambda: _env("METIS_DEFAULT_TIMEZONE", "America/New_York") or "")
    log_level: str = field(default_factory=lambda: _env("METIS_LOG_LEVEL", "INFO") or "")
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(filter(None, (_env("METIS_CORS_ORIGINS", "http://localhost:5173") or "").split(",")))
    )

    limits: Limits = field(default_factory=Limits)
    models: ModelConfig = field(default_factory=ModelConfig)
    speech: SpeechConfig = field(default_factory=SpeechConfig)
    aws: AwsConfig = field(default_factory=AwsConfig)

    def __post_init__(self) -> None:
        if self.environment == "prod":
            # A production deployment must not be able to fall back to a
            # fixture identity or a fixture model when auth or Bedrock fails.
            if self.allow_fixture_identity:
                raise ValueError("METIS_ALLOW_FIXTURE_IDENTITY must be false when METIS_ENV=prod")
            if self.mode is not Mode.CLOUD:
                raise ValueError("METIS_ENV=prod requires METIS_MODE=cloud")
            missing = [
                name
                for name, value in (
                    ("METIS_TABLE_NAME", self.aws.table_name),
                    ("METIS_SOURCES_BUCKET", self.aws.sources_bucket),
                    ("METIS_COGNITO_USER_POOL_ID", self.aws.cognito_user_pool_id),
                    ("METIS_COGNITO_CLIENT_ID", self.aws.cognito_client_id),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"production configuration is missing: {', '.join(missing)}")

    @property
    def is_cloud(self) -> bool:
        return self.mode is Mode.CLOUD

    @property
    def uses_real_inference(self) -> bool:
        return self.mode in (Mode.CLOUD, Mode.HYBRID)


@lru_cache(maxsize=1)
def settings() -> Settings:
    """Process-wide settings. Tests clear the cache after mutating os.environ."""
    return Settings()


def reset_settings_cache() -> None:
    settings.cache_clear()
