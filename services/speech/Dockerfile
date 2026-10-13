# Metis owned speech-to-text worker.
#
# Self-hosted pretrained open-source Whisper weights via faster-whisper /
# CTranslate2 on CPU. No commercial speech-provider API key is required.
#
# Model artifacts are downloaded at BUILD time, pinned by revision and
# checksum, and baked into the image. Nothing is fetched from the public
# internet per transcription, so the running task needs no egress path for
# model files (and therefore no NAT gateway purely to fetch weights).
#
#   docker build -t metis-speech:dev \
#     --build-arg MODEL_ID=small.en \
#     --build-arg MODEL_REVISION=<hf commit sha> \
#     services/speech

FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_DISABLE_TELEMETRY=1

# ffmpeg libraries only — PyAV links against them; the ffmpeg CLI is not
# installed, because no user input is ever handed to a shell.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libsndfile1 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# ---- model artifacts -------------------------------------------------------
FROM base AS models

ARG MODEL_ID=small.en
ARG MODEL_REPO=Systran/faster-whisper-small.en
ARG MODEL_REVISION=main
ARG MODEL_SHA256=""

RUN pip install --no-cache-dir "huggingface_hub==0.25.2"
RUN python - <<'PY'
import os, hashlib, pathlib
from huggingface_hub import snapshot_download

repo = os.environ.get("MODEL_REPO") or "Systran/faster-whisper-small.en"
revision = os.environ.get("MODEL_REVISION") or "main"
target = pathlib.Path("/opt/models") / (os.environ.get("MODEL_ID") or "small.en")
path = snapshot_download(repo_id=repo, revision=revision, local_dir=str(target))

expected = (os.environ.get("MODEL_SHA256") or "").strip()
weights = target / "model.bin"
if weights.is_file():
    digest = hashlib.sha256(weights.read_bytes()).hexdigest()
    print(f"model.bin sha256={digest}")
    if expected and digest != expected:
        raise SystemExit(f"checksum mismatch: expected {expected}, got {digest}")
    elif not expected:
        print("WARNING: MODEL_SHA256 was not supplied; pin it for reproducible builds")
PY
ENV MODEL_REPO=${MODEL_REPO} MODEL_REVISION=${MODEL_REVISION} MODEL_ID=${MODEL_ID} MODEL_SHA256=${MODEL_SHA256}

# ---- runtime ---------------------------------------------------------------
FROM base AS runtime

ARG MODEL_ID=small.en
ARG MODEL_REVISION=main

COPY --from=models /opt/models /opt/models

WORKDIR /app
COPY packages/contracts /app/packages/contracts
COPY services/state /app/services/state
COPY services/speech /app/services/speech

# Pinned: the decode path must be reproducible across image builds.
RUN pip install --no-cache-dir \
      "faster-whisper==1.0.3" \
      "ctranslate2==4.4.0" \
      "av==12.3.0" \
      "numpy<2" \
      "boto3>=1.35,<2" \
      "pydantic>=2.9,<3" \
 && pip install --no-cache-dir --no-deps \
      /app/packages/contracts /app/services/state /app/services/speech

# One inference job per container; CPU threads are bounded by the task's vCPUs.
ENV METIS_MODE=cloud \
    METIS_SPEECH_MODEL_DIR=/opt/models \
    METIS_SPEECH_MODEL_ID=${MODEL_ID} \
    METIS_SPEECH_ARTIFACT_REVISION=${MODEL_REVISION} \
    METIS_SPEECH_COMPUTE_TYPE=int8 \
    METIS_SPEECH_BEAM_SIZE=1 \
    METIS_SPEECH_VAD=true \
    OMP_NUM_THREADS=2

RUN useradd --create-home --uid 10001 metis && chown -R metis:metis /app /opt/models
USER metis

# Fails fast if the model directory is missing or the libraries cannot load,
# rather than discovering it mid-job.
HEALTHCHECK --interval=30s --timeout=10s --retries=2 \
  CMD python -c "import pathlib,sys; sys.exit(0 if any(pathlib.Path('/opt/models').iterdir()) else 1)"

ENTRYPOINT ["python", "-m", "metis_speech.worker"]
