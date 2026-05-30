#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/verify-workspace-surface.sh"

out="$("$SCRIPT")"
[[ "$out" == "WORKSPACE_SURFACE_OK" ]] || {
  echo "unexpected output: $out" >&2
  exit 1
}

echo "ok"
