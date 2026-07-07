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
patterns = [
    ("eq_cdecl", "31c0837c2404050f94c0c3", "stack-arg-uint-eq-imm8-cdecl"),
    ("ne_cdecl", "31c0837c2404050f95c0c3", "stack-arg-uint-ne-imm8-cdecl"),
    ("lt_cdecl", "31c0837c2404050f92c0c3", "stack-arg-uint-lt-imm8-cdecl"),
    ("le_cdecl", "31c0837c2404060f92c0c3", "stack-arg-uint-lt-imm8-cdecl"),
    ("gt_cdecl", "31c0837c2404060f93c0c3", "stack-arg-uint-ge-imm8-cdecl"),
    ("ge_cdecl", "31c0837c2404050f93c0c3", "stack-arg-uint-ge-imm8-cdecl"),
    ("eq_stdcall", "31c0837c2404050f94c0c20400", "stack-arg-uint-eq-imm8-stdcall"),
    ("ne_stdcall", "31c0837c2404050f95c0c20400", "stack-arg-uint-ne-imm8-stdcall"),
    ("lt_stdcall", "31c0837c2404050f92c0c20400", "stack-arg-uint-lt-imm8-stdcall"),
    ("le_stdcall", "31c0837c2404060f92c0c20400", "stack-arg-uint-lt-imm8-stdcall"),
    ("gt_stdcall", "31c0837c2404060f93c0c20400", "stack-arg-uint-ge-imm8-stdcall"),
    ("ge_stdcall", "31c0837c2404050f93c0c20400", "stack-arg-uint-ge-imm8-stdcall"),
]

tasks = []
for index, (name, hex_bytes, rule) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x406000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "i386"}
    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert "unsigned int value" in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert "return value" in sourcegen_candidate["source"], sourcegen_candidate["source"]

    synthesis_candidates = generate({**task, "entry": hex(address), "bytes": hex_bytes}, 16)
    assert any(candidate.rule == rule for candidate in synthesis_candidates), (rule, synthesis_candidates)

    bytes_path = tmp / f"{name}.target.bin"
    bytes_path.write_bytes(data)
    tasks.append(
        {
            "schema": "reconkit.source-task.v1",
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

PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m reconkit_re.source_parity_synthesize \
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
  ([.[] | select((.rule | startswith("stack-arg-uint-")) and (.rule | contains("-imm8-")))] | length) == 12 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 12 and
  ([.[] | select(.rule | endswith("-cdecl"))] | length) == 6 and
  ([.[] | select(.rule | endswith("-stdcall"))] | length) == 6
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
