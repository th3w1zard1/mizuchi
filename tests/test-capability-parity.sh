#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="$(bash "$ROOT/scripts/validate-capability-parity.sh")"
[[ "$out" == "CAPABILITY_PARITY_OK" ]]

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/scripts"
cp "$ROOT/scripts/decomp-cli.sh" "$tmp/scripts/"
cp "$ROOT/CAPABILITY_MATRIX.md" "$tmp/"
sed -i 's/list_prompts(status=<matched|integrated|in_progress|pending|blocked>)/list_prompts(status=<matched|integrated|in_progress|blocked>)/' "$tmp/CAPABILITY_MATRIX.md"

set +e
neg_out="$(MIZUCHI_ROOT="$tmp" bash "$ROOT/scripts/validate-capability-parity.sh" 2>&1)"
neg_rc=$?
set -e
[[ "$neg_rc" -ne 0 ]]
grep -q "missing capability token in CAPABILITY_MATRIX.md" <<<"$neg_out"

echo "test-capability-parity: PASS"
