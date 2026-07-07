#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BINARY="/run/media/brunner56/MyBook/SteamLibrary/steamapps/common/vvvvvv/VVVVVV"
SYMBOL="_ZN12UtilityClass14hms_to_secondsEiii"

if [[ ! -f "$BINARY" ]]; then
  echo "skip: missing VVVVVV binary: $BINARY"
  exit 0
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

report="$("$ROOT/scripts/elf-auto-trivial.py" \
  --binary "$BINARY" \
  --out "$TMP_DIR/functions" \
  --max-size 24)"

printf '%s\n' "$report" | jq -e '.schema == "reconkit.elf-auto-trivial.v1"' >/dev/null
printf '%s\n' "$report" | jq -e '.matchedCount >= 1' >/dev/null
printf '%s\n' "$report" | jq -e '.aggregateSourceRoundtrip.byteIdentical == true' >/dev/null
printf '%s\n' "$report" | jq -e '.aggregateSourceRoundtrip.matchedSymbols == .matchedCount' >/dev/null
printf '%s\n' "$report" | jq -e --arg sym "$SYMBOL" '.matches[] | select(.symbol == $sym and .byteIdentical == true)' >/dev/null

MATCH_DIR="$(printf '%s\n' "$report" | jq -r --arg sym "$SYMBOL" '.matches[] | select(.symbol == $sym) | .functionDir' | head -n 1)"
[[ -f "$MATCH_DIR/candidate.c" ]]
[[ -f "$MATCH_DIR/candidate.o" ]]
[[ -f "$MATCH_DIR/verify.json" ]]
jq -e '.byteIdentical == true' "$MATCH_DIR/verify.json" >/dev/null
AGG_DIR="$TMP_DIR/functions/source-roundtrip"
[[ -f "$AGG_DIR/functions.S" ]]
[[ -f "$AGG_DIR/functions.o" ]]
[[ -f "$AGG_DIR/verify.json" ]]
jq -e '.byteIdentical == true' "$AGG_DIR/verify.json" >/dev/null
jq -e --arg sym "$SYMBOL" '.verified[] | select(.symbol == $sym and .byteIdentical == true)' "$AGG_DIR/verify.json" >/dev/null

echo "ok"
