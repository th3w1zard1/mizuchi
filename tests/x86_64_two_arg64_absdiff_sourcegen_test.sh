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
hex_bytes = "4889f04829f84829f7480f43c7c3"
rule = "x86-64-two-args64-absdiff-cdecl"
expression = "a > b ? a - b : b - a"
pattern = "mov-rax-rsi-sub-rax-rdi-sub-rdi-rsi-cmovae-rax-rdi-ret"

tasks = []
for index, target_format in enumerate([None, "macho"]):
    name = ("elf" if target_format is None else "macho") + "_absdiff64"
    address = 0x6C0000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64", "argumentBits": 64}
    if target_format:
        task["targetFormat"] = target_format
    data = bytes.fromhex(hex_bytes)

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["generator"]["registerArgs"] == ["rdi", "rsi"], sourcegen_candidate
    assert sourcegen_candidate["generator"]["cmov"] == "cmovae", sourcegen_candidate
    assert sourcegen_candidate["generator"]["pattern"] == pattern, sourcegen_candidate
    assert sourcegen_candidate["generator"]["expression"] == expression, sourcegen_candidate
    assert "unsigned long long a, unsigned long long b" in sourcegen_candidate["source"], sourcegen_candidate["source"]
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
  --limit 2 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 2 and .attemptedCandidates == 2 and .semanticCodeSliceMatchedCandidates == 2 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 2 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 2' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 2 and
  ([.[] | select(.rule == "x86-64-two-args64-absdiff-cdecl" and .status == "code-slice-matched" and .differences == 0)] | length) == 2 and
  ([.[] | select(.generationEvidence.registerArgs == ["rdi", "rsi"] and .generationEvidence.cmov == "cmovae" and .generationEvidence.pattern == "mov-rax-rsi-sub-rax-rdi-sub-rdi-rsi-cmovae-rax-rdi-ret")] | length) == 2
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
