#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

if ! command -v clang >/dev/null 2>&1; then
  echo "skip: clang not installed"
  exit 0
fi
if ! command -v objdiff >/dev/null 2>&1; then
  echo "skip: objdiff not installed"
  exit 0
fi

python3 - <<'PY' "$TMP_DIR/pe_tiny.exe"
import struct
import sys
from pathlib import Path

out = Path(sys.argv[1])
image_base = 0x400000
text_rva = 0x1000
text_raw = 0x200
text_size = 0x200

code = bytearray(b"\x90" * text_size)
code[0:10] = bytes.fromhex("5589e5b8785634125dc3")
code[0x10:0x17] = bytes.fromhex("5589e531c05dc3")

headers = bytearray(0x200)
headers[0:2] = b"MZ"
struct.pack_into("<I", headers, 0x3C, 0x80)
pe = 0x80
headers[pe : pe + 4] = b"PE\0\0"
coff = pe + 4
struct.pack_into("<HHIIIHH", headers, coff, 0x014C, 1, 0, 0, 0, 0xE0, 0x0102)
opt = coff + 20
struct.pack_into("<HBBIII", headers, opt, 0x10B, 14, 0, text_size, 0, 0)
struct.pack_into("<III", headers, opt + 16, text_rva, text_rva, 0)
struct.pack_into("<III", headers, opt + 28, image_base, 0x1000, 0x200)
struct.pack_into("<HHHHHH", headers, opt + 40, 4, 0, 0, 0, 4, 0)
struct.pack_into("<I", headers, opt + 56, 0)
struct.pack_into("<III", headers, opt + 60, 0x2000, 0x200, 0)
struct.pack_into("<HH", headers, opt + 68, 3, 0)
struct.pack_into("<IIIIII", headers, opt + 72, 0x100000, 0x1000, 0x100000, 0x1000, 0, 16)

section = opt + 0xE0
headers[section : section + 8] = b".text\0\0\0"
struct.pack_into("<IIIIIIHHI", headers, section + 8, 0x20, text_rva, text_size, text_raw, 0, 0, 0, 0, 0x60000020)
out.write_bytes(headers + code)
PY

PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m mizuchi_re.mizuchi_cli "$TMP_DIR/pe_tiny.exe" \
  --work-dir "$TMP_DIR/recover" \
  --no-resume \
  --no-byte-authority \
  --source-synthesis clang \
  --source-synthesis-limit 10 \
  --source-synthesis-source-quality high-level-c \
  --stop-after plan-strategy \
  --context-max-files 20 \
  --context-max-depth 1 \
  --context-strings-limit 20 >/dev/null

jq -e '.format == "pe" and .architectureHint == "x86"' "$TMP_DIR/recover/target.json" >/dev/null
jq -e '.summary.codeRanges == 1' "$TMP_DIR/recover/binary-inventory.json" >/dev/null
jq -e '.summary.candidateCount == 3 and .summary.bySource["x86-prologue"] == 2 and .summary.bySource["executable-range"] == 1' "$TMP_DIR/recover/function-candidates.json" >/dev/null
jq -e '.generatedByRule["framed-return-immediate-cdecl"] == 1 and .generatedByRule["framed-return-zero"] == 1 and .highLevelSourceCandidates == 2' "$TMP_DIR/recover/source-generation/summary.json" >/dev/null
jq -e '.successfulFunctions == 2 and .highLevelSourceMatches == 2 and .matchedBySourceQuality["high-level-c"] == 2' "$TMP_DIR/recover/source-synthesis/summary.json" >/dev/null
jq -e 'select(.name == "sub_1000" and .rule == "framed-return-immediate-cdecl" and .status == "code-slice-matched" and .differences == 0)' "$TMP_DIR/recover/source-synthesis/plugin-attempts.jsonl" >/dev/null
jq -e 'select(.name == "sub_1010" and .rule == "framed-return-zero" and .status == "code-slice-matched" and .differences == 0)' "$TMP_DIR/recover/source-synthesis/plugin-attempts.jsonl" >/dev/null

echo "ok"
