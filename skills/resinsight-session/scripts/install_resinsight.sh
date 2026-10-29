#!/usr/bin/env bash
# Install ResInsight (Ubuntu 24.04 build) and the matching rips in one prefix. Userspace, no sudo. Linux only.
#
# Usage: install_resinsight.sh PREFIX [RI_VERSION] [RIPS_VERSION]
#        install_resinsight.sh --dry-run PREFIX ...   (print the steps, change nothing)
# Result: PREFIX/ubuntu (ResInsight), PREFIX/extra (xcb libs for GUI mode), PREFIX/venv (rips + numpy),
#         PREFIX/ri (launcher), PREFIX/env.sh (RESINSIGHT_EXECUTABLE, RIPS_PYTHON for hosts without a profile). Put PREFIX/ri in resinsight.bin and PREFIX/venv/bin/python in resinsight.python.
# Each step is skipped when its result is already there, so a second run only checks.
# rips refuses a ResInsight with another major.minor version: keep the two versions together.
set -euo pipefail

DRY=0
if [[ "${1:-}" == "--dry-run" ]]; then DRY=1; shift; fi
PREFIX="${1:?usage: install_resinsight.sh [--dry-run] PREFIX [RI_VERSION] [RIPS_VERSION]}"
RI_VERSION="${2:-2026.09.1}"
RIPS_VERSION="${3:-2026.9.1.2}"
PYTHON="${PYTHON:-python3}"
ZIP="ResInsight-Ubuntu_24.04_gcc-v${RI_VERSION}.zip"
URL="https://github.com/OPM/ResInsight/releases/download/v${RI_VERSION}/${ZIP}"
# GUI mode (images) needs these; console mode needs none of them.
XCB_DEBS=(libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1
  libxkbcommon-x11-0)

[[ "$(uname -s)" == "Linux" ]] || { echo "error: Linux only" >&2; exit 2; }
case "$PREFIX" in /*) ;; *) echo "error: PREFIX must be absolute: $PREFIX" >&2; exit 2 ;; esac
major_minor() { echo "$1" | awk -F. '{printf "%d.%d", $1, $2}'; }
if [[ "$(major_minor "$RI_VERSION")" != "$(major_minor "$RIPS_VERSION")" ]]; then
  echo "error: ResInsight $RI_VERSION and rips $RIPS_VERSION have different major.minor" >&2
  exit 2
fi

step() {
  echo "+ $*"
  if [[ "$DRY" == 0 ]]; then eval "$@"; fi
}

step "mkdir -p '$PREFIX'"
if [[ ! -x "$PREFIX/ubuntu/bin/ResInsight" ]]; then
  [[ -s "$PREFIX/$ZIP" ]] || step "curl -fL --retry 3 -o '$PREFIX/$ZIP.part' '$URL' && mv '$PREFIX/$ZIP.part' '$PREFIX/$ZIP'"
  step "unzip -q -o '$PREFIX/$ZIP' -d '$PREFIX/ubuntu'"
fi
if [[ ! -d "$PREFIX/extra/usr/lib/x86_64-linux-gnu" ]]; then
  if command -v apt-get >/dev/null && command -v dpkg-deb >/dev/null; then
    step "mkdir -p '$PREFIX/debs' && (cd '$PREFIX/debs' && apt-get download ${XCB_DEBS[*]})"
    step "for d in '$PREFIX'/debs/*.deb; do dpkg-deb -x \"\$d\" '$PREFIX/extra'; done"
  else
    echo "note: no apt-get/dpkg-deb; GUI mode needs ${XCB_DEBS[*]} from the system" >&2
  fi
fi
[[ -x "$PREFIX/venv/bin/python" ]] || step "'$PYTHON' -m venv '$PREFIX/venv'"
step "'$PREFIX/venv/bin/python' -m pip install -q 'rips==$RIPS_VERSION' numpy"
if [[ "$DRY" == 0 ]]; then
  cat >"$PREFIX/ri" <<'LAUNCHER'
#!/bin/bash
# ResInsight launcher: userspace xcb libs + bundled Qt. Usage: ri [ResInsight args]
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
export LD_LIBRARY_PATH="$HERE/extra/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$HERE/ubuntu/bin/ResInsight" "$@"
LAUNCHER
  chmod 755 "$PREFIX/ri"
else
  echo "+ write launcher $PREFIX/ri"
fi
if [[ "$DRY" == 0 ]]; then
  printf 'export RESINSIGHT_EXECUTABLE=%q\nexport RIPS_PYTHON=%q\n' "$PREFIX/ri" "$PREFIX/venv/bin/python" >"$PREFIX/env.sh"
fi
cat <<SNIPPET
# site.contract.yml (git-ignored), profile block:
resinsight:
  bin: "$PREFIX/ri"
  python: "$PREFIX/venv/bin/python"
  version: "$RI_VERSION"
  headless: xvfb-run
SNIPPET
