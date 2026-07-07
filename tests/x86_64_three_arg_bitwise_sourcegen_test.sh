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
    ("and_and", "89f821f021d0c3", "(a & b) & c", "mov-eax-edi-and-eax-esi-and-eax-edx-ret"),
    ("and_or", "89f821f009d0c3", "(a & b) | c", "mov-eax-edi-and-eax-esi-or-eax-edx-ret"),
    ("and_xor", "89f821f031d0c3", "(a & b) ^ c", "mov-eax-edi-and-eax-esi-xor-eax-edx-ret"),
    ("or_and", "89f809f021d0c3", "(a | b) & c", "mov-eax-edi-or-eax-esi-and-eax-edx-ret"),
    ("or_or", "89f809f009d0c3", "(a | b) | c", "mov-eax-edi-or-eax-esi-or-eax-edx-ret"),
    ("or_xor", "89f809f031d0c3", "(a | b) ^ c", "mov-eax-edi-or-eax-esi-xor-eax-edx-ret"),
    ("xor_and", "89f831f021d0c3", "(a ^ b) & c", "mov-eax-edi-xor-eax-esi-and-eax-edx-ret"),
    ("xor_or", "89f831f009d0c3", "(a ^ b) | c", "mov-eax-edi-xor-eax-esi-or-eax-edx-ret"),
    ("xor_xor", "89f831f031d0c3", "(a ^ b) ^ c", "mov-eax-edi-xor-eax-esi-xor-eax-edx-ret"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, expression, pattern in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, expression, pattern, target_format))

for index, (name, hex_bytes, expression, pattern, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x630000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == "x86-64-three-args-bitwise-cdecl", sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert sourcegen_candidate["generator"]["expression"] == expression, sourcegen_candidate
    assert sourcegen_candidate["generator"]["pattern"] == pattern, sourcegen_candidate
    assert "unsigned int a, unsigned int b, unsigned int c" in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert expression in sourcegen_candidate["source"], sourcegen_candidate["source"]

    synthesis_candidates = generate({**task, "entry": hex(address), "bytes": hex_bytes}, 16)
    assert any(candidate.rule == "x86-64-three-args-bitwise-cdecl" for candidate in synthesis_candidates), synthesis_candidates

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
  --limit 18 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 18 and .attemptedCandidates == 18 and .semanticCodeSliceMatchedCandidates == 18 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 18 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 18' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 18 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 18 and
  ([.[] | select(.rule == "x86-64-three-args-bitwise-cdecl")] | length) == 18 and
  ([.[] | select(.generationEvidence.expression == "(a & b) & c")] | length) == 2 and
  ([.[] | select(.generationEvidence.expression == "(a | b) | c")] | length) == 2 and
  ([.[] | select(.generationEvidence.expression == "(a ^ b) ^ c")] | length) == 2 and
  ([.[] | select(.generationEvidence.registerArgs == ["edi", "esi", "edx"])] | length) == 18
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
