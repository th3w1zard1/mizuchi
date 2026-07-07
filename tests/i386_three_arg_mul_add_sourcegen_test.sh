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
    ("bc_plus_a", "8b44240c0faf44240803442404", ["c", "b"], "a", "c * b + a"),
    ("ab_plus_c", "8b4424080faf4424040344240c", ["b", "a"], "c", "b * a + c"),
    ("ac_plus_b", "8b44240c0faf44240403442408", ["c", "a"], "b", "c * a + b"),
]

patterns = []
for name, core_hex, multiply_args, add_arg, expression in base_patterns:
    patterns.append((f"{name}_cdecl", f"{core_hex}c3", "three-stack-args-mul-add-cdecl", multiply_args, add_arg, expression))
    patterns.append((f"{name}_stdcall", f"{core_hex}c20c00", "three-stack-args-mul-add-stdcall", multiply_args, add_arg, expression))

tasks = []
for index, (name, hex_bytes, rule, multiply_args, add_arg, expression) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x415000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "i386"}
    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert sourcegen_candidate["generator"]["operator"] == "*+", sourcegen_candidate
    assert sourcegen_candidate["generator"]["multiplyArgs"] == multiply_args, sourcegen_candidate
    assert sourcegen_candidate["generator"]["addArg"] == add_arg, sourcegen_candidate
    assert "unsigned int a, unsigned int b, unsigned int c" in sourcegen_candidate["source"], sourcegen_candidate["source"]
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
  --limit 6 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 6 and .attemptedCandidates == 6 and .semanticCodeSliceMatchedCandidates == 6 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 6 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 6' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 6 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 6 and
  ([.[] | select(.rule == "three-stack-args-mul-add-cdecl")] | length) == 3 and
  ([.[] | select(.rule == "three-stack-args-mul-add-stdcall")] | length) == 3 and
  ([.[] | select(.generationEvidence.addArg == "a")] | length) == 2 and
  ([.[] | select(.generationEvidence.addArg == "b")] | length) == 2 and
  ([.[] | select(.generationEvidence.addArg == "c")] | length) == 2 and
  ([.[] | select(.generationEvidence.stackBytes == 12)] | length) == 3
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
