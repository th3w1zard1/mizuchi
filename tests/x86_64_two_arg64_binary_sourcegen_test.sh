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
    ("add64", "488d0437c3", "x86-64-two-args64-add-cdecl", "+", "lea-rax-rdi-rsi-ret"),
    ("sub64", "4889f84829f0c3", "x86-64-two-args64-sub-cdecl", "-", "mov-rax-rdi-sub-rax-rsi-ret"),
    ("mul64", "4889f8480fafc6c3", "x86-64-two-args64-mul-cdecl", "*", "mov-rax-rdi-imul-rax-rsi-ret"),
    ("and64", "4889f84821f0c3", "x86-64-two-args64-and-cdecl", "&", "mov-rax-rdi-and-rax-rsi-ret"),
    ("or64", "4889f84809f0c3", "x86-64-two-args64-or-cdecl", "|", "mov-rax-rdi-or-rax-rsi-ret"),
    ("xor64", "4889f84831f0c3", "x86-64-two-args64-xor-cdecl", "^", "mov-rax-rdi-xor-rax-rsi-ret"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, operator, pattern in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, operator, pattern, target_format))

for index, (name, hex_bytes, rule, operator, pattern, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x710000 + index * 0x10
    task = {
        "name": name,
        "address": address,
        "architectureHint": "x86_64",
        "argumentBits": 64,
    }
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["generator"]["registerArgs"] == ["rdi", "rsi"], sourcegen_candidate
    assert sourcegen_candidate["generator"]["operator"] == operator, sourcegen_candidate
    assert sourcegen_candidate["generator"]["pattern"] == pattern, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert "unsigned long long a, unsigned long long b" in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert f"return a {operator} b;" in sourcegen_candidate["source"], sourcegen_candidate["source"]

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
  --limit 12 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 12 and .attemptedCandidates == 12 and .semanticCodeSliceMatchedCandidates == 12 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 12 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 12' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 12 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 12 and
  ([.[] | select(.rule == "x86-64-two-args64-add-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-two-args64-sub-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-two-args64-mul-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-two-args64-and-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-two-args64-or-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-two-args64-xor-cdecl")] | length) == 2 and
  ([.[] | select(.generationEvidence.registerArgs == ["rdi", "rsi"])] | length) == 12
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
