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
setcc = {
    "eq": "94",
    "ne": "95",
    "lt": "92",
    "le": "96",
    "gt": "97",
    "ge": "93",
}

patterns = []
for suffix, opcode in setcc.items():
    patterns.append((f"{suffix}_cdecl", f"8b4c240431c03b4c24080f{opcode}c0c3", f"two-stack-args-uint-{suffix}-cdecl"))
    patterns.append((f"{suffix}_stdcall", f"8b4c240431c03b4c24080f{opcode}c0c20800", f"two-stack-args-uint-{suffix}-stdcall"))

tasks = []
for index, (name, hex_bytes, rule) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x403000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "i386"}
    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert "return a " in sourcegen_candidate["source"], sourcegen_candidate["source"]

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
  --limit 12 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 12 and .attemptedCandidates == 12 and .semanticCodeSliceMatchedCandidates == 12 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 12 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 12' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 12 and
  ([.[] | select(.rule | startswith("two-stack-args-uint-"))] | length) == 12 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 12 and
  ([.[] | select(.rule | endswith("-cdecl"))] | length) == 6 and
  ([.[] | select(.rule | endswith("-stdcall"))] | length) == 6
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
