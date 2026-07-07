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
    ("a_plus_2b", "8d0477c3", "a + 2u * b", 1, 2, 0),
    ("a_plus_3b", "8d047601f8c3", "a + 3u * b", 1, 3, 0),
    ("a_plus_4b", "8d04b7c3", "a + 4u * b", 1, 4, 0),
    ("a_plus_5b", "8d04b601f8c3", "a + 5u * b", 1, 5, 0),
    ("a_plus_8b", "8d04f7c3", "a + 8u * b", 1, 8, 0),
    ("two_a_plus_b", "8d047ec3", "2u * a + b", 2, 1, 0),
    ("a_plus_b_plus_7", "8d043783c007c3", "a + b + 0x07u", 1, 1, 7),
    ("a_plus_4b_plus_7", "8d04b783c007c3", "a + 4u * b + 0x07u", 1, 4, 7),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, expression, coeff_a, coeff_b, immediate in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, expression, coeff_a, coeff_b, immediate, target_format))

for index, (name, hex_bytes, expression, coeff_a, coeff_b, immediate, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x5D0000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == "x86-64-two-args-affine-lea-cdecl", sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert sourcegen_candidate["generator"]["coeffA"] == coeff_a, sourcegen_candidate
    assert sourcegen_candidate["generator"]["coeffB"] == coeff_b, sourcegen_candidate
    assert sourcegen_candidate["generator"]["immediate"] == immediate, sourcegen_candidate
    assert "unsigned int a, unsigned int b" in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert expression in sourcegen_candidate["source"], sourcegen_candidate["source"]

    synthesis_candidates = generate({**task, "entry": hex(address), "bytes": hex_bytes}, 16)
    assert any(candidate.rule == "x86-64-two-args-affine-lea-cdecl" for candidate in synthesis_candidates), synthesis_candidates

    bytes_path = tmp / f"{name}.target.bin"
    bytes_path.write_bytes(data)
    tasks.append(
        {
            "schema": "mizuchi.source-task.v1",
            "name": name,
            "entry": hex(address),
            "address": address,
            "targetFormat": target_format,
            "architectureHint": "x86_64",
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
  --limit 16 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 16 and .attemptedCandidates == 16 and .semanticCodeSliceMatchedCandidates == 16 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 16 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 16' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 16 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 16 and
  ([.[] | select(.rule == "x86-64-two-args-affine-lea-cdecl")] | length) == 16 and
  ([.[] | select(.generationEvidence.coeffB == 2)] | length) == 2 and
  ([.[] | select(.generationEvidence.coeffB == 3)] | length) == 2 and
  ([.[] | select(.generationEvidence.coeffB == 4)] | length) == 4 and
  ([.[] | select(.generationEvidence.coeffB == 5)] | length) == 2 and
  ([.[] | select(.generationEvidence.coeffB == 8)] | length) == 2 and
  ([.[] | select(.generationEvidence.coeffA == 2)] | length) == 2 and
  ([.[] | select(.generationEvidence.immediate == 7)] | length) == 4
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
