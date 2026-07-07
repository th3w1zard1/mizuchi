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
    ("eq_u", "31c039f70f94c0c3", "x86-64-uint-eq-two-args-cdecl", "unsigned int a, unsigned int b", "a == b"),
    ("ne_u", "31c039f70f95c0c3", "x86-64-uint-ne-two-args-cdecl", "unsigned int a, unsigned int b", "a != b"),
    ("lt_u", "31c039f70f92c0c3", "x86-64-uint-lt-two-args-cdecl", "unsigned int a, unsigned int b", "a < b"),
    ("ge_u", "31c039f70f93c0c3", "x86-64-uint-ge-two-args-cdecl", "unsigned int a, unsigned int b", "a >= b"),
    ("gt_u", "31c039f70f97c0c3", "x86-64-uint-gt-two-args-cdecl", "unsigned int a, unsigned int b", "a > b"),
    ("le_u", "31c039f70f96c0c3", "x86-64-uint-le-two-args-cdecl", "unsigned int a, unsigned int b", "a <= b"),
    ("lt_s", "31c039f70f9cc0c3", "x86-64-int-lt-two-args-cdecl", "int a, int b", "a < b"),
    ("ge_s", "31c039f70f9dc0c3", "x86-64-int-ge-two-args-cdecl", "int a, int b", "a >= b"),
    ("gt_s", "31c039f70f9fc0c3", "x86-64-int-gt-two-args-cdecl", "int a, int b", "a > b"),
    ("le_s", "31c039f70f9ec0c3", "x86-64-int-le-two-args-cdecl", "int a, int b", "a <= b"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, signature_fragment, expression in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, signature_fragment, expression, target_format))

for index, (name, hex_bytes, rule, signature_fragment, expression, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x520000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format
    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert signature_fragment in sourcegen_candidate["source"], sourcegen_candidate["source"]
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
  --limit 20 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 20 and .attemptedCandidates == 20 and .semanticCodeSliceMatchedCandidates == 20 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 20 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 20' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 20 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 20 and
  ([.[] | select(.rule == "x86-64-uint-eq-two-args-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-uint-ne-two-args-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-uint-lt-two-args-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-uint-ge-two-args-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-uint-gt-two-args-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-uint-le-two-args-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-int-lt-two-args-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-int-ge-two-args-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-int-gt-two-args-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-int-le-two-args-cdecl")] | length) == 2
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
