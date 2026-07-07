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
    ("add_128", "8d8780000000c3", "x86-64-arg-add-imm32-cdecl", "value + 0x00000080u"),
    ("add_1024", "8d8700040000c3", "x86-64-arg-add-imm32-cdecl", "value + 0x00000400u"),
    ("sub_129", "8d877fffffffc3", "x86-64-arg-sub-imm32-cdecl", "value - 0x00000081u"),
    ("mul_2", "8d043fc3", "x86-64-arg-mul-lea-cdecl", "value * 2u"),
    ("mul_3", "8d047fc3", "x86-64-arg-mul-lea-cdecl", "value * 3u"),
    ("mul_4", "8d04bd00000000c3", "x86-64-arg-mul-lea-cdecl", "value * 4u"),
    ("mul_5", "8d04bfc3", "x86-64-arg-mul-lea-cdecl", "value * 5u"),
    ("mul_6", "01ff8d047fc3", "x86-64-arg-mul-lea-cdecl", "value * 6u"),
    ("mul_7", "8d04fd0000000029f8c3", "x86-64-arg-mul-lea-cdecl", "value * 7u"),
    ("mul_8", "8d04fd00000000c3", "x86-64-arg-mul-lea-cdecl", "value * 8u"),
    ("mul_9", "8d04ffc3", "x86-64-arg-mul-lea-cdecl", "value * 9u"),
    ("mul_10", "01ff8d04bfc3", "x86-64-arg-mul-lea-cdecl", "value * 10u"),
    ("mul_11", "8d04bf8d0447c3", "x86-64-arg-mul-lea-cdecl", "value * 11u"),
    ("mul_12", "c1e7028d047fc3", "x86-64-arg-mul-lea-cdecl", "value * 12u"),
    ("mul_13", "8d047f8d0487c3", "x86-64-arg-mul-lea-cdecl", "value * 13u"),
    ("mul_14", "89f88d0c00c1e00429c8c3", "x86-64-arg-mul-lea-cdecl", "value * 14u"),
    ("mul_15", "8d04bf8d0440c3", "x86-64-arg-mul-lea-cdecl", "value * 15u"),
    ("mul_24", "c1e7038d047fc3", "x86-64-arg-mul-lea-cdecl", "value * 24u"),
    ("mul_31", "89f8c1e00529f8c3", "x86-64-arg-mul-lea-cdecl", "value * 31u"),
    ("mul_33", "89f8c1e00501f8c3", "x86-64-arg-mul-lea-cdecl", "value * 33u"),
    ("shl_4", "89f8c1e004c3", "x86-64-arg-shl-imm8-cdecl", "value << 4"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, expression in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, expression, target_format))

for index, (name, hex_bytes, rule, expression, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x560000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert "unsigned int value" in sourcegen_candidate["source"], sourcegen_candidate["source"]
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
  --limit 42 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 42 and .attemptedCandidates == 42 and .semanticCodeSliceMatchedCandidates == 42 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 42 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 42' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 42 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 42 and
  ([.[] | select(.rule == "x86-64-arg-add-imm32-cdecl")] | length) == 4 and
  ([.[] | select(.rule == "x86-64-arg-sub-imm32-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-arg-mul-lea-cdecl")] | length) == 34 and
  ([.[] | select(.rule == "x86-64-arg-shl-imm8-cdecl")] | length) == 2
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
