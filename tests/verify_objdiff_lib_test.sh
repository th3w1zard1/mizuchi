#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

target="$TMP_DIR/target.o"
candidate="$TMP_DIR/candidate.o"
missing="$TMP_DIR/missing.o"

"$ROOT/scripts/build-and-verify.sh" --prompt "$ROOT/prompts/roundtrip_identity" --refresh-target >/dev/null
cp "$ROOT/prompts/roundtrip_identity/build/target.o" "$target"
cp "$ROOT/prompts/roundtrip_identity/build/target.o" "$candidate"

out="$("$ROOT/scripts/lib/verify-objdiff.sh" "$target" "$candidate" --out "$TMP_DIR/report.json" --raw-out "$TMP_DIR/raw.txt")"
printf '%s\n' "$out" | jq -e '
  .schema == "mizuchi.verify-objdiff.v1" and
  .status == "matched" and
  .differences == 0
' >/dev/null
jq -e '.status == "matched"' "$TMP_DIR/report.json" >/dev/null
[[ -f "$TMP_DIR/raw.txt" ]]

set +e
"$ROOT/scripts/lib/verify-objdiff.sh" "$missing" "$candidate" >"$TMP_DIR/missing.json"
missing_rc=$?
set -e
[[ "$missing_rc" -eq 1 ]]
jq -e '.status == "error" and (.message | contains("Target file not found"))' "$TMP_DIR/missing.json" >/dev/null

echo "ok"
