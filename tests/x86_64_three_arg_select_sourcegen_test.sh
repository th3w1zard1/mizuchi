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
    ("uint_sel_lt", "89d039f70f42c7c3", "unsigned int", "a < b ? a : c", "cmovb"),
    ("uint_sel_ge", "89d039f70f43c7c3", "unsigned int", "a < b ? c : a", "cmovae"),
    ("uint_sel_gt", "89d039f70f47c7c3", "unsigned int", "a > b ? a : c", "cmova"),
    ("uint_sel_le", "89d039f70f46c7c3", "unsigned int", "a > b ? c : a", "cmovbe"),
    ("int_sel_lt", "89d039f70f4cc7c3", "int", "a < b ? a : c", "cmovl"),
    ("int_sel_ge", "89d039f70f4dc7c3", "int", "a < b ? c : a", "cmovge"),
    ("int_sel_gt", "89d039f70f4fc7c3", "int", "a > b ? a : c", "cmovg"),
    ("int_sel_le", "89d039f70f4ec7c3", "int", "a > b ? c : a", "cmovle"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, value_type, expression, cmov in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, value_type, expression, cmov, target_format))

for index, (name, hex_bytes, value_type, expression, cmov, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x640000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == "x86-64-three-args-select-cdecl", sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert sourcegen_candidate["generator"]["valueType"] == value_type, sourcegen_candidate
    assert sourcegen_candidate["generator"]["expression"] == expression, sourcegen_candidate
    assert sourcegen_candidate["generator"]["cmov"] == cmov, sourcegen_candidate
    assert f"{value_type} a, {value_type} b, {value_type} c" in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert expression in sourcegen_candidate["source"], sourcegen_candidate["source"]

    synthesis_candidates = generate({**task, "entry": hex(address), "bytes": hex_bytes}, 16)
    assert any(candidate.rule == "x86-64-three-args-select-cdecl" for candidate in synthesis_candidates), synthesis_candidates

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
  --limit 16 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 16 and .attemptedCandidates == 16 and .semanticCodeSliceMatchedCandidates == 16 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 16 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 16' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 16 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 16 and
  ([.[] | select(.rule == "x86-64-three-args-select-cdecl")] | length) == 16 and
  ([.[] | select(.generationEvidence.valueType == "unsigned int")] | length) == 8 and
  ([.[] | select(.generationEvidence.valueType == "int")] | length) == 8 and
  ([.[] | select(.generationEvidence.cmov == "cmovb")] | length) == 2 and
  ([.[] | select(.generationEvidence.cmov == "cmovg")] | length) == 2 and
  ([.[] | select(.generationEvidence.expression == "a < b ? a : c")] | length) == 4
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
