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
    ("add_cdecl", "8b44240483c005c3", "stack-arg-add-imm8-cdecl", "value + 0x05u"),
    ("sub_cdecl", "8b44240483c0fbc3", "stack-arg-sub-imm8-cdecl", "value - 0x05u"),
    ("and_cdecl", "8b44240483e00fc3", "stack-arg-and-imm8-cdecl", "value & 0x0fu"),
    ("or_cdecl", "8b44240483c805c3", "stack-arg-or-imm8-cdecl", "value | 0x05u"),
    ("xor_cdecl", "8b44240483f005c3", "stack-arg-xor-imm8-cdecl", "value ^ 0x05u"),
    ("add_stdcall", "8b44240483c005c20400", "stack-arg-add-imm8-stdcall", "value + 0x05u"),
    ("sub_stdcall", "8b44240483c0fbc20400", "stack-arg-sub-imm8-stdcall", "value - 0x05u"),
    ("and_stdcall", "8b44240483e00fc20400", "stack-arg-and-imm8-stdcall", "value & 0x0fu"),
    ("or_stdcall", "8b44240483c805c20400", "stack-arg-or-imm8-stdcall", "value | 0x05u"),
    ("xor_stdcall", "8b44240483f005c20400", "stack-arg-xor-imm8-stdcall", "value ^ 0x05u"),
]

tasks = []
for index, (name, hex_bytes, rule, expression) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x40B000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "i386"}
    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert "unsigned int value" in sourcegen_candidate["source"], sourcegen_candidate["source"]
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
  ([.[] | select((.rule | startswith("stack-arg-")) and (.rule | contains("-imm8-")) and .status == "code-slice-matched" and .differences == 0)] | length) == 10 and
  ([.[] | select(.rule | endswith("-cdecl"))] | length) == 5 and
  ([.[] | select(.rule | endswith("-stdcall"))] | length) == 5 and
  ([.[] | select(.rule == "stack-arg-add-imm8-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "stack-arg-sub-imm8-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "stack-arg-and-imm8-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "stack-arg-or-imm8-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "stack-arg-xor-imm8-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "stack-arg-add-imm8-stdcall")] | length) == 1 and
  ([.[] | select(.rule == "stack-arg-sub-imm8-stdcall")] | length) == 1 and
  ([.[] | select(.rule == "stack-arg-and-imm8-stdcall")] | length) == 1 and
  ([.[] | select(.rule == "stack-arg-or-imm8-stdcall")] | length) == 1 and
  ([.[] | select(.rule == "stack-arg-xor-imm8-stdcall")] | length) == 1
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
