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
    ("x64_neg", "89f8f7d8c3", "x86-64-arg-neg-cdecl", "x86_64", None, "-value"),
    ("x64_not", "89f8f7d0c3", "x86-64-arg-not-cdecl", "x86_64", None, "~value"),
    ("macho_neg", "89f8f7d8c3", "x86-64-arg-neg-cdecl", "x86_64", "macho", "-value"),
    ("macho_not", "89f8f7d0c3", "x86-64-arg-not-cdecl", "x86_64", "macho", "~value"),
    ("i386_neg", "31c02b442404c3", "stack-arg-neg-cdecl", "i386", None, "-value"),
    ("i386_not", "8b442404f7d0c3", "stack-arg-not-cdecl", "i386", None, "~value"),
    ("i386_neg_stdcall", "31c02b442404c20400", "stack-arg-neg-stdcall", "i386", None, "-value"),
    ("i386_not_stdcall", "8b442404f7d0c20400", "stack-arg-not-stdcall", "i386", None, "~value"),
]

tasks = []
for index, (name, hex_bytes, rule, arch, target_format, expression) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x540000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": arch}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert "unsigned int value" in sourcegen_candidate["source"], sourcegen_candidate["source"]
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
            "targetFormat": target_format,
            "architectureHint": arch,
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
  ([.[] | select(.rule == "x86-64-arg-neg-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-arg-not-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "stack-arg-neg-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "stack-arg-not-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "stack-arg-neg-stdcall")] | length) == 1 and
  ([.[] | select(.rule == "stack-arg-not-stdcall")] | length) == 1
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
