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
    ("add", "8b442408034424040344240c", "+", "a + b + c"),
    ("xor", "8b442408334424043344240c", "^", "a ^ b ^ c"),
    ("and", "8b442408234424042344240c", "&", "a & b & c"),
    ("or", "8b4424080b4424040b44240c", "|", "a | b | c"),
]

patterns = []
for suffix, core_hex, operator, expression in base_patterns:
    patterns.append((f"{suffix}_cdecl", f"{core_hex}c3", f"three-stack-args-{suffix}-cdecl", operator, expression))
    patterns.append((f"{suffix}_stdcall", f"{core_hex}c20c00", f"three-stack-args-{suffix}-stdcall", operator, expression))

tasks = []
for index, (name, hex_bytes, rule, operator, expression) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x414000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "i386"}
    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert sourcegen_candidate["generator"]["operator"] == operator, sourcegen_candidate
    assert sourcegen_candidate["generator"]["operandOrder"] == "stack8-then-stack4-then-stack12", sourcegen_candidate
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
  ([.[] | select(.rule == "three-stack-args-add-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "three-stack-args-xor-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "three-stack-args-and-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "three-stack-args-or-cdecl")] | length) == 1 and
  ([.[] | select(.rule == "three-stack-args-add-stdcall")] | length) == 1 and
  ([.[] | select(.rule == "three-stack-args-xor-stdcall")] | length) == 1 and
  ([.[] | select(.rule == "three-stack-args-and-stdcall")] | length) == 1 and
  ([.[] | select(.rule == "three-stack-args-or-stdcall")] | length) == 1 and
  ([.[] | select(.generationEvidence.stackBytes == 12)] | length) == 4
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
