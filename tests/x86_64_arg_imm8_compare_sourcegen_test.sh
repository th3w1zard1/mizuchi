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
    ("uint_eq", "31c083ff050f94c0c3", "x86-64-uint-eq-imm8-cdecl", "unsigned int value", ("0x00000005u",)),
    ("uint_ne", "31c083ff050f95c0c3", "x86-64-uint-ne-imm8-cdecl", "unsigned int value", ("0x00000005u",)),
    ("uint_lt", "31c083ff050f92c0c3", "x86-64-uint-lt-imm8-cdecl", "unsigned int value", ("0x00000005u",)),
    ("uint_ge", "31c083ff050f93c0c3", "x86-64-uint-ge-imm8-cdecl", "unsigned int value", ("0x00000005u",)),
    ("uint_le", "31c083ff050f96c0c3", "x86-64-uint-le-imm8-cdecl", "unsigned int value", ("< 0x00000006u", "<= 0x00000005u")),
    ("uint_gt", "31c083ff050f97c0c3", "x86-64-uint-gt-imm8-cdecl", "unsigned int value", (">= 0x00000006u", "> 0x00000005u")),
    ("int_lt", "31c083ff050f9cc0c3", "x86-64-int-lt-imm8-cdecl", "int value", ("(5)",)),
    ("int_ge", "31c083ff050f9dc0c3", "x86-64-int-ge-imm8-cdecl", "int value", ("(5)",)),
    ("int_le", "31c083ff050f9ec0c3", "x86-64-int-le-imm8-cdecl", "int value", ("< (6)", "<= (5)")),
    ("int_gt", "31c083ff050f9fc0c3", "x86-64-int-gt-imm8-cdecl", "int value", (">= (6)", "> (5)")),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, signature_fragment, expression in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, signature_fragment, expression, target_format))

for index, (name, hex_bytes, rule, signature_fragment, expression, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x540000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format
    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert "unsigned int value" in sourcegen_candidate["source"] or "int value" in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert signature_fragment in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert any(candidate in sourcegen_candidate["source"] for candidate in expression), sourcegen_candidate["source"]

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
  --limit 20 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 20 and .attemptedCandidates == 20 and .semanticCodeSliceMatchedCandidates == 20 and .compileFailedCandidates == 0 and .semanticMismatchedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 20 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 20' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 20 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 20 and
  ([.[] | select(.rule == "x86-64-uint-eq-imm8-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-uint-ne-imm8-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-uint-lt-imm8-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-uint-ge-imm8-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-uint-le-imm8-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-uint-gt-imm8-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-int-lt-imm8-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-int-ge-imm8-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-int-le-imm8-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-int-gt-imm8-cdecl")] | length) == 2
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
