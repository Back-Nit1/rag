#!/bin/sh
# Prototype: freeze health.py into a single executable named for Tauri externalBin.
set -eu
cd "$(dirname "$0")"
triple=$(rustc -vV | sed -n 's/^host: //p')
uvx --python 3.12 pyinstaller==6.* --onefile --name "metis-py-health-$triple" --distpath dist health.py
