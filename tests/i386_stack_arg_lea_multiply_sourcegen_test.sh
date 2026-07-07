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
    ("mul_2", "8b44240401c0", "value * 2u"),
    ("mul_3", "8b4424048d0440", "value * 3u"),
    ("mul_5", "8b4424048d0480", "value * 5u"),
    ("mul_6", "8b44240401c08d0440", "value * 6u"),
    ("mul_7", "8b4c24048d04cd0000000029c8", "value * 7u"),
    ("mul_9", "8b4424048d04c0", "value * 9u"),
    ("mul_10", "8b44240401c08d0480", "value * 10u"),
    ("mul_11", "8b4424048d0c808d0448", "value * 11u"),
    ("mul_12", "8b442404c1e0028d0440", "value * 12u"),
    ("mul_13", "8b4424048d0c408d0488", "value * 13u"),
    ("mul_14", "8b4424048d0c00c1e00429c8", "value * 14u"),
    ("mul_15", "8b4424048d04808d0440", "value * 15u"),
    ("mul_24", "8b442404c1e0038d0440", "value * 24u"),
    ("mul_31", "8b4c240489c8c1e00529c8", "value * 31u"),
    ("mul_33", "8b4c240489c8c1e00501c8", "value * 33u"),
]

patterns = []
for name, core_hex, expression in base_patterns:
    patterns.append((f"{name}_cdecl", f"{core_hex}c3", "stack-arg-mul-lea-cdecl", expression))
    patterns.append((f"{name}_stdcall", f"{core_hex}c20400", "stack-arg-mul-lea-stdcall", expression))

tasks = []
for index, (name, hex_bytes, rule, expression) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x40D000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "i386"}
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
  --limit 30 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 30 and .attemptedCandidates == 30 and .semanticCodeSliceMatchedCandidates == 30 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 30 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 30' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 30 and
  ([.[] | select(.rule == "stack-arg-mul-lea-cdecl" and .status == "code-slice-matched" and .differences == 0)] | length) == 15 and
  ([.[] | select(.rule == "stack-arg-mul-lea-stdcall" and .status == "code-slice-matched" and .differences == 0)] | length) == 15 and
  ([.[] | select(.generationEvidence.multiplier == 2)] | length) == 2 and
  ([.[] | select(.generationEvidence.multiplier == 33)] | length) == 2
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
