#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BINARY="/run/media/brunner56/MyBook/SteamLibrary/steamapps/common/swkotor/binkw32.dll"
SYMBOL="_BinkGetError@0"
REPUBLIC_COMMANDO_CTGAME="/run/media/brunner56/MyBook/SteamLibrary/steamapps/common/Star Wars Republic Commando/GameData/System-Scalar/ctgame.dll"
EMPIRE_MSS64MIDI="/run/media/brunner56/MyBook/SteamLibrary/steamapps/common/Star Wars Empire at War/GameData/MSS64/mss64midi.dll"

if [[ ! -f "$BINARY" ]]; then
  echo "skip: missing KOTOR binkw32.dll: $BINARY"
  exit 0
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

report="$("$ROOT/scripts/pe-auto-trivial.py" \
  --binary "$BINARY" \
  --out "$TMP_DIR/functions" \
  --max-size 24)"

fast_report="$("$ROOT/scripts/pe-auto-trivial.py" \
  --binary "$BINARY" \
  --out "$TMP_DIR/functions-fast" \
  --max-size 24 \
  --pe-rebuild-mode never)"

printf '%s\n' "$report" | jq -e '.schema == "mizuchi.pe-auto-trivial.v1"' >/dev/null
printf '%s\n' "$report" | jq -e '.architecture == "i386"' >/dev/null
printf '%s\n' "$report" | jq -e '.compilerArchFlags == ["-m32"]' >/dev/null
printf '%s\n' "$report" | jq -e '.matchedCount >= 3' >/dev/null
printf '%s\n' "$report" | jq -e '.coffTarget == "i686-w64-windows-gnu"' >/dev/null
printf '%s\n' "$report" | jq -e '.coffMatches >= 3' >/dev/null
printf '%s\n' "$report" | jq -e '.elfFallbackAttempts == 0' >/dev/null
printf '%s\n' "$report" | jq -e '.aggregateSourceRoundtrip.byteIdentical == true' >/dev/null
printf '%s\n' "$report" | jq -e '.aggregateSourceRoundtrip.matchedSymbols == .coffMatches' >/dev/null
printf '%s\n' "$report" | jq -e '.aggregateSourceRoundtrip.rebuiltDllRoundtrip.byteIdenticalExports == true' >/dev/null
printf '%s\n' "$report" | jq -e '.aggregateSourceRoundtrip.rebuiltDllRoundtrip.matchedSymbols == .coffMatches' >/dev/null
printf '%s\n' "$report" | jq -e --arg sym "$SYMBOL" '.matches[] | select(.symbol == $sym and .byteIdentical == true)' >/dev/null
printf '%s\n' "$fast_report" | jq -e '.peRebuildMode == "never"' >/dev/null
printf '%s\n' "$fast_report" | jq -e '.matchedCount >= 3' >/dev/null
printf '%s\n' "$fast_report" | jq -e '.aggregateSourceRoundtrip.rebuiltDllRoundtrip.status == "skipped"' >/dev/null
[[ ! -f "$TMP_DIR/functions-fast/source-roundtrip/exports.dll" ]]

