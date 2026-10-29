#!/usr/bin/env bash
# Run from the case ert folder. The active environment supplies ert and flow.
set -uo pipefail
cfg=$1
name=$2
mkdir -p "../output/$name"
start=$(date +%s)
ert ensemble_smoother --target-ensemble 'iter-%d' --disable-monitoring --experiment-name "$name" "$cfg" > "../output/$name/run.log" 2>&1
rc=$?
printf 'rc=%s wall_s=%s start=%s\n' "$rc" "$(( $(date +%s) - start ))" "$(date -d "@$start" -Iseconds)" > "../output/$name/wall.txt"
exit "$rc"
