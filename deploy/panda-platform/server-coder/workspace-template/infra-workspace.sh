#!/bin/bash
# One operator entry point, from this source checkout or the installed host bundle.
set -euo pipefail
base=$(cd -- "$(dirname -- "$0")/.." && pwd)
if [ -f "$base/workspace-template/infra/manage.py" ] && [ "$(id -un)" = coder ] && [ -S "/run/user/$(id -u)/docker.sock" ]; then
 exec python3 "$base/workspace-template/infra/manage.py" "$@"
fi
host=${CODER_INFRA_SSH_HOST:-coder@10.0.0.10}
ssh_args=(-o ConnectTimeout=25 -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
if [ -n "${CODER_INFRA_SSH_CONFIG:-}" ]; then ssh_args+=(-F "$CODER_INFRA_SSH_CONFIG"); fi
# Ship only reviewed platform sources, never state, credentials or customer files.
bundle=$(mktemp -d)
trap 'rm -rf "$bundle"' EXIT
mkdir -p "$bundle/server-coder"
for directory in workspace-template development-tools; do
 cp -R "$base/$directory" "$bundle/server-coder/"
done
mkdir -p "$bundle/server-coder/app-platform" "$bundle/server-coder/observability"
for f in coder-preview coder-preview-link coder-publish; do cp "$base/app-platform/$f" "$bundle/server-coder/app-platform/"; done
for f in observer.py host_snapshot.py workspace_snapshot.py kernel_oom.py coder-workspace-observer.service; do cp "$base/observability/$f" "$bundle/server-coder/observability/"; done
find "$bundle" -name __pycache__ -type d -exec rm -rf {} +
# Quote every argument; no shell interpolation of workspace/user input.
args=''
for arg in "$@"; do printf -v quoted '%q' "$arg"; args+=" $quoted"; done
stamp=$(date +%Y%m%d%H%M%S)-$$
remote="/opt/coder/workspace-kit/releases/source-$stamp"
ssh "${ssh_args[@]}" "$host" "umask 077; mkdir -p '$remote'"
tar -cf - -C "$bundle/server-coder" . | ssh "${ssh_args[@]}" "$host" "tar -xf - -C '$remote'"
ssh "${ssh_args[@]}" "$host" "python3 '$remote/workspace-template/infra/manage.py'$args"
