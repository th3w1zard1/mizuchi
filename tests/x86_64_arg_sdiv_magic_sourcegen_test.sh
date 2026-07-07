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
    ("sdiv3", "4863c74869c0565555554889c148c1e93f48c1e82001c8c3", 3, "0x55555556", 32, "value / 3"),
    ("sdiv5", "4863c74869c0676666664889c148c1e93f48c1f82101c8c3", 5, "0x66666667", 33, "value / 5"),
    ("sdiv10", "4863c74869c0676666664889c148c1e93f48c1f82201c8c3", 10, "0x66666667", 34, "value / 10"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, divisor, multiplier, shift, expression in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, divisor, multiplier, shift, expression, target_format))

for index, (name, hex_bytes, divisor, multiplier, shift, expression, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x790000 + index * 0x20
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == "x86-64-arg-sdiv-magic-cdecl", sourcegen_candidate
    assert sourcegen_candidate["generator"]["divisor"] == divisor, sourcegen_candidate
    assert sourcegen_candidate["generator"]["multiplier"] == multiplier, sourcegen_candidate
    assert sourcegen_candidate["generator"]["shift"] == shift, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert "int value" in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert expression in sourcegen_candidate["source"], sourcegen_candidate["source"]

    synthesis_candidates = generate({**task, "entry": hex(address), "bytes": hex_bytes}, 16)
    assert synthesis_candidates, name
    assert synthesis_candidates[0].rule == "x86-64-arg-sdiv-magic-cdecl", synthesis_candidates

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
  --limit 6 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 6 and .attemptedCandidates == 6 and .semanticCodeSliceMatchedCandidates == 6 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 6 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 6' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 6 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 6 and
  ([.[] | select(.rule == "x86-64-arg-sdiv-magic-cdecl")] | length) == 6 and
  ([.[] | select(.generationEvidence.divisor == 3)] | length) == 2 and
  ([.[] | select(.generationEvidence.divisor == 5)] | length) == 2 and
  ([.[] | select(.generationEvidence.divisor == 10)] | length) == 2
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
