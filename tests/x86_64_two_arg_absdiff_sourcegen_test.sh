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
    ("u32_absdiff", "89f029f829f70f43c7c3", "x86-64-two-args-absdiff-cdecl", "unsigned int", "cmovae", "mov-eax-esi-sub-eax-edi-sub-edi-esi-cmovae-eax-edi-ret"),
    ("s32_absdiff", "89f829f029fe0f4dc6c3", "x86-64-two-args-int-absdiff-cdecl", "int", "cmovge", "mov-eax-edi-sub-eax-esi-sub-esi-edi-cmovge-eax-esi-ret"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, value_type, cmov, pattern in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, value_type, cmov, pattern, target_format))

for index, (name, hex_bytes, rule, value_type, cmov, pattern, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x6D0000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["generator"]["registerArgs"] == ["edi", "esi"], sourcegen_candidate
    assert sourcegen_candidate["generator"]["valueType"] == value_type, sourcegen_candidate
    assert sourcegen_candidate["generator"]["cmov"] == cmov, sourcegen_candidate
    assert sourcegen_candidate["generator"]["pattern"] == pattern, sourcegen_candidate
    assert "return a > b ? a - b : b - a;" in sourcegen_candidate["source"], sourcegen_candidate["source"]

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
  --limit 4 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 4 and .attemptedCandidates == 4 and .semanticCodeSliceMatchedCandidates == 4 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 4 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 4' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 4 and
  ([.[] | select(.rule == "x86-64-two-args-absdiff-cdecl" and .status == "code-slice-matched" and .differences == 0)] | length) == 2 and
  ([.[] | select(.rule == "x86-64-two-args-int-absdiff-cdecl" and .status == "code-slice-matched" and .differences == 0)] | length) == 2 and
  ([.[] | select(.generationEvidence.expression == "a > b ? a - b : b - a")] | length) == 4 and
  ([.[] | select(.generationEvidence.registerArgs == ["edi", "esi"])] | length) == 4
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