MATCH_DIR="$(printf '%s\n' "$report" | jq -r --arg sym "$SYMBOL" '.matches[] | select(.symbol == $sym) | .functionDir' | head -n 1)"
[[ -f "$MATCH_DIR/candidate.c" ]]
[[ -f "$MATCH_DIR/candidate.obj" ]]
[[ -f "$MATCH_DIR/target.bin" ]]
[[ -f "$MATCH_DIR/verify.json" ]]
jq -e '.byteIdentical == true' "$MATCH_DIR/verify.json" >/dev/null
jq -e '.schema == "mizuchi.pe-export-slice-verify.v1"' "$MATCH_DIR/verify.json" >/dev/null
jq -e '.candidateObjectFormat == "coff"' "$MATCH_DIR/verify.json" >/dev/null
AGG_DIR="$TMP_DIR/functions/source-roundtrip"
[[ -f "$AGG_DIR/exports.S" ]]
[[ -f "$AGG_DIR/exports.obj" ]]
[[ -f "$AGG_DIR/exports.dll" ]]
[[ -f "$AGG_DIR/verify.json" ]]
[[ -f "$AGG_DIR/rebuilt-dll-verify.json" ]]
jq -e '.byteIdentical == true' "$AGG_DIR/verify.json" >/dev/null
jq -e '.verified[] | select(.symbol == "_BinkGetError@0" and .byteIdentical == true)' "$AGG_DIR/verify.json" >/dev/null
jq -e '.byteIdenticalExports == true' "$AGG_DIR/rebuilt-dll-verify.json" >/dev/null
jq -e '.verified[] | select(.symbol == "_BinkGetError@0" and .byteIdentical == true)' "$AGG_DIR/rebuilt-dll-verify.json" >/dev/null

if [[ -f "$REPUBLIC_COMMANDO_CTGAME" ]]; then
  rc_report="$("$ROOT/scripts/pe-auto-trivial.py" \
    --binary "$REPUBLIC_COMMANDO_CTGAME" \
    --out "$TMP_DIR/republic-commando-ctgame" \
    --max-size 24)"
  printf '%s\n' "$rc_report" | jq -e '.matchedCount >= 400' >/dev/null
  printf '%s\n' "$rc_report" | jq -e '.coffMatches >= 400' >/dev/null
  printf '%s\n' "$rc_report" | jq -e '.aggregateSourceRoundtrip.byteIdentical == true' >/dev/null
  printf '%s\n' "$rc_report" | jq -e '.aggregateSourceRoundtrip.matchedSymbols == .coffMatches' >/dev/null
  printf '%s\n' "$rc_report" | jq -e '.aggregateSourceRoundtrip.rebuiltDllRoundtrip.byteIdenticalExports == true' >/dev/null
  printf '%s\n' "$rc_report" | jq -e '.aggregateSourceRoundtrip.rebuiltDllRoundtrip.matchedSymbols == .coffMatches' >/dev/null
  printf '%s\n' "$rc_report" | jq -e '[.matches[].pattern] | index("msvc_asm_return_edx") != null' >/dev/null
  printf '%s\n' "$rc_report" | jq -e '[.matches[].pattern] | index("msvc_asm_return_zero_xor") != null' >/dev/null
  printf '%s\n' "$rc_report" | jq -e '[.matches[].pattern] | index("msvc_asm_return_u8") != null' >/dev/null
  printf '%s\n' "$rc_report" | jq -e '[.matches[].pattern] | index("msvc_asm_ret_imm16") != null' >/dev/null
  printf '%s\n' "$rc_report" | jq -e '[.matches[].pattern] | index("msvc_asm_return_i32_ret_imm16") != null' >/dev/null
fi

if [[ -f "$EMPIRE_MSS64MIDI" ]]; then
  x64_report="$("$ROOT/scripts/pe-auto-trivial.py" \
    --binary "$EMPIRE_MSS64MIDI" \
    --out "$TMP_DIR/empire-mss64midi" \
    --max-size 24)"
  printf '%s\n' "$x64_report" | jq -e '.architecture == "x86_64"' >/dev/null
  printf '%s\n' "$x64_report" | jq -e '.matchedCount >= 1' >/dev/null
  printf '%s\n' "$x64_report" | jq -e '.aggregateSourceRoundtrip.byteIdentical == true' >/dev/null
  printf '%s\n' "$x64_report" | jq -e '.aggregateSourceRoundtrip.rebuiltDllRoundtrip.byteIdenticalExports == true' >/dev/null
  printf '%s\n' "$x64_report" | jq -e '.aggregateSourceRoundtrip.rebuiltDllRoundtrip.dll.format == "pe32plus-dll"' >/dev/null
  [[ -f "$TMP_DIR/empire-mss64midi/source-roundtrip/exports.dll" ]]
fi

echo "ok"
