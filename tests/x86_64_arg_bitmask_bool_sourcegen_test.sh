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
    ("bit0_nonzero", "89f883e001c3", "x86-64-arg-bitmask-nonzero-cdecl", "0x00000001u) != 0", "0x00000001", None, None),
    ("bit0_zero", "89f8f7d083e001c3", "x86-64-arg-bitmask-zero-cdecl", "0x00000001u) == 0", "0x00000001", None, None),
    ("bit3_nonzero", "89f8c1e80383e001c3", "x86-64-arg-bitmask-nonzero-cdecl", "0x00000008u) != 0", "0x00000008", 3, None),
    ("bit3_zero", "31c040f6c7080f94c0c3", "x86-64-arg-bitmask-zero-cdecl", "0x00000008u) == 0", "0x00000008", None, "sete"),
    ("mask7_nonzero", "31c040f6c7070f95c0c3", "x86-64-arg-bitmask-nonzero-cdecl", "0x00000007u) != 0", "0x00000007", None, "setne"),
    ("mask7_zero", "31c040f6c7070f94c0c3", "x86-64-arg-bitmask-zero-cdecl", "0x00000007u) == 0", "0x00000007", None, "sete"),
    ("mask256_nonzero", "89f8c1e80883e001c3", "x86-64-arg-bitmask-nonzero-cdecl", "0x00000100u) != 0", "0x00000100", 8, None),
    ("mask256_zero", "31c0f7c7000100000f94c0c3", "x86-64-arg-bitmask-zero-cdecl", "0x00000100u) == 0", "0x00000100", None, "sete"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, expression, mask, shift, setcc in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, expression, mask, shift, setcc, target_format))

for index, (name, hex_bytes, rule, expression, mask, shift, setcc, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x660000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["generator"]["mask"] == mask, sourcegen_candidate
    assert sourcegen_candidate["generator"]["shift"] == shift, sourcegen_candidate
    assert sourcegen_candidate["generator"]["setcc"] == setcc, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert "unsigned int value" in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert expression in sourcegen_candidate["source"], sourcegen_candidate["source"]

    synthesis_candidates = generate({**task, "entry": hex(address), "bytes": hex_bytes}, 16)
    assert any(candidate.rule == rule for candidate in synthesis_candidates), synthesis_candidates

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
  ([.[] | select(.rule == "x86-64-arg-bitmask-nonzero-cdecl")] | length) == 8 and
  ([.[] | select(.rule == "x86-64-arg-bitmask-zero-cdecl")] | length) == 8 and
  ([.[] | select(.generationEvidence.mask == "0x00000001")] | length) == 4 and
  ([.[] | select(.generationEvidence.mask == "0x00000008")] | length) == 4 and
  ([.[] | select(.generationEvidence.mask == "0x00000007")] | length) == 4 and
  ([.[] | select(.generationEvidence.mask == "0x00000100")] | length) == 4
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
