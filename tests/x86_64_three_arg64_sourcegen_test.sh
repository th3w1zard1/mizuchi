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
    ("add3_64", "488d04374801d0c3", "x86-64-three-args-arithmetic64-cdecl", "unsigned long long", "a + b + c"),
    ("sub_add_64", "4829f7488d0417c3", "x86-64-three-args-arithmetic64-cdecl", "unsigned long long", "a - b + c"),
    ("sub_sum_64", "4889f84801d64829f0c3", "x86-64-three-args-arithmetic64-cdecl", "unsigned long long", "a - b - c"),
    ("sub_sum_rev_64", "4889d04801f74829f8c3", "x86-64-three-args-arithmetic64-cdecl", "unsigned long long", "c - (a + b)"),
    ("mul_add_64", "480faffe488d0417c3", "x86-64-three-args-arithmetic64-cdecl", "unsigned long long", "a * b + c"),
    ("mul_sub_64", "4889f8480fafc64829d0c3", "x86-64-three-args-arithmetic64-cdecl", "unsigned long long", "a * b - c"),
    ("sub_mul_64", "4889d0480faffe4829f8c3", "x86-64-three-args-arithmetic64-cdecl", "unsigned long long", "c - a * b"),
    ("and3_64", "4889f84821f04821d0c3", "x86-64-three-args-bitwise64-cdecl", "unsigned long long", "(a & b) & c"),
    ("or3_64", "4889f84809f04809d0c3", "x86-64-three-args-bitwise64-cdecl", "unsigned long long", "(a | b) | c"),
    ("xor3_64", "4889f84831f04831d0c3", "x86-64-three-args-bitwise64-cdecl", "unsigned long long", "(a ^ b) ^ c"),
    ("and_or_64", "4889f84821f04809d0c3", "x86-64-three-args-bitwise64-cdecl", "unsigned long long", "(a & b) | c"),
    ("uint_sel_lt_64", "4889d04839f7480f42c7c3", "x86-64-three-args-select64-cdecl", "unsigned long long", "a < b ? a : c"),
    ("uint_sel_ge_64", "4889d04839f7480f43c7c3", "x86-64-three-args-select64-cdecl", "unsigned long long", "a < b ? c : a"),
    ("int_sel_lt_64", "4889d04839f7480f4cc7c3", "x86-64-three-args-select64-cdecl", "long long", "a < b ? a : c"),
    ("int_sel_le_64", "4889d04839f7480f4ec7c3", "x86-64-three-args-select64-cdecl", "long long", "a > b ? c : a"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, value_type, expression in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, value_type, expression, target_format))

for index, (name, hex_bytes, rule, value_type, expression, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x650000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    if "valueType" in sourcegen_candidate["generator"]:
        assert sourcegen_candidate["generator"]["valueType"] == value_type, sourcegen_candidate
    assert expression in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert f"{value_type} a, {value_type} b, {value_type} c" in sourcegen_candidate["source"], sourcegen_candidate["source"]

    synthesis_candidates = generate({**task, "entry": hex(address), "bytes": hex_bytes}, 16)
    assert any(candidate.rule == rule for candidate in synthesis_candidates), synthesis_candidates

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
  --limit 30 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 30 and .attemptedCandidates == 30 and .semanticCodeSliceMatchedCandidates == 30 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 30 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 30' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 30 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 30 and
  ([.[] | select(.rule == "x86-64-three-args-arithmetic64-cdecl")] | length) == 14 and
  ([.[] | select(.rule == "x86-64-three-args-bitwise64-cdecl")] | length) == 8 and
  ([.[] | select(.rule == "x86-64-three-args-select64-cdecl")] | length) == 8 and
  ([.[] | select(.generationEvidence.registerArgs == ["rdi", "rsi", "rdx"])] | length) == 30 and
  ([.[] | select(.generationEvidence.valueType == "long long")] | length) == 4 and
  ([.[] | select(.generationEvidence.valueType == "unsigned long long")] | length) == 4
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
