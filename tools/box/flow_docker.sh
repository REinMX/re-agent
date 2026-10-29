#!/bin/bash
# OPM Flow on the box via Docker (persists in /workspace/bin across box restarts).
# Uses plain docker if the user can reach the daemon, else passwordless `sudo -n docker`
# (same privilege as the previous wrappers). Never prompts. Run box-setup.sh if docker is gone.
IMG="${FLOW_IMAGE:-openporousmedia/opmreleases:2026.04_amd64}"
if docker info >/dev/null 2>&1; then D=(docker)
elif sudo -n docker info >/dev/null 2>&1; then D=(sudo -n docker)
else echo "flow: docker daemon not reachable; run /workspace/bin/box-setup.sh" >&2; exit 127; fi
MOUNTS=(-v /workspace:/workspace)
case "$PWD/" in /workspace/*) ;; *) MOUNTS+=(-v "$PWD:$PWD");; esac
exec "${D[@]}" run --rm --user "$(id -u):$(id -g)" "${MOUNTS[@]}" -w "$PWD" "$IMG" flow "$@"
