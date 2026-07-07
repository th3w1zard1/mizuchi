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
patterns = [
    ("sub_cdecl", "8b4424042b442408c3", "two-stack-args-sub-cdecl", "a - b"),
    ("mul_cdecl", "8b4424080faf442404c3", "two-stack-args-mul-cdecl", "a * b"),
    ("and_cdecl", "8b44240823442404c3", "two-stack-args-and-cdecl", "a & b"),
    ("or_cdecl", "8b4424080b442404c3", "two-stack-args-or-cdecl", "a | b"),
    ("xor_cdecl", "8b44240833442404c3", "two-stack-args-xor-cdecl", "a ^ b"),
    ("sub_stdcall", "8b4424042b442408c20800", "two-stack-args-sub-stdcall", "a - b"),
    ("mul_stdcall", "8b4424080faf442404c20800", "two-stack-args-mul-stdcall", "a * b"),
    ("and_stdcall", "8b44240823442404c20800", "two-stack-args-and-stdcall", "a & b"),
    ("or_stdcall", "8b4424080b442404c20800", "two-stack-args-or-stdcall", "a | b"),
    ("xor_stdcall", "8b44240833442404c20800", "two-stack-args-xor-stdcall", "a ^ b"),
]

tasks = []
for index, (name, hex_bytes, rule, expression) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x40A000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "i386"}
    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert "unsigned int a, unsigned int b" in sourcegen_candidate["source"], sourcegen_candidate["source"]
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
            "architectureHint": "i386",
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
  ([.[] | select((.rule | startswith("two-stack-args-")) and .status == "code-slice-matched" and .differences == 0)] | length) == 10 and
  ([.[] | select(.rule | endswith("-cdecl"))] | length) == 5 and
  ([.[] | select(.rule | endswith("-stdcall"))] | length) == 5 and
  ([.[] | select(.rule == "two-stack-args-sub-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "two-stack-args-mul-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "two-stack-args-and-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "two-stack-args-or-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "two-stack-args-xor-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "two-stack-args-sub-stdcall")] | length) == 1 and
  ([.[] | select(.rule == "two-stack-args-mul-stdcall")] | length) == 1 and
  ([.[] | select(.rule == "two-stack-args-and-stdcall")] | length) == 1 and
  ([.[] | select(.rule == "two-stack-args-or-stdcall")] | length) == 1 and
  ([.[] | select(.rule == "two-stack-args-xor-stdcall")] | length) == 1
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
