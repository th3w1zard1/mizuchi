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
setcc = {
    "lt": "9c",
    "le": "9e",
    "gt": "9f",
    "ge": "9d",
}

tasks = []
for index, (suffix, opcode) in enumerate(setcc.items()):
    for convention, ret in (("cdecl", "c3"), ("stdcall", "c20800")):
        name = f"{suffix}_{convention}"
        rule = f"two-stack-args-int-{suffix}-{convention}"
        hex_bytes = f"8b4c240431c03b4c24080f{opcode}c0{ret}"
        data = bytes.fromhex(hex_bytes)
        address = 0x404000 + len(tasks) * 0x10
        task = {"name": name, "address": address, "architectureHint": "i386"}
        sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
        assert sourcegen_candidate is not None, name
        assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
        assert sourcegen_candidate["language"] == "c", sourcegen_candidate
        assert f"return a " in sourcegen_candidate["source"], sourcegen_candidate["source"]
        assert "int a, int b" in sourcegen_candidate["source"], sourcegen_candidate["source"]

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
  ([.[] | select(.rule | startswith("two-stack-args-int-"))] | length) == 8 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 8 and
  ([.[] | select(.rule | endswith("-cdecl"))] | length) == 4 and
  ([.[] | select(.rule | endswith("-stdcall"))] | length) == 4
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
