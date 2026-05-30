#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/lfg-smoke.sh"

out="$("$SCRIPT" --name ci)"
[[ "$out" == *"LFG_SMOKE_OK name=ci"* ]] || {
  echo "unexpected output: $out" >&2
  exit 1
}
[[ "$out" == *"WORKSPACE_SURFACE_OK"* ]] || {
  echo "missing WORKSPACE_SURFACE_OK in output" >&2
  exit 1
}
[[ "$out" == *"PROMPT_STATUS_OK"* ]] || {
  echo "missing PROMPT_STATUS_OK in output" >&2
  exit 1
}

set +e
"$SCRIPT" >/tmp/lfg_smoke_usage.$$ 2>&1
status=$?
set -e
[[ "$status" -eq 2 ]] || {
  echo "expected exit 2, got $status" >&2
  exit 1
}
grep -q "usage:" /tmp/lfg_smoke_usage.$$ || {
  echo "missing usage output" >&2
  exit 1
}
rm -f /tmp/lfg_smoke_usage.$$

echo "ok"
