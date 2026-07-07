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
    ("add3", "8d043701d0c3", "a + b + c", "lea-eax-rdi-rsi-add-eax-edx-ret"),
    ("sub_add", "29f78d0417c3", "a - b + c", "sub-edi-esi-lea-eax-rdi-rdx-ret"),
    ("sub_sum", "89f801d629f0c3", "a - b - c", "mov-eax-edi-add-esi-edx-sub-eax-esi-ret"),
    ("sub_sum_reversed", "89d001f729f8c3", "c - (a + b)", "mov-eax-edx-add-edi-esi-sub-eax-edi-ret"),
    ("mul_add", "0faffe8d0417c3", "a * b + c", "imul-edi-esi-lea-eax-rdi-rdx-ret"),
    ("mul_sub", "89f80fafc629d0c3", "a * b - c", "mov-eax-edi-imul-eax-esi-sub-eax-edx-ret"),
    ("sub_mul", "89d00faffe29f8c3", "c - a * b", "mov-eax-edx-imul-edi-esi-sub-eax-edi-ret"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, expression, pattern in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, expression, pattern, target_format))

for index, (name, hex_bytes, expression, pattern, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x620000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == "x86-64-three-args-arithmetic-cdecl", sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert sourcegen_candidate["generator"]["expression"] == expression, sourcegen_candidate
    assert sourcegen_candidate["generator"]["pattern"] == pattern, sourcegen_candidate
    assert "unsigned int a, unsigned int b, unsigned int c" in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert expression in sourcegen_candidate["source"], sourcegen_candidate["source"]

    synthesis_candidates = generate({**task, "entry": hex(address), "bytes": hex_bytes}, 16)
    assert any(candidate.rule == "x86-64-three-args-arithmetic-cdecl" for candidate in synthesis_candidates), synthesis_candidates

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
  --limit 14 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 14 and .attemptedCandidates == 14 and .semanticCodeSliceMatchedCandidates == 14 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 14 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 14' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 14 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 14 and
  ([.[] | select(.rule == "x86-64-three-args-arithmetic-cdecl")] | length) == 14 and
  ([.[] | select(.generationEvidence.expression == "a + b + c")] | length) == 2 and
  ([.[] | select(.generationEvidence.expression == "a * b + c")] | length) == 2 and
  ([.[] | select(.generationEvidence.expression == "c - a * b")] | length) == 2 and
  ([.[] | select(.generationEvidence.registerArgs == ["edi", "esi", "edx"])] | length) == 14
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
