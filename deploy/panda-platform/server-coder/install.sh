#!/bin/sh
# Jalankan --env PATH plan/render terlebih dahulu. Tidak membaca .env sebagai shell.
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$base/installer/main.py" "$@"
