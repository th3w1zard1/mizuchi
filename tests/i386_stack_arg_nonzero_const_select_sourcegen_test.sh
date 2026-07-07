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
        "setne_scale2",
        "31c0837c2404000f95c08d044507000000",
        "value != 0 ? 0x00000009u : 0x00000007u",
        "setne",
        "0x00000009",
        "0x00000007",
        7,
        2,
    ),
    (
        "sete_scale2",
        "31c0837c2404000f94c08d044507000000",
        "value != 0 ? 0x00000007u : 0x00000009u",
        "sete",
        "0x00000007",
        "0x00000009",
        7,
        2,
    ),
    (
        "setne_scale4",
        "31c0837c2404000f95c08d048507000000",
        "value != 0 ? 0x0000000bu : 0x00000007u",
        "setne",
        "0x0000000b",
        "0x00000007",
        7,
        4,
    ),
    (
        "sete_scale4",
        "31c0837c2404000f94c08d048507000000",
        "value != 0 ? 0x00000007u : 0x0000000bu",
        "sete",
        "0x00000007",
        "0x0000000b",
        7,
        4,
    ),
    (
        "setne_scale8",
        "31c0837c2404000f95c08d04c507000000",
        "value != 0 ? 0x0000000fu : 0x00000007u",
        "setne",
        "0x0000000f",
        "0x00000007",
        7,
        8,
    ),
    (
        "sete_scale8",
        "31c0837c2404000f94c08d04c507000000",
        "value != 0 ? 0x00000007u : 0x0000000fu",
        "sete",
        "0x00000007",
        "0x0000000f",
        7,
        8,
    ),
]

tasks = []
patterns = []
for name, core_hex, expression, setcc, true_value, false_value, base_value, scale in base_patterns:
    patterns.append(
        (
            f"{name}_cdecl",
            f"{core_hex}c3",
            "stack-arg-nonzero-const-select-cdecl",
            expression,
            setcc,
            true_value,
            false_value,
            base_value,
            scale,
        )
    )
    patterns.append(
        (
            f"{name}_stdcall",
            f"{core_hex}c20400",
            "stack-arg-nonzero-const-select-stdcall",
            expression,
            setcc,
            true_value,
            false_value,
            base_value,
            scale,
        )
    )

for index, (name, hex_bytes, rule, expression, setcc, true_value, false_value, base_value, scale) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x410000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "i386"}

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert sourcegen_candidate["generator"]["setcc"] == setcc, sourcegen_candidate
    assert sourcegen_candidate["generator"]["trueValue"] == true_value, sourcegen_candidate
    assert sourcegen_candidate["generator"]["falseValue"] == false_value, sourcegen_candidate
    assert sourcegen_candidate["generator"]["baseValue"] == base_value, sourcegen_candidate
    assert sourcegen_candidate["generator"]["scale"] == scale, sourcegen_candidate
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
  --limit 12 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 12 and .attemptedCandidates == 12 and .semanticCodeSliceMatchedCandidates == 12 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 12 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 12' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 12 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 12 and
  ([.[] | select(.rule == "stack-arg-nonzero-const-select-cdecl")] | length) == 6 and
  ([.[] | select(.rule == "stack-arg-nonzero-const-select-stdcall")] | length) == 6 and
  ([.[] | select(.generationEvidence.setcc == "setne")] | length) == 6 and
  ([.[] | select(.generationEvidence.setcc == "sete")] | length) == 6 and
  ([.[] | select(.generationEvidence.scale == 2)] | length) == 4 and
  ([.[] | select(.generationEvidence.scale == 4)] | length) == 4 and
  ([.[] | select(.generationEvidence.scale == 8)] | length) == 4
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
