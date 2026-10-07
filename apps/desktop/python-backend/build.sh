#!/bin/sh
# Freeze the Metis services into ONE executable: src-tauri/binaries/metis-backend-<triple>.
# Needs uv + network for PyInstaller on first run. Never downloads model weights.
# Env: METIS_BACKEND_ASR=1 (default) bundles faster-whisper if it resolves;
#      METIS_BACKEND_ASR=0 builds without it (speech then reports `failed` honestly).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
out="$here/../src-tauri/binaries"
work="$here/.build"
export PATH="$HOME/.cargo/bin:$PATH"
triple=$(rustc -vV | sed -n 's/^host: //p')
ext=""; case "$triple" in *windows*) ext=".exe";; esac
asr="${METIS_BACKEND_ASR:-1}"

mkdir -p "$out" "$work"
cd "$repo"
export UV_PROJECT_ENVIRONMENT="$work/venv"
extra=""
[ "$asr" = "1" ] && extra="--extra asr"
# --frozen: use uv.lock as is. Python 3.12 is where the pinned ctranslate2 wheel exists.
uv sync --frozen --no-dev --python 3.12 --all-packages $extra
uv pip install --python "$work/venv/bin/python" 'pyinstaller==6.*'
# faster-whisper 1.0.3 imports `requests` but the locked huggingface-hub no longer brings it.
# Build-time workaround until the asr extra declares it (coordinator request).
[ "$asr" = "1" ] && uv pip install --python "$work/venv/bin/python" 'requests>=2.31'

collect=""
if [ "$asr" = "1" ]; then
  collect="--collect-all faster_whisper --collect-all ctranslate2 --collect-all av --collect-all tokenizers"
fi
"$work/venv/bin/python" -m PyInstaller --noconfirm --clean --onefile \
  --name "metis-backend" --distpath "$work/dist" --workpath "$work/pyi" --specpath "$work" \
  --paths "$here" \
  --collect-submodules metis_api --collect-submodules metis_pipeline --collect-submodules metis_state \
  --collect-submodules metis_scheduler --collect-submodules metis_retrieval --collect-submodules metis_planner \
  --collect-submodules metis_speech --collect-all metis_contracts --collect-submodules uvicorn \
  --hidden-import uvloop --hidden-import httptools --hidden-import websockets \
  $collect "$here/metis_backend.py"
cp "$work/dist/metis-backend$ext" "$out/metis-backend-$triple$ext"
echo "built $out/metis-backend-$triple$ext (asr=$asr)"
