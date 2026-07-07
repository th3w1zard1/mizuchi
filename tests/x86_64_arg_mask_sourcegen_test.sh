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
    ("nonzero_mask", "31c0f7df19c0c3", "x86-64-arg-nonzero-mask-cdecl", "value != 0 ? 0xffffffffu : 0u", "!=", 0),
    ("zero_mask_as_lt1", "31c083ff0119c0c3", "x86-64-arg-uint-lt-imm8-mask-cdecl", "value < 0x01u ? 0xffffffffu : 0u", "<", 1),
    ("ult7_mask", "31c083ff0719c0c3", "x86-64-arg-uint-lt-imm8-mask-cdecl", "value < 0x07u ? 0xffffffffu : 0u", "<", 7),
    ("ule7_mask_as_lt8", "31c083ff0819c0c3", "x86-64-arg-uint-lt-imm8-mask-cdecl", "value < 0x08u ? 0xffffffffu : 0u", "<", 8),
    ("uge7_mask", "31c083ff0783d0ffc3", "x86-64-arg-uint-ge-imm8-mask-cdecl", "value >= 0x07u ? 0xffffffffu : 0u", ">=", 7),
    ("ugt7_mask_as_ge8", "31c083ff0883d0ffc3", "x86-64-arg-uint-ge-imm8-mask-cdecl", "value >= 0x08u ? 0xffffffffu : 0u", ">=", 8),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, expression, operator, immediate in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, expression, operator, immediate, target_format))

for index, (name, hex_bytes, rule, expression, operator, immediate, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x640000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["generator"]["operator"] == operator, sourcegen_candidate
    assert sourcegen_candidate["generator"]["immediate"] == immediate, sourcegen_candidate
    assert sourcegen_candidate["generator"]["trueValue"] == "0xffffffff", sourcegen_candidate
    assert sourcegen_candidate["generator"]["falseValue"] == "0x00000000", sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert "unsigned int value" in sourcegen_candidate["source"], sourcegen_candidate["source"]
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
  ([.[] | select(.rule == "x86-64-arg-nonzero-mask-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-arg-uint-lt-imm8-mask-cdecl")] | length) == 6 and
  ([.[] | select(.rule == "x86-64-arg-uint-ge-imm8-mask-cdecl")] | length) == 4 and
  ([.[] | select(.generationEvidence.trueValue == "0xffffffff")] | length) == 12 and
  ([.[] | select(.generationEvidence.falseValue == "0x00000000")] | length) == 12
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
