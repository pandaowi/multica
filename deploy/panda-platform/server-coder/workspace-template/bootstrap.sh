#!/bin/bash
# Offline, additive bootstrap in the workspace. No provider credentials here.
set -euo pipefail
[ "$(id -u)" = 1000 ] || { echo 'Expected UID 1000' >&2; exit 1; }
kit=/opt/coder-workspace-kit
mkdir -p "$HOME/projects" "$HOME/.local/bin" "$HOME/.local/share" "$HOME/.config/coder-preview" "$HOME/.config/coder-platform" "$HOME/.local/state/coder-preview" "$HOME/.cache/coder-platform-fonts"
exec 9>"$HOME/.config/coder-platform/bootstrap.lock"
flock 9
release=$(cat "$kit/RELEASE")
workspace_id=${1:-$(cat "$HOME/.config/coder-preview/workspace-id" 2>/dev/null || true)}
[[ "$workspace_id" =~ ^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$ ]] || { echo 'Expected workspace UUID' >&2; exit 1; }
if [ -e "$HOME/.config/coder-preview/workspace-id" ]; then
 [ "$(cat "$HOME/.config/coder-preview/workspace-id")" = "$workspace_id" ] || { echo 'Workspace identity mismatch' >&2; exit 1; }
fi
printf '%s\n' "$workspace_id" > "$HOME/.config/coder-preview/workspace-id"
chmod 600 "$HOME/.config/coder-preview/workspace-id"
target="$HOME/.local/share/coder-development-tools"
if [ -e "$target" ] || [ -L "$target" ]; then
 [ -L "$target" ] && [ "$(readlink "$target")" = "$kit/tools" ] || { echo 'Refusing unmanaged helper replacement' >&2; exit 1; }
else
 ln -s "$kit/tools" "$target"
fi
install -m 700 "$kit/coder-dev" "$HOME/.local/bin/coder-dev"
for helper in coder-preview coder-preview-link coder-publish; do
 install -m 700 "$kit/$helper" "$HOME/.local/bin/$helper"
done
# Keep the launcher multi-app. Refuse an unknown launcher instead of overwriting it.
launcher="$HOME/.config/coder-preview/start.sh"
if [ -e "$launcher" ] && ! grep -q 'coder-platform managed launcher' "$launcher"; then
 echo 'Refusing unmanaged preview launcher replacement' >&2; exit 1
fi
cat > "$launcher" <<'LAUNCH'
#!/bin/sh
# coder-platform managed launcher
exec "$HOME/.local/bin/coder-preview" serve
LAUNCH
chmod 700 "$launcher"
install -m 600 "$kit/workspace-instructions.md" "$HOME/.config/coder-platform/instructions.md"
if [ ! -e "$HOME/projects/AGENTS.md" ]; then
 install -m 600 "$kit/AGENTS.md" "$HOME/projects/AGENTS.md"
fi
# Existing project instructions/source/state are never replaced on restart.
node "$target/cli.mjs" version
export PLAYWRIGHT_BROWSERS_PATH="$kit/browsers"
node "$target/cli.mjs" browser doctor
printf '%s\n' "$release" > "$HOME/.config/coder-platform/release"
chmod 600 "$HOME/.config/coder-platform/release"
# flock prevents duplicate daemons; this process does not own any individual app.
nohup bash "$launcher" >>"$HOME/.coder-preview.log" 2>&1 </dev/null 9>&- &
printf '%s\n' 'coder-platform bootstrap ready'
