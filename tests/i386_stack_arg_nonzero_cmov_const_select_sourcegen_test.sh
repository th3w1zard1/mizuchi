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
    (
        "nonzero_1234",
        "8b4c240485c9b8d20400000f45c1",
        "value != 0 ? value : 0x000004d2u",
        "cmovne",
        "value",
        "0x000004d2",
    ),
    (
        "zero_1234",
        "8b4c240485c9b8d20400000f44c1",
        "value != 0 ? 0x000004d2u : value",
        "cmove",
        "0x000004d2",
        "value",
    ),
    (
        "nonzero_7",
        "8b4c240485c9b8070000000f45c1",
        "value != 0 ? value : 0x00000007u",
        "cmovne",
        "value",
        "0x00000007",
    ),
    (
        "zero_7",
        "8b4c240485c9b8070000000f44c1",
        "value != 0 ? 0x00000007u : value",
        "cmove",
        "0x00000007",
        "value",
    ),
]

tasks = []
patterns = []
for name, core_hex, expression, cmov, true_value, false_value in base_patterns:
    patterns.append(
        (
            f"{name}_cdecl",
            f"{core_hex}c3",
            "stack-arg-nonzero-cmov-const-select-cdecl",
            expression,
            cmov,
            true_value,
            false_value,
        )
    )
    patterns.append(
        (
            f"{name}_stdcall",
            f"{core_hex}c20400",
            "stack-arg-nonzero-cmov-const-select-stdcall",
            expression,
            cmov,
            true_value,
            false_value,
        )
    )

for index, (name, hex_bytes, rule, expression, cmov, true_value, false_value) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x40F000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "i386"}

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert sourcegen_candidate["generator"]["cmov"] == cmov, sourcegen_candidate
    assert sourcegen_candidate["generator"]["trueValue"] == true_value, sourcegen_candidate
    assert sourcegen_candidate["generator"]["falseValue"] == false_value, sourcegen_candidate
    assert "unsigned int value" in sourcegen_candidate["source"], sourcegen_candidate["source"]
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

PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m reconkit_re.source_parity_synthesize \
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
  ([.[] | select(.rule == "stack-arg-nonzero-cmov-const-select-cdecl")] | length) == 4 and
  ([.[] | select(.rule == "stack-arg-nonzero-cmov-const-select-stdcall")] | length) == 4 and
  ([.[] | select(.generationEvidence.cmov == "cmove")] | length) == 4 and
  ([.[] | select(.generationEvidence.cmov == "cmovne")] | length) == 4 and
  ([.[] | select(.generationEvidence.trueValue == "value")] | length) == 4 and
  ([.[] | select(.generationEvidence.falseValue == "value")] | length) == 4
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
