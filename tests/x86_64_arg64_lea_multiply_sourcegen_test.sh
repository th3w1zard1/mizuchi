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
rule = "x86-64-arg64-mul-lea-cdecl"
base_patterns = [
    ("mul64_2", "488d043fc3", 2, "lea-rax-rdi-rdi-ret"),
    ("mul64_3", "488d047fc3", 3, "lea-rax-rdi-rdi2-ret"),
    ("mul64_4", "488d04bd00000000c3", 4, "lea-rax-rdi4-ret"),
    ("mul64_5", "488d04bfc3", 5, "lea-rax-rdi-rdi4-ret"),
    ("mul64_8", "488d04fd00000000c3", 8, "lea-rax-rdi8-ret"),
    ("mul64_9", "488d04ffc3", 9, "lea-rax-rdi-rdi8-ret"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, multiplier, pattern in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, multiplier, pattern, target_format))

for index, (name, hex_bytes, multiplier, pattern, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x6B0000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["generator"]["registerArg"] == "rdi", sourcegen_candidate
    assert sourcegen_candidate["generator"]["operator"] == "*", sourcegen_candidate
    assert sourcegen_candidate["generator"]["multiplier"] == multiplier, sourcegen_candidate
    assert sourcegen_candidate["generator"]["pattern"] == pattern, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert "unsigned long long value" in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert f"value * {multiplier}ull" in sourcegen_candidate["source"], sourcegen_candidate["source"]

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
  ([.[] | select(.rule == "x86-64-arg64-mul-lea-cdecl")] | length) == 12 and
  ([.[] | select(.generationEvidence.registerArg == "rdi" and .generationEvidence.operator == "*")] | length) == 12 and
  ([.[] | select(.generationEvidence.multiplier == 2)] | length) == 2 and
  ([.[] | select(.generationEvidence.multiplier == 3)] | length) == 2 and
  ([.[] | select(.generationEvidence.multiplier == 4)] | length) == 2 and
  ([.[] | select(.generationEvidence.multiplier == 5)] | length) == 2 and
  ([.[] | select(.generationEvidence.multiplier == 8)] | length) == 2 and
  ([.[] | select(.generationEvidence.multiplier == 9)] | length) == 2
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
