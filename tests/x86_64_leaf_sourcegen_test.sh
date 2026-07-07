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

from mizuchi_re.sourcegen import generated_candidate_from_target_bytes
from mizuchi_re.source_parity_synthesize import generate

tmp = Path(sys.argv[1])
patterns = [
    ("leaf_zero", "31c0c3", "x86-64-return-zero-cdecl", None),
    ("leaf_one", "b801000000c3", "x86-64-return-one-cdecl", None),
    ("leaf_imm", "b878563412c3", "x86-64-return-immediate-cdecl", None),
    ("leaf_arg", "89f8c3", "x86-64-return-first-arg-cdecl", None),
    ("leaf_add", "8d0437c3", "x86-64-add-two-args-cdecl", None),
    ("leaf_nonzero", "31c085ff0f95c0c3", "x86-64-arg-nonzero-bool-cdecl", None),
    ("leaf_is_zero", "31c085ff0f94c0c3", "x86-64-arg-zero-bool-cdecl", None),
    ("macho_zero", "31c0c3", "x86-64-return-zero-cdecl", "macho"),
    ("macho_one", "b801000000c3", "x86-64-return-one-cdecl", "macho"),
    ("macho_imm", "b878563412c3", "x86-64-return-immediate-cdecl", "macho"),
    ("macho_nonzero", "31c085ff0f95c0c3", "x86-64-arg-nonzero-bool-cdecl", "macho"),
    ("macho_is_zero", "31c085ff0f94c0c3", "x86-64-arg-zero-bool-cdecl", "macho"),
    ("macho_arg", "554889e589f85dc3", "x86-64-framed-return-first-arg-cdecl", "macho"),
    ("macho_add", "554889e58d04375dc3", "x86-64-framed-add-two-args-cdecl", "macho"),
]
tasks = []
for index, (name, hex_bytes, rule, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    task = {"name": name, "address": 0x1000 + index * 0x10, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format
    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    synthesis_candidates = generate({**task, "entry": hex(task["address"]), "bytes": hex_bytes}, 8)
    assert any(candidate.rule == rule for candidate in synthesis_candidates), (rule, synthesis_candidates)

    bytes_path = tmp / f"{name}.target.bin"
    bytes_path.write_bytes(data)
    tasks.append(
        {
            "schema": "mizuchi.source-task.v1",
            "name": name,
            "entry": hex(task["address"]),
            "address": task["address"],
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
  --limit 14 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 14 and .attemptedCandidates == 14 and .semanticCodeSliceMatchedCandidates == 14 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 14 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 14' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 14 and
  ([.[] | select(.rule == "x86-64-return-zero-cdecl" and .status == "code-slice-matched" and .differences == 0)] | length) == 2 and
  ([.[] | select(.rule == "x86-64-return-one-cdecl" and .status == "code-slice-matched" and .differences == 0)] | length) == 2 and
  ([.[] | select(.rule == "x86-64-return-immediate-cdecl" and .status == "code-slice-matched" and .differences == 0)] | length) == 2 and
  ([.[] | select(.rule == "x86-64-arg-nonzero-bool-cdecl" and .status == "code-slice-matched" and .differences == 0)] | length) == 2 and
  ([.[] | select(.rule == "x86-64-arg-zero-bool-cdecl" and .status == "code-slice-matched" and .differences == 0)] | length) == 2 and
  ([.[] | select(.rule == "x86-64-return-first-arg-cdecl" and .status == "code-slice-matched" and .differences == 0)] | length) == 1 and
  ([.[] | select(.rule == "x86-64-add-two-args-cdecl" and .status == "code-slice-matched" and .differences == 0)] | length) == 1 and
  ([.[] | select(.rule == "x86-64-framed-return-first-arg-cdecl" and .status == "code-slice-matched" and .differences == 0)] | length) == 1 and
  ([.[] | select(.rule == "x86-64-framed-add-two-args-cdecl" and .status == "code-slice-matched" and .differences == 0)] | length) == 1
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
