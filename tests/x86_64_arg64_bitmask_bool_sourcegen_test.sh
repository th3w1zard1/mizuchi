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
    ("bit0_nonzero", "4889f883e001c3", "x86-64-arg64-bitmask-nonzero-cdecl", "0x0000000000000001", None, None, "0x0000000000000001ull) != 0"),
    ("bit3_nonzero", "4889f8c1e80383e001c3", "x86-64-arg64-bitmask-nonzero-cdecl", "0x0000000000000008", 3, None, "0x0000000000000008ull) != 0"),
    ("bit3_zero", "31c040f6c7080f94c0c3", "x86-64-arg64-bitmask-zero-cdecl", "0x0000000000000008", None, "sete", "0x0000000000000008ull) == 0"),
    ("mask7_nonzero", "31c040f6c7070f95c0c3", "x86-64-arg64-bitmask-nonzero-cdecl", "0x0000000000000007", None, "setne", "0x0000000000000007ull) != 0"),
    ("mask7_zero", "31c040f6c7070f94c0c3", "x86-64-arg64-bitmask-zero-cdecl", "0x0000000000000007", None, "sete", "0x0000000000000007ull) == 0"),
    ("bit8_nonzero", "4889f8c1e80883e001c3", "x86-64-arg64-bitmask-nonzero-cdecl", "0x0000000000000100", 8, None, "0x0000000000000100ull) != 0"),
    ("bit8_zero", "31c0f7c7000100000f94c0c3", "x86-64-arg64-bitmask-zero-cdecl", "0x0000000000000100", None, "sete", "0x0000000000000100ull) == 0"),
    ("bit40_nonzero", "4889f848c1e82883e001c3", "x86-64-arg64-bitmask-nonzero-cdecl", "0x0000010000000000", 40, None, "0x0000010000000000ull) != 0"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, mask, shift, setcc, expression in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, mask, shift, setcc, expression, target_format))

for index, (name, hex_bytes, rule, mask, shift, setcc, expression, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x700000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64", "argumentBits": 64}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["generator"]["registerArg"] == "rdi", sourcegen_candidate
    assert sourcegen_candidate["generator"]["mask"] == mask, sourcegen_candidate
    assert sourcegen_candidate["generator"]["shift"] == shift, sourcegen_candidate
    assert sourcegen_candidate["generator"]["setcc"] == setcc, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert "unsigned long long value" in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert expression in sourcegen_candidate["source"], sourcegen_candidate["source"]

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
            "argumentBits": 64,
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
  ([.[] | select(.rule == "x86-64-arg64-bitmask-nonzero-cdecl")] | length) == 10 and
  ([.[] | select(.rule == "x86-64-arg64-bitmask-zero-cdecl")] | length) == 6 and
  ([.[] | select(.generationEvidence.registerArg == "rdi")] | length) == 16 and
  ([.[] | select(.generationEvidence.shift == 40)] | length) == 2
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
