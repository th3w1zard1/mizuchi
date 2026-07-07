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
base_patterns = [
    ("arg64_nonzero", "31c04885ff0f95c0c3", "x86-64-arg64-nonzero-bool-cdecl", "!=", "setne", "value != 0"),
    ("arg64_zero", "31c04885ff0f94c0c3", "x86-64-arg64-zero-bool-cdecl", "==", "sete", "value == 0"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, operator, setcc, expression in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, operator, setcc, expression, target_format))

for index, (name, hex_bytes, rule, operator, setcc, expression, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x6F0000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["generator"]["registerArg"] == "rdi", sourcegen_candidate
    assert sourcegen_candidate["generator"]["operator"] == operator, sourcegen_candidate
    assert sourcegen_candidate["generator"]["setcc"] == setcc, sourcegen_candidate
    assert sourcegen_candidate["generator"]["predicate"] == expression, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert "unsigned long long value" in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert expression in sourcegen_candidate["source"], sourcegen_candidate["source"]

    synthesis_candidates = generate({**task, "entry": hex(address), "bytes": hex_bytes}, 16)
    assert synthesis_candidates, name
    assert synthesis_candidates[0].rule == rule, synthesis_candidates

    bytes_path = tmp / f"{name}.target.bin"
    bytes_path.write_bytes(data)
    tasks.append(
        {
            "schema": "reconkit.source-task.v1",
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

PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m reconkit_re.source_parity_synthesize \
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
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 4 and
  ([.[] | select(.rule == "x86-64-arg64-nonzero-bool-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-arg64-zero-bool-cdecl")] | length) == 2 and
  ([.[] | select(.generationEvidence.registerArg == "rdi")] | length) == 4 and
  ([.[] | select(.generationEvidence.setcc == "setne")] | length) == 2 and
  ([.[] | select(.generationEvidence.setcc == "sete")] | length) == 2
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
