#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

if ! command -v clang >/dev/null 2>&1; then
  echo "skip: clang not installed"
  exit 0
fi
if ! command -v objdump >/dev/null 2>&1; then
  echo "skip: objdump not installed"
  exit 0
fi

PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 - <<'PY' "$TMP_DIR"
import json
import sys
from pathlib import Path

from mizuchi_re.source_parity_synthesize import generate
from mizuchi_re.sourcegen import generated_candidate_from_target_bytes

tmp = Path(sys.argv[1])
base_patterns = [
    ("rotl64_1", "4889f848d1c0c3", "x86-64-arg64-rotl-cdecl", "left", 1, 1, "rol", "(value << 1) | (value >> 63)"),
    ("rotr64_1", "4889f848d1c8c3", "x86-64-arg64-rotr-cdecl", "right", 1, 1, "ror", "(value >> 1) | (value << 63)"),
    ("rotl64_8", "4889f848c1c008c3", "x86-64-arg64-rotl-cdecl", "left", 8, 8, "rol", "(value << 8) | (value >> 56)"),
    ("rotr64_8", "4889f848c1c038c3", "x86-64-arg64-rotr-cdecl", "right", 8, 56, "rol", "(value >> 8) | (value << 56)"),
    ("rotl64_16", "4889f848c1c010c3", "x86-64-arg64-rotl-cdecl", "left", 16, 16, "rol", "(value << 16) | (value >> 48)"),
    ("rotr64_16", "4889f848c1c030c3", "x86-64-arg64-rotr-cdecl", "right", 16, 48, "rol", "(value >> 16) | (value << 48)"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, direction, count, encoded_count, encoding, expression in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, direction, count, encoded_count, encoding, expression, target_format))

for index, (name, hex_bytes, rule, direction, count, encoded_count, encoding, expression, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x6E0000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["generator"]["registerArg"] == "rdi", sourcegen_candidate
    assert sourcegen_candidate["generator"]["direction"] == direction, sourcegen_candidate
    assert sourcegen_candidate["generator"]["count"] == count, sourcegen_candidate
    assert sourcegen_candidate["generator"]["encodedCount"] == encoded_count, sourcegen_candidate
    assert sourcegen_candidate["generator"]["encoding"] == encoding, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert "unsigned long long value" in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert expression in sourcegen_candidate["source"], sourcegen_candidate["source"]

    synthesis_candidates = generate({**task, "entry": hex(address), "bytes": hex_bytes}, 16)
    assert synthesis_candidates, name
    assert synthesis_candidates[0].rule == rule, synthesis_candidates

    bytes_path = tmp / f"{name}.target.bin"
    bytes_path.write_bytes(data)
    tasks.append(
        {
            "schema": "mizuchi.source-task.v1",
            "name": name,
            "entry": hex(address),
            "address": address,
            "targetFormat": target_format,
            "architectureHint": "x86_64",
            "targetSlice": {
                "status": "complete",
                "bytesPath": str(bytes_path),
                "boundaryQuality": {"status": "complete"},
            },
        }
    )

(tmp / "tasks.jsonl").write_text("\n".join(json.dumps(row, sort_keys=True) for row in tasks) + "\n", encoding="utf-8")
PY

PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/tasks.jsonl" \
  --source-tasks-only \
  --out-dir "$TMP_DIR/out" \
  --compiler clang \
  --limit 12 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 12 and .attemptedCandidates == 12 and .semanticCodeSliceMatchedCandidates == 12 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 12 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 12' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 12 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 12 and
  ([.[] | select(.rule == "x86-64-arg64-rotl-cdecl")] | length) == 6 and
  ([.[] | select(.rule == "x86-64-arg64-rotr-cdecl")] | length) == 6 and
  ([.[] | select(.generationEvidence.direction == "left")] | length) == 6 and
  ([.[] | select(.generationEvidence.direction == "right")] | length) == 6 and
  ([.[] | select(.generationEvidence.encodedCount == 48)] | length) == 2 and
  ([.[] | select(.generationEvidence.encodedCount == 56)] | length) == 2
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
