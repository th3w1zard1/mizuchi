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
    ("b_add_a_sub_c", "8b442408034424042b44240c", "b + a - c"),
    ("a_sub_b_add_c", "8b4424042b4424080344240c", "a - b + c"),
    ("c_sub_ba", "8b44240c8b4c2408034c240429c8", "c - (b + a)"),
    ("a_sub_bc", "8b4424048b4c2408034c240c29c8", "a - (b + c)"),
]

patterns = []
for name, core_hex, expression in base_patterns:
    patterns.append((f"{name}_cdecl", f"{core_hex}c3", "three-stack-args-add-sub-cdecl", expression))
    patterns.append((f"{name}_stdcall", f"{core_hex}c20c00", "three-stack-args-add-sub-stdcall", expression))

tasks = []
for index, (name, hex_bytes, rule, expression) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x416000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "i386"}
    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert sourcegen_candidate["generator"]["expression"] == expression, sourcegen_candidate
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
  --limit 8 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 8 and .attemptedCandidates == 8 and .semanticCodeSliceMatchedCandidates == 8 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 8 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 8' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 8 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 8 and
  ([.[] | select(.rule == "three-stack-args-add-sub-cdecl")] | length) == 4 and
  ([.[] | select(.rule == "three-stack-args-add-sub-stdcall")] | length) == 4 and
  ([.[] | select(.generationEvidence.stackBytes == 12)] | length) == 4 and
  ([.[] | select(.generationEvidence.pattern == "mov-eax-stackX-op-eax-stackY-op-eax-stackZ")] | length) == 4 and
  ([.[] | select(.generationEvidence.pattern == "mov-eax-stackX-mov-ecx-stackY-add-ecx-stackZ-sub-eax-ecx")] | length) == 4
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
