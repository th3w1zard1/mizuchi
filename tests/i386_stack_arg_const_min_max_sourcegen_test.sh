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
    ("umin7", "8b4c240483f907b8070000000f42c1", "stack-arg-uint-min-const-cmov", "unsigned int value", "value < 0x00000007u ? value : 0x00000007u", "cmovb", 7),
    ("umax7", "8b4c240483f908b8070000000f43c1", "stack-arg-uint-max-const-cmov", "unsigned int value", "value > 0x00000007u ? value : 0x00000007u", "cmovae", 8),
    ("imin7", "8b4c240483f907b8070000000f4cc1", "stack-arg-int-min-const-cmov", "int value", "value < 7 ? value : 7", "cmovl", 7),
    ("imax7", "8b4c240483f908b8070000000f4dc1", "stack-arg-int-max-const-cmov", "int value", "value > 7 ? value : 7", "cmovge", 8),
    ("umin255", "8b4c240481f9ff000000b8ff0000000f42c1", "stack-arg-uint-min-const-cmov", "unsigned int value", "value < 0x000000ffu ? value : 0x000000ffu", "cmovb", 255),
    ("umax255", "8b4c240481f900010000b8ff0000000f43c1", "stack-arg-uint-max-const-cmov", "unsigned int value", "value > 0x000000ffu ? value : 0x000000ffu", "cmovae", 256),
    ("imin255", "8b4c240481f9ff000000b8ff0000000f4cc1", "stack-arg-int-min-const-cmov", "int value", "value < 255 ? value : 255", "cmovl", 255),
    ("imax255", "8b4c240481f900010000b8ff0000000f4dc1", "stack-arg-int-max-const-cmov", "int value", "value > 255 ? value : 255", "cmovge", 256),
    ("imin_neg5", "8b4c240483f9fbb8fbffffff0f4cc1", "stack-arg-int-min-const-cmov", "int value", "value < -5 ? value : -5", "cmovl", -5),
    ("imax_neg5", "8b4c240483f9fcb8fbffffff0f4dc1", "stack-arg-int-max-const-cmov", "int value", "value > -5 ? value : -5", "cmovge", -4),
]

patterns = []
for name, core_hex, rule_prefix, parameter, expression, cmov, compare_immediate in base_patterns:
    patterns.append((f"{name}_cdecl", f"{core_hex}c3", f"{rule_prefix}-cdecl", parameter, expression, cmov, compare_immediate))
    patterns.append((f"{name}_stdcall", f"{core_hex}c20400", f"{rule_prefix}-stdcall", parameter, expression, cmov, compare_immediate))

tasks = []
for index, (name, hex_bytes, rule, parameter, expression, cmov, compare_immediate) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x40E000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "i386"}
    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["generator"]["cmov"] == cmov, sourcegen_candidate
    assert sourcegen_candidate["generator"]["compareImmediate"] == compare_immediate, sourcegen_candidate
    assert parameter in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert expression in sourcegen_candidate["source"], sourcegen_candidate["source"]

    synthesis_candidates = generate({**task, "entry": hex(address), "bytes": hex_bytes}, 16)
    assert any(candidate.rule == rule for candidate in synthesis_candidates), (rule, synthesis_candidates)

    bytes_path = tmp / f"{name}.target.bin"
    bytes_path.write_bytes(data)
    tasks.append(
        {
            "schema": "mizuchi.source-task.v1",
            "name": name,
            "entry": hex(address),
            "address": address,
            "architectureHint": "i386",
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
  --limit 20 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 20 and .attemptedCandidates == 20 and .semanticCodeSliceMatchedCandidates == 20 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 20 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 20' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 20 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 20 and
  ([.[] | select(.rule == "stack-arg-uint-min-const-cmov-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "stack-arg-uint-max-const-cmov-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "stack-arg-int-min-const-cmov-cdecl")] | length) == 3 and
  ([.[] | select(.rule == "stack-arg-int-max-const-cmov-cdecl")] | length) == 3 and
  ([.[] | select(.rule == "stack-arg-uint-min-const-cmov-stdcall")] | length) == 2 and
  ([.[] | select(.rule == "stack-arg-uint-max-const-cmov-stdcall")] | length) == 2 and
  ([.[] | select(.rule == "stack-arg-int-min-const-cmov-stdcall")] | length) == 3 and
  ([.[] | select(.rule == "stack-arg-int-max-const-cmov-stdcall")] | length) == 3 and
  ([.[] | select(.generationEvidence.cmov == "cmovb")] | length) == 4 and
  ([.[] | select(.generationEvidence.cmov == "cmovae")] | length) == 4 and
  ([.[] | select(.generationEvidence.cmov == "cmovl")] | length) == 6 and
  ([.[] | select(.generationEvidence.cmov == "cmovge")] | length) == 6
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
