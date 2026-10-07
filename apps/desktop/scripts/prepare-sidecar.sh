#!/bin/sh
# Build the health sidecar and place it where Tauri's externalBin expects it:
# src-tauri/binaries/<name>-<host-target-triple>. Output is gitignored.
set -eu
cd "$(dirname "$0")/.."
export PATH="$HOME/.cargo/bin:$PATH"
triple=$(rustc -vV | sed -n 's/^host: //p')
cargo build --release -p metis-health-sidecar
ext=""; case "$triple" in *windows*) ext=".exe";; esac
cp "target/release/metis-health-sidecar$ext" "src-tauri/binaries/metis-health-sidecar-$triple$ext"
