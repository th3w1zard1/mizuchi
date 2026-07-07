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
    ("sub", "89f829f0c3", "x86-64-two-args-sub-cdecl", "unsigned int a, unsigned int b", "a - b"),
    ("mul", "89f80fafc6c3", "x86-64-two-args-mul-cdecl", "unsigned int a, unsigned int b", "a * b"),
    ("and", "89f821f0c3", "x86-64-two-args-and-cdecl", "unsigned int a, unsigned int b", "a & b"),
    ("or", "89f809f0c3", "x86-64-two-args-or-cdecl", "unsigned int a, unsigned int b", "a | b"),
    ("xor", "89f831f0c3", "x86-64-two-args-xor-cdecl", "unsigned int a, unsigned int b", "a ^ b"),
    ("udiv2", "89f8d1e8c3", "x86-64-arg-udiv-pow2-cdecl", "unsigned int value", "value / 2u"),
    ("sar1", "89f8d1f8c3", "x86-64-arg-sar-imm8-cdecl", "int value", "value >> 1"),
    ("udiv8", "89f8c1e803c3", "x86-64-arg-udiv-pow2-cdecl", "unsigned int value", "value / 8u"),
    ("sar", "89f8c1f803c3", "x86-64-arg-sar-imm8-cdecl", "int value", "value >> 3"),
]

tasks = []
patterns = []
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for name, hex_bytes, rule, signature_fragment, expression in base_patterns:
        patterns.append((f"{prefix}_{name}", hex_bytes, rule, signature_fragment, expression, target_format))

for index, (name, hex_bytes, rule, signature_fragment, expression, target_format) in enumerate(patterns):
    data = bytes.fromhex(hex_bytes)
    address = 0x500000 + index * 0x10
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
  --limit 18 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 18 and .attemptedCandidates == 18 and .semanticCodeSliceMatchedCandidates == 18 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 18 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 18' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 18 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 18 and
  ([.[] | select(.rule == "x86-64-two-args-sub-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-two-args-mul-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-two-args-and-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-two-args-or-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-two-args-xor-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-arg-udiv-pow2-cdecl")] | length) == 4 and
  ([.[] | select(.rule == "x86-64-arg-sar-imm8-cdecl")] | length) == 4 and
  ([.[] | select(.generationEvidence.shift == 1)] | length) == 4 and
  ([.[] | select(.generationEvidence.pattern == "mov-eax-edi-shift-one-ret")] | length) == 2 and
  ([.[] | select(.generationEvidence.shift == 3)] | length) == 4
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
