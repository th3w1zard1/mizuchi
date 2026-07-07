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
    ("nonzero_mask", "31c0f7df19c0c3", "x86-64-arg-nonzero-mask-cdecl", "unsigned int value", "value != 0 ? 0xffffffffu : 0u", "!=", 0, None),
    ("zero_mask_as_lt1", "31c083ff0119c0c3", "x86-64-arg-uint-lt-imm8-mask-cdecl", "unsigned int value", "value < 0x01u ? 0xffffffffu : 0u", "<", 1, None),
    ("ult7_mask", "31c083ff0719c0c3", "x86-64-arg-uint-lt-imm8-mask-cdecl", "unsigned int value", "value < 0x07u ? 0xffffffffu : 0u", "<", 7, None),
    ("ule7_mask_as_lt8", "31c083ff0819c0c3", "x86-64-arg-uint-lt-imm8-mask-cdecl", "unsigned int value", "value < 0x08u ? 0xffffffffu : 0u", "<", 8, None),
    ("uge7_mask", "31c083ff0783d0ffc3", "x86-64-arg-uint-ge-imm8-mask-cdecl", "unsigned int value", "value >= 0x07u ? 0xffffffffu : 0u", ">=", 7, None),
    ("ugt7_mask_as_ge8", "31c083ff0883d0ffc3", "x86-64-arg-uint-ge-imm8-mask-cdecl", "unsigned int value", "value >= 0x08u ? 0xffffffffu : 0u", ">=", 8, None),
    ("eq7_mask", "31c083ff070f94c0f7d8c3", "x86-64-arg-uint-eq-imm8-mask-cdecl", "unsigned int value", "value == 0x07u ? 0xffffffffu : 0u", "==", 7, "sete"),
    ("ne7_mask", "31c083ff070f95c0f7d8c3", "x86-64-arg-uint-ne-imm8-mask-cdecl", "unsigned int value", "value != 0x07u ? 0xffffffffu : 0u", "!=", 7, "setne"),
    ("ilt7_mask", "31c083ff070f9cc0f7d8c3", "x86-64-arg-int-lt-imm8-mask-cdecl", "int value", "value < 7 ? -1 : 0", "<", 7, "setl"),
    ("ige7_mask", "31c083ff070f9dc0f7d8c3", "x86-64-arg-int-ge-imm8-mask-cdecl", "int value", "value >= 7 ? -1 : 0", ">=", 7, "setge"),
    ("igt7_mask_as_ge8", "31c083ff080f9dc0f7d8c3", "x86-64-arg-int-ge-imm8-mask-cdecl", "int value", "value >= 8 ? -1 : 0", ">=", 8, "setge"),
    ("ile7_mask_as_lt8", "31c083ff080f9cc0f7d8c3", "x86-64-arg-int-lt-imm8-mask-cdecl", "int value", "value < 8 ? -1 : 0", "<", 8, "setl"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, parameter, expression, operator, immediate, setcc in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, parameter, expression, operator, immediate, setcc, target_format))

for index, (name, hex_bytes, rule, parameter, expression, operator, immediate, setcc, target_format) in enumerate(patterns):
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
    assert sourcegen_candidate["generator"]["setcc"] == setcc, sourcegen_candidate
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
  --limit 24 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 24 and .attemptedCandidates == 24 and .semanticCodeSliceMatchedCandidates == 24 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 24 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 24' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 24 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 24 and
  ([.[] | select(.rule == "x86-64-arg-nonzero-mask-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-arg-uint-lt-imm8-mask-cdecl")] | length) == 6 and
  ([.[] | select(.rule == "x86-64-arg-uint-ge-imm8-mask-cdecl")] | length) == 4 and
  ([.[] | select(.rule == "x86-64-arg-uint-eq-imm8-mask-cdecl" and .generationEvidence.setcc == "sete")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-arg-uint-ne-imm8-mask-cdecl" and .generationEvidence.setcc == "setne")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-arg-int-lt-imm8-mask-cdecl" and .generationEvidence.setcc == "setl")] | length) == 4 and
  ([.[] | select(.rule == "x86-64-arg-int-ge-imm8-mask-cdecl" and .generationEvidence.setcc == "setge")] | length) == 4 and
  ([.[] | select(.generationEvidence.trueValue == "0xffffffff")] | length) == 24 and
  ([.[] | select(.generationEvidence.falseValue == "0x00000000")] | length) == 24
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
