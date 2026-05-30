#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

out="$(bash "$ROOT/scripts/validate-guide-coverage.sh")"
[[ "$out" == "GUIDE_COVERAGE_OK" ]]

trace="$(bash "$ROOT/scripts/validate-guide-coverage.sh" 2>&1 >/dev/null)"
[[ "$trace" == *"mcp   server=agdec-http"* ]] || {
  echo "expected agdec-http server trace, got: $trace" >&2
  exit 1
}
[[ "$trace" == *"mcp   server=mizuchi"* ]] || {
  echo "expected mizuchi server trace, got: $trace" >&2
  exit 1
}
[[ "$trace" == *"--- validate-guide-coverage summary (GUIDE_COVERAGE_OK) ---"* ]] || {
  echo "expected summary block, got: $trace" >&2
  exit 1
}

quiet_out="$(bash "$ROOT/scripts/validate-guide-coverage.sh" --quiet 2>&1)"
[[ "$quiet_out" != *"mcp   server="* ]] || {
  echo "quiet mode should suppress mcp trace" >&2
  exit 1
}

echo "test-guide-coverage: PASS"
