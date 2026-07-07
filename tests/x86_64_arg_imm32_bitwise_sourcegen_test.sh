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
    ("and_mixed", "89f825ff00ff00c3", "x86-64-arg-and-imm32-cdecl", "value & 0x00ff00ffu"),
    ("and_high", "89f82500000080c3", "x86-64-arg-and-imm32-cdecl", "value & 0x80000000u"),
    ("or_low", "89f80dff000000c3", "x86-64-arg-or-imm32-cdecl", "value | 0x000000ffu"),
    ("or_mixed", "89f80dff00ff00c3", "x86-64-arg-or-imm32-cdecl", "value | 0x00ff00ffu"),
    ("or_high", "89f80d00000080c3", "x86-64-arg-or-imm32-cdecl", "value | 0x80000000u"),
    ("xor_low", "89f835ff000000c3", "x86-64-arg-xor-imm32-cdecl", "value ^ 0x000000ffu"),
    ("xor_mixed", "89f835ff00ff00c3", "x86-64-arg-xor-imm32-cdecl", "value ^ 0x00ff00ffu"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, expression in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, expression, target_format))

for index, (name, hex_bytes, rule, expression, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x570000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["generator"]["immediateBits"] == 32, sourcegen_candidate
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
  --limit 14 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 14 and .attemptedCandidates == 14 and .semanticCodeSliceMatchedCandidates == 14 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 14 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 14' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 14 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 14 and
  ([.[] | select(.rule == "x86-64-arg-and-imm32-cdecl")] | length) == 4 and
  ([.[] | select(.rule == "x86-64-arg-or-imm32-cdecl")] | length) == 6 and
  ([.[] | select(.rule == "x86-64-arg-xor-imm32-cdecl")] | length) == 4
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
