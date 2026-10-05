#!/usr/bin/env bash
# Local development: FastAPI + the outbox/job worker loop, plus the web client
# when it exists. Fixture adapters only — no cloud account, no provider key.
#
# Targets bash 3.2 (the macOS system bash), so no `wait -n` and no array
# expansion under `set -u`.
set -euo pipefail
cd "$(dirname "$0")/.."

export METIS_MODE="${METIS_MODE:-local}"
UV="${UV:-uv}"
PNPM="${PNPM:-pnpm}"
API_PORT="${METIS_API_PORT:-8000}"

pids=""

cleanup() {
  trap - EXIT INT TERM
  for pid in $pids; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "metis dev: METIS_MODE=$METIS_MODE"

"$UV" run uvicorn metis_api.main:app --reload --port "$API_PORT" &
pids="$pids $!"

"$UV" run python -m metis_pipeline.local_worker &
pids="$pids $!"

if [ -f apps/web/package.json ]; then
  "$PNPM" --filter @metis/web dev &
  pids="$pids $!"
else
  echo "metis dev: apps/web/package.json is not present yet — API + worker only" >&2
fi

wait
