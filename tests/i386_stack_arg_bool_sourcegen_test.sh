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
    ("arg_nonzero", "31c0837c2404000f95c0c3", "stack-arg-nonzero-bool-cdecl", True),
    ("arg_zero", "31c0837c2404000f94c0c3", "stack-arg-zero-bool-cdecl", True),
    ("arg_nonzero_msvc_xor", "33c0837c2404000f95c0c3", "stack-arg-nonzero-bool-cdecl", False),
    ("arg_zero_msvc_xor", "33c0837c2404000f94c0c3", "stack-arg-zero-bool-cdecl", False),
    ("arg_nonzero_stdcall", "31c0837c2404000f95c0c20400", "stack-arg-nonzero-bool-stdcall", True),
    ("arg_zero_stdcall", "31c0837c2404000f94c0c20400", "stack-arg-zero-bool-stdcall", True),
]

tasks = []
for index, (name, hex_bytes, rule, compile_with_clang) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x401000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "i386"}
    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert "return value" in sourcegen_candidate["source"], sourcegen_candidate["source"]

    synthesis_candidates = generate({**task, "entry": hex(address), "bytes": hex_bytes}, 8)
    assert any(candidate.rule == rule for candidate in synthesis_candidates), (rule, synthesis_candidates)

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
  ([.[] | select(.rule == "stack-arg-nonzero-bool-cdecl" and .status == "code-slice-matched" and .differences == 0)] | length) == 1 and
  ([.[] | select(.rule == "stack-arg-zero-bool-cdecl" and .status == "code-slice-matched" and .differences == 0)] | length) == 1 and
  ([.[] | select(.rule == "stack-arg-nonzero-bool-stdcall" and .status == "code-slice-matched" and .differences == 0)] | length) == 1 and
  ([.[] | select(.rule == "stack-arg-zero-bool-stdcall" and .status == "code-slice-matched" and .differences == 0)] | length) == 1
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
