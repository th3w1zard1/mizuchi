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
    ("uint64_min", "4889f04839f7480f42c7c3", "x86-64-uint64-min-two-args-cdecl", "unsigned long long", "cmovb", "a < b ? a : b"),
    ("uint64_max", "4889f04839f7480f47c7c3", "x86-64-uint64-max-two-args-cdecl", "unsigned long long", "cmova", "a > b ? a : b"),
    ("int64_min", "4889f04839f7480f4cc7c3", "x86-64-int64-min-two-args-cdecl", "long long", "cmovl", "a < b ? a : b"),
    ("int64_max", "4889f04839f7480f4fc7c3", "x86-64-int64-max-two-args-cdecl", "long long", "cmovg", "a > b ? a : b"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, value_type, cmov, expression in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, value_type, cmov, expression, target_format))

for index, (name, hex_bytes, rule, value_type, cmov, expression, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x730000 + index * 0x10
    task = {
        "name": name,
        "address": address,
        "architectureHint": "x86_64",
        "argumentBits": 64,
    }
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["generator"]["registerArgs"] == ["rdi", "rsi"], sourcegen_candidate
    assert sourcegen_candidate["generator"]["valueType"] == value_type, sourcegen_candidate
    assert sourcegen_candidate["generator"]["cmov"] == cmov, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert f"{value_type} a, {value_type} b" in sourcegen_candidate["source"], sourcegen_candidate["source"]
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
            "argumentBits": 64,
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
  --limit 8 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 8 and .attemptedCandidates == 8 and .semanticCodeSliceMatchedCandidates == 8 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 8 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 8' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 8 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 8 and
  ([.[] | select(.rule == "x86-64-uint64-min-two-args-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-uint64-max-two-args-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-int64-min-two-args-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-int64-max-two-args-cdecl")] | length) == 2 and
  ([.[] | select(.generationEvidence.registerArgs == ["rdi", "rsi"])] | length) == 8
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
