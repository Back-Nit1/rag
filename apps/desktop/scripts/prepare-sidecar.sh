#!/bin/sh
# Make the workspace buildable and testable:
#  - src-tauri/binaries/metis-backend-<triple>: the REAL packaged backend that
#    Tauri's externalBin requires. Built once (slow, needs uv + network for
#    PyInstaller) and reused; delete it or run python-backend/build.sh to rebuild.
#  - target/debug/metis-health-sidecar: test-only fake backend (never bundled).
set -eu
cd "$(dirname "$0")/.."
export PATH="$HOME/.cargo/bin:$PATH"
triple=$(rustc -vV | sed -n 's/^host: //p')
ext=""; case "$triple" in *windows*) ext=".exe";; esac
if [ ! -f "src-tauri/binaries/metis-backend-$triple$ext" ]; then
  sh python-backend/build.sh
fi
cargo build -p metis-health-sidecar
