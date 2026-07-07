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
    ("choose_5_3", "31c085ff0f95c08d044503000000c3", "value != 0 ? 0x00000005u : 0x00000003u", "setne", 2),
    ("choose_3_5", "31c085ff0f94c08d044503000000c3", "value != 0 ? 0x00000003u : 0x00000005u", "sete", 2),
    ("choose_7_3", "31c085ff0f95c08d048503000000c3", "value != 0 ? 0x00000007u : 0x00000003u", "setne", 4),
    ("choose_3_7", "31c085ff0f94c08d048503000000c3", "value != 0 ? 0x00000003u : 0x00000007u", "sete", 4),
    ("choose_9_1", "31c085ff0f95c08d04c501000000c3", "value != 0 ? 0x00000009u : 0x00000001u", "setne", 8),
    ("choose_1_9", "31c085ff0f94c08d04c501000000c3", "value != 0 ? 0x00000001u : 0x00000009u", "sete", 8),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, expression, setcc, scale in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, expression, setcc, scale, target_format))

for index, (name, hex_bytes, expression, setcc, scale, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x610000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == "x86-64-arg-nonzero-const-select-cdecl", sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert sourcegen_candidate["generator"]["setcc"] == setcc, sourcegen_candidate
    assert sourcegen_candidate["generator"]["scale"] == scale, sourcegen_candidate
    assert "unsigned int value" in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert expression in sourcegen_candidate["source"], sourcegen_candidate["source"]

    synthesis_candidates = generate({**task, "entry": hex(address), "bytes": hex_bytes}, 16)
    assert any(candidate.rule == "x86-64-arg-nonzero-const-select-cdecl" for candidate in synthesis_candidates), synthesis_candidates

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
  ([.[] | select(.rule == "x86-64-arg-nonzero-const-select-cdecl")] | length) == 12 and
  ([.[] | select(.generationEvidence.scale == 2)] | length) == 4 and
  ([.[] | select(.generationEvidence.scale == 4)] | length) == 4 and
  ([.[] | select(.generationEvidence.scale == 8)] | length) == 4 and
  ([.[] | select(.generationEvidence.setcc == "setne")] | length) == 6 and
  ([.[] | select(.generationEvidence.setcc == "sete")] | length) == 6
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
