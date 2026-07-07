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
    ("uint_min", "8b4424088b4c240439c10f42c1", "uint-min", "unsigned int", "cmovb", "a < b ? a : b"),
    ("uint_max", "8b4424088b4c240439c10f47c1", "uint-max", "unsigned int", "cmova", "a > b ? a : b"),
    ("int_min", "8b4424088b4c240439c10f4cc1", "int-min", "int", "cmovl", "a < b ? a : b"),
    ("int_max", "8b4424088b4c240439c10f4fc1", "int-max", "int", "cmovg", "a > b ? a : b"),
]

patterns = []
for name, core_hex, suffix, value_type, cmov, expression in base_patterns:
    patterns.append((f"{name}_cdecl", f"{core_hex}c3", f"two-stack-args-{suffix}-cmov-cdecl", value_type, cmov, expression))
    patterns.append((f"{name}_stdcall", f"{core_hex}c20800", f"two-stack-args-{suffix}-cmov-stdcall", value_type, cmov, expression))

tasks = []
for index, (name, hex_bytes, rule, value_type, cmov, expression) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x411000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "i386"}
    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert sourcegen_candidate["generator"]["cmov"] == cmov, sourcegen_candidate
    assert sourcegen_candidate["generator"]["valueType"] == value_type, sourcegen_candidate
    assert f"{value_type} a, {value_type} b" in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert expression in sourcegen_candidate["source"], sourcegen_candidate["source"]

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
  --limit 8 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 8 and .attemptedCandidates == 8 and .semanticCodeSliceMatchedCandidates == 8 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 8 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 8' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 8 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 8 and
  ([.[] | select(.rule == "two-stack-args-uint-min-cmov-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "two-stack-args-uint-max-cmov-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "two-stack-args-int-min-cmov-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "two-stack-args-int-max-cmov-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "two-stack-args-uint-min-cmov-stdcall")] | length) == 1 and
  ([.[] | select(.rule == "two-stack-args-uint-max-cmov-stdcall")] | length) == 1 and
  ([.[] | select(.rule == "two-stack-args-int-min-cmov-stdcall")] | length) == 1 and
  ([.[] | select(.rule == "two-stack-args-int-max-cmov-stdcall")] | length) == 1 and
  ([.[] | select(.generationEvidence.cmov == "cmovb")] | length) == 2 and
  ([.[] | select(.generationEvidence.cmov == "cmova")] | length) == 2 and
  ([.[] | select(.generationEvidence.cmov == "cmovl")] | length) == 2 and
  ([.[] | select(.generationEvidence.cmov == "cmovg")] | length) == 2
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
