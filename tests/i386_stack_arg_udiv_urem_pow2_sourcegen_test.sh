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
    ("udiv2", "8b442404d1e8", "udiv", "/", 2, 1, None, "value / 2u"),
    ("udiv4", "8b442404c1e802", "udiv", "/", 4, 2, None, "value / 4u"),
    ("udiv8", "8b442404c1e803", "udiv", "/", 8, 3, None, "value / 8u"),
    ("urem2", "8b44240483e001", "urem", "%", 2, 1, 1, "value % 2u"),
    ("urem4", "8b44240483e003", "urem", "%", 4, 2, 3, "value % 4u"),
    ("urem8", "8b44240483e007", "urem", "%", 8, 3, 7, "value % 8u"),
]

tasks = []
patterns = []
for convention, ret in [("cdecl", "c3"), ("stdcall", "c20400")]:
    for name, core_hex, operation, operator, divisor, shift, mask, expression in base_patterns:
        patterns.append((f"{name}_{convention}", core_hex + ret, convention, operation, operator, divisor, shift, mask, expression))

for index, (name, hex_bytes, convention, operation, operator, divisor, shift, mask, expression) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x40F000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "i386"}

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    expected_rule = f"stack-arg-{operation}-pow2-{convention}"
    assert sourcegen_candidate["generator"]["rule"] == expected_rule, sourcegen_candidate
    assert sourcegen_candidate["generator"]["operator"] == operator, sourcegen_candidate
    assert sourcegen_candidate["generator"]["divisor"] == divisor, sourcegen_candidate
    assert sourcegen_candidate["generator"]["shift"] == shift, sourcegen_candidate
    if mask is not None:
        assert sourcegen_candidate["generator"]["mask"] == mask, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert "unsigned int value" in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert expression in sourcegen_candidate["source"], sourcegen_candidate["source"]

    synthesis_candidates = generate({**task, "entry": hex(address), "bytes": hex_bytes}, 20)
    assert synthesis_candidates, name
    assert any(candidate.rule == expected_rule for candidate in synthesis_candidates), synthesis_candidates

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
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 12 and
  ([.[] | select(.rule == "stack-arg-udiv-pow2-cdecl")] | length) == 3 and
  ([.[] | select(.rule == "stack-arg-udiv-pow2-stdcall")] | length) == 3 and
  ([.[] | select(.rule == "stack-arg-urem-pow2-cdecl")] | length) == 3 and
  ([.[] | select(.rule == "stack-arg-urem-pow2-stdcall")] | length) == 3 and
  ([.[] | select(.generationEvidence.divisor == 2)] | length) == 4 and
  ([.[] | select(.generationEvidence.divisor == 4)] | length) == 4 and
  ([.[] | select(.generationEvidence.divisor == 8)] | length) == 4
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
