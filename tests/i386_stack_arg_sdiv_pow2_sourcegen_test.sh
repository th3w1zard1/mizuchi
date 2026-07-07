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
    ("sdiv2", "8b4c240489c8c1e81f01c8d1f8", 2, 1, 1, "value / 2"),
    ("sdiv4", "8b4c24048d410385c90f49c1c1f802", 4, 2, 3, "value / 4"),
    ("sdiv8", "8b4c24048d410785c90f49c1c1f803", 8, 3, 7, "value / 8"),
]

tasks = []
patterns = []
for convention, ret in [("cdecl", "c3"), ("stdcall", "c20400")]:
    for name, core_hex, divisor, shift, bias, expression in base_patterns:
        patterns.append((f"{name}_{convention}", core_hex + ret, convention, divisor, shift, bias, expression))

for index, (name, hex_bytes, convention, divisor, shift, bias, expression) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x40D000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "i386"}

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    expected_rule = f"stack-arg-sdiv-pow2-{convention}"
    assert sourcegen_candidate["generator"]["rule"] == expected_rule, sourcegen_candidate
    assert sourcegen_candidate["generator"]["divisor"] == divisor, sourcegen_candidate
    assert sourcegen_candidate["generator"]["shift"] == shift, sourcegen_candidate
    assert sourcegen_candidate["generator"]["bias"] == bias, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert "int value" in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert expression in sourcegen_candidate["source"], sourcegen_candidate["source"]

    synthesis_candidates = generate({**task, "entry": hex(address), "bytes": hex_bytes}, 16)
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
  --limit 6 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 6 and .attemptedCandidates == 6 and .semanticCodeSliceMatchedCandidates == 6 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 6 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 6' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 6 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 6 and
  ([.[] | select(.rule == "stack-arg-sdiv-pow2-cdecl")] | length) == 3 and
  ([.[] | select(.rule == "stack-arg-sdiv-pow2-stdcall")] | length) == 3 and
  ([.[] | select(.generationEvidence.divisor == 2)] | length) == 2 and
  ([.[] | select(.generationEvidence.divisor == 4)] | length) == 2 and
  ([.[] | select(.generationEvidence.divisor == 8)] | length) == 2
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
