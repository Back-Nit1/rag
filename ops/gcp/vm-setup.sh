#!/usr/bin/env bash
# Runs ON the VM as root (invoked by deploy.sh). Idempotent: re-run to redeploy.
# Installs the release tarball at /tmp/metis.tar.gz and (re)starts three
# loopback-only services. Nothing listens on a routable interface.
set -euo pipefail
: "${HOST:?set HOST (<ip>.sslip.io)}"

APP=/opt/metis
DATA=/var/lib/metis
MODELS=/opt/models
MODEL_ID=small.en
MODEL_REPO=Systran/faster-whisper-small.en

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends libsndfile1 ca-certificates curl caddy

id metis >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin metis
mkdir -p "$DATA" "$MODELS"

command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

rm -rf "$APP" && mkdir -p "$APP"
tar -xzf /tmp/metis.tar.gz -C "$APP"
cd "$APP"
# System python 3.12 (Ubuntu 24.04) so the venv is readable by the metis user.
uv sync --all-packages --no-dev --extra asr --frozen --python /usr/bin/python3.12

# Model weights are fetched once, at deploy time, pinned to the commit resolved
# here. The transcriber runs local_files_only and never downloads per request.
REV_FILE="$MODELS/$MODEL_ID.revision"
if [ ! -f "$REV_FILE" ]; then
  .venv/bin/python - "$MODEL_REPO" "$MODELS/$MODEL_ID" "$REV_FILE" <<'PY'
import pathlib, sys
from huggingface_hub import HfApi, snapshot_download

repo, target, rev_file = sys.argv[1:]
sha = HfApi().model_info(repo).sha
snapshot_download(repo_id=repo, revision=sha, local_dir=target)
pathlib.Path(rev_file).write_text(sha)
PY
fi

cat >/etc/metis.env <<EOF
METIS_MODE=local
METIS_ENV=dev
METIS_ALLOW_FIXTURE_IDENTITY=true
METIS_FIXTURE_USER_ID=demo_authenticated_subject
METIS_DESKTOP_IDENTITY_PATH=$DATA/install_id
METIS_ALLOWED_HOSTS=$HOST
METIS_SQLITE_PATH=$DATA/metis.sqlite
METIS_LOCAL_OBJECTS_DIR=$DATA/objects
METIS_CORS_ORIGINS=http://localhost:5173,https://metis-delta.vercel.app
METIS_SPEECH_ENGINE=faster_whisper
METIS_SPEECH_MODEL_DIR=$MODELS
METIS_SPEECH_MODEL_ID=$MODEL_ID
METIS_SPEECH_ARTIFACT_REVISION=$(cat "$REV_FILE")
HF_HUB_OFFLINE=1
EOF

# Single-user access key. With METIS_DESKTOP_AUTH_SECRET set the API rejects any
# request whose bearer is not exactly this value and ignores fixture identity.
[ -f /etc/metis-api.env ] || (
  umask 077
  echo "METIS_DESKTOP_AUTH_SECRET=$(head -c 36 /dev/urandom | base64 | tr -d '=+/\n')" >/etc/metis-api.env
)

# Dictation ("Flow") ASR server: loopback-only, bearer secret generated once.
[ -f /etc/metis-speech.env ] || (
  umask 077
  echo "METIS_SPEECH_SECRET=$(head -c 32 /dev/urandom | base64 | tr -d '=+/\n')" >/etc/metis-speech.env
)

unit() { # name, ExecStart
  cat >"/etc/systemd/system/$1.service" <<EOF
[Unit]
Description=$1
After=network.target

[Service]
User=metis
WorkingDirectory=$APP
EnvironmentFile=/etc/metis.env
EnvironmentFile=-/etc/metis-speech.env
EnvironmentFile=-/etc/metis-api.env
ExecStart=$2
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$DATA
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
}
unit metis-api "$APP/.venv/bin/uvicorn metis_api.main:app --host 127.0.0.1 --port 8000"
unit metis-worker "$APP/.venv/bin/python -m metis_pipeline.local_worker"
unit metis-speech "$APP/.venv/bin/python -m metis_speech.server --port 8787"
unit metis-web "/usr/bin/python3 -m http.server 5173 --bind 127.0.0.1 --directory $APP/apps/web/dist"

# Public edge: TLS + reverse proxy to the loopback API only.
cat >/etc/caddy/Caddyfile <<CADDY
$HOST {
	@docs path /docs* /redoc* /openapi.json
	respond @docs 404
	reverse_proxy 127.0.0.1:8000
}
CADDY
systemctl enable -q caddy
systemctl reload-or-restart caddy

chown -R metis:metis "$DATA"
chown -R root:root "$APP" "$MODELS" && chmod -R a+rX "$APP" "$MODELS"
systemctl daemon-reload
systemctl enable -q metis-api metis-worker metis-speech metis-web
systemctl restart metis-api metis-worker metis-speech metis-web

KEY="$(sed -n 's/^METIS_DESKTOP_AUTH_SECRET=//p' /etc/metis-api.env)"
for _ in $(seq 30); do
  curl -fsS -H "Authorization: Bearer $KEY" http://127.0.0.1:8000/v1/health && echo && exit 0
  sleep 1
done
journalctl -u metis-api -n 40 --no-pager
exit 1
