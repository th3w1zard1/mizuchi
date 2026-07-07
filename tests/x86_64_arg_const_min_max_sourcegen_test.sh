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

from reconkit_re.source_parity_synthesize import generate
from reconkit_re.sourcegen import generated_candidate_from_target_bytes

tmp = Path(sys.argv[1])
base_patterns = [
    ("umax7", "83ff08b8070000000f43c7c3", "x86-64-arg-uint-max-imm8-cmov-cdecl", "unsigned int value", "value > 0x07u ? value : 0x07u", "cmovae", 8),
    ("umin7", "83ff07b8070000000f42c7c3", "x86-64-arg-uint-min-imm8-cmov-cdecl", "unsigned int value", "value < 0x07u ? value : 0x07u", "cmovb", 7),
    ("imax7", "83ff08b8070000000f4dc7c3", "x86-64-arg-int-max-imm8-cmov-cdecl", "int value", "value > 7 ? value : 7", "cmovge", 8),
    ("imin7", "83ff07b8070000000f4cc7c3", "x86-64-arg-int-min-imm8-cmov-cdecl", "int value", "value < 7 ? value : 7", "cmovl", 7),
    ("umax8", "83ff09b8080000000f43c7c3", "x86-64-arg-uint-max-imm8-cmov-cdecl", "unsigned int value", "value > 0x08u ? value : 0x08u", "cmovae", 9),
    ("umin8", "83ff08b8080000000f42c7c3", "x86-64-arg-uint-min-imm8-cmov-cdecl", "unsigned int value", "value < 0x08u ? value : 0x08u", "cmovb", 8),
    ("imax8", "83ff09b8080000000f4dc7c3", "x86-64-arg-int-max-imm8-cmov-cdecl", "int value", "value > 8 ? value : 8", "cmovge", 9),
    ("imin8", "83ff08b8080000000f4cc7c3", "x86-64-arg-int-min-imm8-cmov-cdecl", "int value", "value < 8 ? value : 8", "cmovl", 8),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, parameter, expression, cmov, compare_immediate in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, parameter, expression, cmov, compare_immediate, target_format))

for index, (name, hex_bytes, rule, parameter, expression, cmov, compare_immediate, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x630000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["generator"]["cmov"] == cmov, sourcegen_candidate
    assert sourcegen_candidate["generator"]["compareImmediate"] == compare_immediate, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert parameter in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert expression in sourcegen_candidate["source"], sourcegen_candidate["source"]

    synthesis_candidates = generate({**task, "entry": hex(address), "bytes": hex_bytes}, 16)
    assert any(candidate.rule == rule for candidate in synthesis_candidates), (rule, synthesis_candidates)

    bytes_path = tmp / f"{name}.target.bin"
    bytes_path.write_bytes(data)
    tasks.append(
        {
            "schema": "reconkit.source-task.v1",
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

PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m reconkit_re.source_parity_synthesize \
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
  ([.[] | select(.rule == "x86-64-arg-uint-max-imm8-cmov-cdecl")] | length) == 4 and
  ([.[] | select(.rule == "x86-64-arg-uint-min-imm8-cmov-cdecl")] | length) == 4 and
  ([.[] | select(.rule == "x86-64-arg-int-max-imm8-cmov-cdecl")] | length) == 4 and
  ([.[] | select(.rule == "x86-64-arg-int-min-imm8-cmov-cdecl")] | length) == 4 and
  ([.[] | select(.generationEvidence.cmov == "cmovae")] | length) == 4 and
  ([.[] | select(.generationEvidence.cmov == "cmovb")] | length) == 4 and
  ([.[] | select(.generationEvidence.cmov == "cmovge")] | length) == 4 and
  ([.[] | select(.generationEvidence.cmov == "cmovl")] | length) == 4
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
