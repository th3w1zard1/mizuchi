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
    ("add64_128", "488d8780000000c3", "x86-64-arg64-add-imm32-cdecl", "+", "0x00000080", "lea-rax-rdi-disp32-ret", "value + 0x00000080ull"),
    ("add64_big", "488d8778563412c3", "x86-64-arg64-add-imm32-cdecl", "+", "0x12345678", "lea-rax-rdi-disp32-ret", "value + 0x12345678ull"),
    ("sub64_129", "488d877fffffffc3", "x86-64-arg64-sub-imm32-cdecl", "-", "0x00000081", "lea-rax-rdi-disp32-ret", "value - 0x00000081ull"),
    ("xor64_big", "4889f8483578563412c3", "x86-64-arg64-xor-imm32-cdecl", "^", "0x12345678", "mov-rax-rdi-rex-accum-op-rax-imm32-ret", "value ^ 0x12345678ull"),
    ("or64_big", "4889f8480d78563412c3", "x86-64-arg64-or-imm32-cdecl", "|", "0x12345678", "mov-rax-rdi-rex-accum-op-rax-imm32-ret", "value | 0x12345678ull"),
    ("and64_big", "4889f82578563412c3", "x86-64-arg64-and-imm32-cdecl", "&", "0x12345678", "mov-rax-rdi-and-eax-imm32-ret", "value & 0x12345678ull"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, operator, immediate, pattern, expression in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, operator, immediate, pattern, expression, target_format))

for index, (name, hex_bytes, rule, operator, immediate, pattern, expression, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x6A0000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["generator"]["registerArg"] == "rdi", sourcegen_candidate
    assert sourcegen_candidate["generator"]["operator"] == operator, sourcegen_candidate
    assert sourcegen_candidate["generator"]["immediate"] == immediate, sourcegen_candidate
    assert sourcegen_candidate["generator"]["immediateBits"] == 32, sourcegen_candidate
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
  --limit 12 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 12 and .attemptedCandidates == 12 and .semanticCodeSliceMatchedCandidates == 12 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 12 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 12' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 12 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 12 and
  ([.[] | select(.rule == "x86-64-arg64-add-imm32-cdecl")] | length) == 4 and
  ([.[] | select(.rule == "x86-64-arg64-sub-imm32-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-arg64-xor-imm32-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-arg64-or-imm32-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-arg64-and-imm32-cdecl")] | length) == 2 and
  ([.[] | select(.generationEvidence.registerArg == "rdi" and .generationEvidence.immediateBits == 32)] | length) == 12
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
