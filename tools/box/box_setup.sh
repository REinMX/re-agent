#!/bin/bash
# Home-box setup after a restart (the root filesystem is reset; the work disk persists). Idempotent.
# Restores the docker daemon, the OPM Flow image, a `flow` wrapper on PATH and make. Linux only.
# Usage: tools/box/box_setup.sh            (BOX_BIN defaults to /workspace/bin)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
BOX_BIN="${BOX_BIN:-/workspace/bin}"
IMG="${FLOW_IMAGE:-openporousmedia/opmreleases:2026.04_amd64}"
command -v docker >/dev/null || sudo -n apt-get install -y -q docker.io >/dev/null
if ! sudo -n docker info >/dev/null 2>&1; then
  sudo -n nohup dockerd >/tmp/dockerd.log 2>&1 &
  for _ in $(seq 30); do sudo -n docker info >/dev/null 2>&1 && break; sleep 1; done
fi
sudo -n docker image inspect "$IMG" >/dev/null 2>&1 || sudo -n docker pull -q "$IMG"
mkdir -p "$BOX_BIN"
[ ! -f "$HERE/flow_docker.sh" ] || cmp -s "$HERE/flow_docker.sh" "$BOX_BIN/flow" || install -m 0755 "$HERE/flow_docker.sh" "$BOX_BIN/flow"
[ "$(readlink /usr/local/bin/flow 2>/dev/null)" = "$BOX_BIN/flow" ] || sudo -n ln -sf "$BOX_BIN/flow" /usr/local/bin/flow
for f in ~/.profile ~/.bashrc; do
  grep -q "$BOX_BIN" "$f" 2>/dev/null || echo "export PATH=\"$BOX_BIN:\$PATH\"" >> "$f"
done
command -v make >/dev/null || sudo -n apt-get install -y -q make >/dev/null
"$BOX_BIN/flow" --version | head -1
