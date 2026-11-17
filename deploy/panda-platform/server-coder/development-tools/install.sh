#!/bin/bash
# Run as UID 1000 inside the approved workspace. No host/root package changes.
set -euo pipefail
[ "$(id -u)" = 1000 ] || { echo 'Expected workspace UID 1000' >&2; exit 1; }
source_dir=$(cd -- "$(dirname -- "$0")" && pwd)
target="$HOME/.local/share/coder-development-tools"
mkdir -p "$target" "$HOME/.local/bin"
chmod 700 "$target"
for file in cli.mjs jobs.mjs package.json package-lock.json; do
  [ "$source_dir" = "$target" ] || install -m 600 "$source_dir/$file" "$target/$file"
done
cd "$target"
npm ci --no-audit --no-fund
# Pinning playwright-core also pins the matching browser revision.
PLAYWRIGHT_SKIP_BROWSER_GC=1 node node_modules/playwright-core/cli.js install --only-shell chromium
if [ ! -d browser-root ]; then
  # Reuse the already verified local library pack on this workspace.
  if [ -d "$HOME/.cache/login-flow-browser/root/usr/share/fonts/truetype/dejavu" ]; then
    cp -a "$HOME/.cache/login-flow-browser/root" browser-root
  else
    # Current approved workspace uses Ubuntu 26.04 amd64. Other images fail closed.
    . /etc/os-release
    [ "$ID:$VERSION_ID:$(dpkg --print-architecture)" = 'ubuntu:26.04:amd64' ] || { echo 'Unsupported browser library image' >&2; exit 1; }
    mkdir -p apt/lists/partial apt/archives/partial browser-root
    apt-get -o "Dir::State::Lists=$target/apt/lists" -o APT::Sandbox::User="$(id -un)" update
    (cd apt/archives && apt-get -o "Dir::State::Lists=$target/apt/lists" -o APT::Sandbox::User="$(id -un)" download \
      libdrm2 libxfixes3 fontconfig-config libnss3 libatk1.0-0t64 fonts-dejavu-core \
      libgbm1 libxrandr2 libxkbcommon0 libxrender1 fontconfig libasound2t64 \
      libxdamage1 libxcomposite1 libatk-bridge2.0-0t64 libatspi2.0-0t64 libxi6 libnspr4 libxres1)
    for deb in apt/archives/*.deb; do dpkg-deb -x "$deb" browser-root; done
  fi
fi
mkdir -p "$target/font-cache"
cat > fonts.conf <<FONT
<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig><dir>$target/browser-root/usr/share/fonts/truetype/dejavu</dir><cachedir>$target/font-cache</cachedir></fontconfig>
FONT
install -m 700 "$source_dir/coder-dev" "$HOME/.local/bin/coder-dev"
node cli.mjs browser doctor
