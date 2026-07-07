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
    ("one_stdcall", "b801000000c20400", "return-one-stdcall", True),
    ("first_arg_stdcall", "8b442404c20400", "return-first-stack-arg-stdcall", True),
    ("add_cdecl_clang", "8b44240803442404c3", "add-two-stack-args-cdecl", True),
    ("add_stdcall_clang", "8b44240803442404c20800", "add-two-stack-args-stdcall", True),
    ("add_cdecl_alt", "8b44240403442408c3", "add-two-stack-args-cdecl", False),
    ("add_stdcall_alt", "8b44240403442408c20800", "add-two-stack-args-stdcall", False),
]

tasks = []
for index, (name, hex_bytes, sourcegen_rule, compile_with_clang) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x402000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "i386"}
    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == sourcegen_rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate

    synthesis_candidates = generate({**task, "entry": hex(address), "bytes": hex_bytes}, 8)
    assert any(candidate.rule == sourcegen_rule for candidate in synthesis_candidates), (sourcegen_rule, synthesis_candidates)

    if compile_with_clang:
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
  --limit 4 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 4 and .attemptedCandidates == 4 and .semanticCodeSliceMatchedCandidates == 4 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 4 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 4' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 4 and
  ([.[] | select(.rule == "return-one-stdcall" and .status == "code-slice-matched" and .differences == 0)] | length) == 1 and
  ([.[] | select(.rule == "return-first-stack-arg-stdcall" and .status == "code-slice-matched" and .differences == 0)] | length) == 1 and
  ([.[] | select(.rule == "add-two-stack-args-cdecl" and .status == "code-slice-matched" and .differences == 0)] | length) == 1 and
  ([.[] | select(.rule == "add-two-stack-args-stdcall" and .status == "code-slice-matched" and .differences == 0)] | length) == 1
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
