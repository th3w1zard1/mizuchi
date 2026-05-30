#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AUDIT="$ROOT/scripts/audit-plugin-readiness.sh"
PASS_FIXTURE="$ROOT/tests/fixtures/plugin-readiness-pass"
FAIL_FIXTURE="$ROOT/tests/fixtures/plugin-readiness-fail"

chmod +x "$AUDIT" "$PASS_FIXTURE/hooks/fixture-hook.sh" 2>/dev/null || true

pass_out="$("$AUDIT" --plugin-root "$PASS_FIXTURE" --quiet)"
[[ "$pass_out" == "PLUGIN_READINESS_OK" ]] || {
  echo "expected PLUGIN_READINESS_OK, got: $pass_out" >&2
  exit 1
}

set +e
fail_out="$("$AUDIT" --plugin-root "$FAIL_FIXTURE" --quiet 2>&1)"
fail_status=$?
set -e
[[ "$fail_status" -ne 0 ]] || {
  echo "expected non-zero for fail fixture" >&2
  exit 1
}
[[ "$fail_out" == *"PLUGIN_READINESS_FAIL"* ]] || {
  echo "expected PLUGIN_READINESS_FAIL marker, got: $fail_out" >&2
  exit 1
}

if [[ -d "${HOME}/.cursor/plugins/local/matching-decompilation-re" ]]; then
  real_out="$("$AUDIT" --plugin-root "${HOME}/.cursor/plugins/local/matching-decompilation-re" --quiet)"
  [[ "$real_out" == "PLUGIN_READINESS_OK" ]] || {
    echo "local plugin audit failed: $real_out" >&2
    exit 1
  }
fi

echo "ok"
