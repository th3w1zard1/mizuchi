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
    ("add64_1", "488d4701c3", "x86-64-arg64-add-imm32-cdecl", "+", "0x01", 8, "lea-rax-rdi-disp8-ret", "value + 0x01ull"),
    ("add64_127", "488d477fc3", "x86-64-arg64-add-imm32-cdecl", "+", "0x7f", 8, "lea-rax-rdi-disp8-ret", "value + 0x7full"),
    ("sub64_1", "488d47ffc3", "x86-64-arg64-sub-imm32-cdecl", "-", "0x01", 8, "lea-rax-rdi-disp8-ret", "value - 0x01ull"),
    ("sub64_128", "488d4780c3", "x86-64-arg64-sub-imm32-cdecl", "-", "0x80", 8, "lea-rax-rdi-disp8-ret", "value - 0x80ull"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, operator, immediate, immediate_bits, pattern, expression in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, operator, immediate, immediate_bits, pattern, expression, target_format))

for index, (name, hex_bytes, rule, operator, immediate, immediate_bits, pattern, expression, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x6B0000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["generator"]["registerArg"] == "rdi", sourcegen_candidate
    assert sourcegen_candidate["generator"]["operator"] == operator, sourcegen_candidate
    assert sourcegen_candidate["generator"]["immediate"] == immediate, sourcegen_candidate
    assert sourcegen_candidate["generator"]["immediateBits"] == immediate_bits, sourcegen_candidate
    assert sourcegen_candidate["generator"]["pattern"] == pattern, sourcegen_candidate
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
  --limit 8 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 8 and .attemptedCandidates == 8 and .semanticCodeSliceMatchedCandidates == 8 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 8 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 8' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 8 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 8 and
  ([.[] | select(.rule == "x86-64-arg64-add-imm32-cdecl")] | length) == 4 and
  ([.[] | select(.rule == "x86-64-arg64-sub-imm32-cdecl")] | length) == 4 and
  ([.[] | select(.generationEvidence.registerArg == "rdi" and .generationEvidence.immediateBits == 8 and .generationEvidence.pattern == "lea-rax-rdi-disp8-ret")] | length) == 8 and
  ([.[] | select(.generationEvidence.immediate == "0x01")] | length) == 4 and
  ([.[] | select(.generationEvidence.immediate == "0x7f")] | length) == 2 and
  ([.[] | select(.generationEvidence.immediate == "0x80")] | length) == 2
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
