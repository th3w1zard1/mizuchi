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
    ("srem2", "89f889f9c1e91f01f983e1fe29c8c3", 2, 1, 1, "0xfffffffe", "value % 2"),
    ("srem4", "89f88d480385ff0f49cf83e1fc29c8c3", 4, 2, 3, "0xfffffffc", "value % 4"),
    ("srem8", "89f88d480785ff0f49cf83e1f829c8c3", 8, 3, 7, "0xfffffff8", "value % 8"),
    ("srem16", "89f88d480f85ff0f49cf83e1f029c8c3", 16, 4, 15, "0xfffffff0", "value % 16"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, divisor, shift, bias, mask, expression in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, divisor, shift, bias, mask, expression, target_format))

for index, (name, hex_bytes, divisor, shift, bias, mask, expression, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x7C0000 + index * 0x20
    task = {"name": name, "address": address, "architectureHint": "x86_64"}
    if target_format:
        task["targetFormat"] = target_format

    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == "x86-64-arg-srem-pow2-cdecl", sourcegen_candidate
    assert sourcegen_candidate["generator"]["divisor"] == divisor, sourcegen_candidate
    assert sourcegen_candidate["generator"]["shift"] == shift, sourcegen_candidate
    assert sourcegen_candidate["generator"]["bias"] == bias, sourcegen_candidate
    assert sourcegen_candidate["generator"]["mask"] == mask, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert "int value" in sourcegen_candidate["source"], sourcegen_candidate["source"]
    assert expression in sourcegen_candidate["source"], sourcegen_candidate["source"]

    synthesis_candidates = generate({**task, "entry": hex(address), "bytes": hex_bytes}, 16)
    assert synthesis_candidates, name
    assert synthesis_candidates[0].rule == "x86-64-arg-srem-pow2-cdecl", synthesis_candidates

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
  --limit 8 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 8 and .attemptedCandidates == 8 and .semanticCodeSliceMatchedCandidates == 8 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 8 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 8' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 8 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 8 and
  ([.[] | select(.rule == "x86-64-arg-srem-pow2-cdecl")] | length) == 8 and
  ([.[] | select(.generationEvidence.divisor == 2)] | length) == 2 and
  ([.[] | select(.generationEvidence.divisor == 4)] | length) == 2 and
  ([.[] | select(.generationEvidence.divisor == 8)] | length) == 2 and
  ([.[] | select(.generationEvidence.divisor == 16)] | length) == 2
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
