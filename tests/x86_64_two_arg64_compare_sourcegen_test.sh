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
    ("eq_u64", "31c04839f70f94c0c3", "x86-64-uint64-eq-two-args-cdecl", "unsigned long long a, unsigned long long b", "a == b", "sete"),
    ("ne_u64", "31c04839f70f95c0c3", "x86-64-uint64-ne-two-args-cdecl", "unsigned long long a, unsigned long long b", "a != b", "setne"),
    ("lt_u64", "31c04839f70f92c0c3", "x86-64-uint64-lt-two-args-cdecl", "unsigned long long a, unsigned long long b", "a < b", "setb"),
    ("le_u64", "31c04839f70f96c0c3", "x86-64-uint64-le-two-args-cdecl", "unsigned long long a, unsigned long long b", "a <= b", "setbe"),
    ("gt_u64", "31c04839f70f97c0c3", "x86-64-uint64-gt-two-args-cdecl", "unsigned long long a, unsigned long long b", "a > b", "seta"),
    ("ge_u64", "31c04839f70f93c0c3", "x86-64-uint64-ge-two-args-cdecl", "unsigned long long a, unsigned long long b", "a >= b", "setae"),
    ("lt_i64", "31c04839f70f9cc0c3", "x86-64-int64-lt-two-args-cdecl", "long long a, long long b", "a < b", "setl"),
    ("le_i64", "31c04839f70f9ec0c3", "x86-64-int64-le-two-args-cdecl", "long long a, long long b", "a <= b", "setle"),
    ("gt_i64", "31c04839f70f9fc0c3", "x86-64-int64-gt-two-args-cdecl", "long long a, long long b", "a > b", "setg"),
    ("ge_i64", "31c04839f70f9dc0c3", "x86-64-int64-ge-two-args-cdecl", "long long a, long long b", "a >= b", "setge"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, signature_fragment, expression, setcc in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, signature_fragment, expression, setcc, target_format))

for index, (name, hex_bytes, rule, signature_fragment, expression, setcc, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x720000 + index * 0x10
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
    assert sourcegen_candidate["generator"]["setcc"] == setcc, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert signature_fragment in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert f"return {expression};" in sourcegen_candidate["source"], sourcegen_candidate["source"]

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
  --limit 20 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 20 and .attemptedCandidates == 20 and .semanticCodeSliceMatchedCandidates == 20 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 20 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 20' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 20 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 20 and
  ([.[] | select(.rule | startswith("x86-64-uint64-"))] | length) == 12 and
  ([.[] | select(.rule | startswith("x86-64-int64-"))] | length) == 8 and
  ([.[] | select(.generationEvidence.registerArgs == ["rdi", "rsi"])] | length) == 20 and
  ([.[] | select(.generationEvidence.pattern == "xor-eax-cmp-rdi-rsi-setcc-al-ret")] | length) == 20
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
