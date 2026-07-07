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
    ("shr64_1", "4889f848d1e8c3", "x86-64-arg64-shr-imm8-cdecl", "unsigned long long value", "value >> 1", ">>", 1, "mov-rax-rdi-shift-one-ret"),
    ("sar64_1", "4889f848d1f8c3", "x86-64-arg64-sar-imm8-cdecl", "long long value", "value >> 1", ">>", 1, "mov-rax-rdi-shift-one-ret"),
    ("shl64_7", "4889f848c1e007c3", "x86-64-arg64-shl-imm8-cdecl", "unsigned long long value", "value << 7", "<<", 7, "mov-rax-rdi-shift-imm8-ret"),
    ("shr64_7", "4889f848c1e807c3", "x86-64-arg64-shr-imm8-cdecl", "unsigned long long value", "value >> 7", ">>", 7, "mov-rax-rdi-shift-imm8-ret"),
    ("sar64_7", "4889f848c1f807c3", "x86-64-arg64-sar-imm8-cdecl", "long long value", "value >> 7", ">>", 7, "mov-rax-rdi-shift-imm8-ret"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, signature_fragment, expression, operator, shift, pattern in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, signature_fragment, expression, operator, shift, pattern, target_format))

for index, (name, hex_bytes, rule, signature_fragment, expression, operator, shift, pattern, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x6D0000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["generator"]["registerArg"] == "rdi", sourcegen_candidate
    assert sourcegen_candidate["generator"]["operator"] == operator, sourcegen_candidate
    assert sourcegen_candidate["generator"]["shift"] == shift, sourcegen_candidate
    assert sourcegen_candidate["generator"]["pattern"] == pattern, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert signature_fragment in sourcegen_candidate["source"], sourcegen_candidate["source"]
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
  --limit 10 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 10 and .attemptedCandidates == 10 and .semanticCodeSliceMatchedCandidates == 10 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 10 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 10' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 10 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 10 and
  ([.[] | select(.rule == "x86-64-arg64-shl-imm8-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-arg64-shr-imm8-cdecl")] | length) == 4 and
  ([.[] | select(.rule == "x86-64-arg64-sar-imm8-cdecl")] | length) == 4 and
  ([.[] | select(.generationEvidence.registerArg == "rdi")] | length) == 10
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
