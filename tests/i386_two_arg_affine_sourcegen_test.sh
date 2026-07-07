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
patterns = [
    ("b_scale2_cdecl", "8b44240801c003442404c3", "two-stack-args-affine-cdecl", "b", 2, "0x00000000", "a + b * 2u"),
    ("b_scale4_cdecl", "8b442408c1e00203442404c3", "two-stack-args-affine-cdecl", "b", 4, "0x00000000", "a + b * 4u"),
    ("b_scale8_cdecl", "8b442408c1e00303442404c3", "two-stack-args-affine-cdecl", "b", 8, "0x00000000", "a + b * 8u"),
    ("a_scale2_cdecl", "8b44240401c003442408c3", "two-stack-args-affine-cdecl", "a", 2, "0x00000000", "a * 2u + b"),
    ("b_scale2_imm7_cdecl", "8b4424088b4c24048d044183c007c3", "two-stack-args-affine-cdecl", "b", 2, "0x00000007", "a + b * 2u + 0x07u"),
    ("b_scale4_imm7_cdecl", "8b4424088b4c24048d048183c007c3", "two-stack-args-affine-cdecl", "b", 4, "0x00000007", "a + b * 4u + 0x07u"),
    ("b_scale8_imm7_cdecl", "8b4424088b4c24048d04c183c007c3", "two-stack-args-affine-cdecl", "b", 8, "0x00000007", "a + b * 8u + 0x07u"),
    ("b_scale2_imm255_cdecl", "8b4424088b4c24048d044105ff000000c3", "two-stack-args-affine-cdecl", "b", 2, "0x000000ff", "a + b * 2u + 0xffu"),
    ("b_scale2_stdcall", "8b44240801c003442404c20800", "two-stack-args-affine-stdcall", "b", 2, "0x00000000", "a + b * 2u"),
    ("b_scale4_stdcall", "8b442408c1e00203442404c20800", "two-stack-args-affine-stdcall", "b", 4, "0x00000000", "a + b * 4u"),
    ("b_scale8_imm7_stdcall", "8b4424088b4c24048d04c183c007c20800", "two-stack-args-affine-stdcall", "b", 8, "0x00000007", "a + b * 8u + 0x07u"),
    ("a_scale2_imm7_stdcall", "8b4424048b4c24088d044183c007c20800", "two-stack-args-affine-stdcall", "a", 2, "0x00000007", "a * 2u + b + 0x07u"),
]

tasks = []
for index, (name, hex_bytes, rule, scaled_arg, scale, immediate, expression) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x412000 + index * 0x10
    task = {"name": name, "address": address, "architectureHint": "i386"}
    sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
    assert sourcegen_candidate is not None, name
    assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
    assert sourcegen_candidate["language"] == "c", sourcegen_candidate
    assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
    assert sourcegen_candidate["generator"]["scaledArg"] == scaled_arg, sourcegen_candidate
    assert sourcegen_candidate["generator"]["scale"] == scale, sourcegen_candidate
    assert sourcegen_candidate["generator"]["immediate"] == immediate, sourcegen_candidate
    assert "unsigned int a, unsigned int b" in sourcegen_candidate["source"], sourcegen_candidate["source"]
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
  --limit 12 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 12 and .attemptedCandidates == 12 and .semanticCodeSliceMatchedCandidates == 12 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 12 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 12' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 12 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 12 and
  ([.[] | select(.rule == "two-stack-args-affine-cdecl")] | length) == 8 and
  ([.[] | select(.rule == "two-stack-args-affine-stdcall")] | length) == 4 and
  ([.[] | select(.generationEvidence.scaledArg == "a")] | length) == 2 and
  ([.[] | select(.generationEvidence.scaledArg == "b")] | length) == 10 and
  ([.[] | select(.generationEvidence.scale == 2)] | length) == 6 and
  ([.[] | select(.generationEvidence.scale == 4)] | length) == 3 and
  ([.[] | select(.generationEvidence.scale == 8)] | length) == 3 and
  ([.[] | select(.generationEvidence.immediate == "0x00000000")] | length) == 6 and
  ([.[] | select(.generationEvidence.immediate == "0x00000007")] | length) == 5 and
  ([.[] | select(.generationEvidence.immediate == "0x000000ff")] | length) == 1
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
