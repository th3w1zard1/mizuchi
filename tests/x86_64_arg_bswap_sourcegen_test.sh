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
    (
        "bswap32",
        "89f80fc8c3",
        "x86-64-arg-bswap32-cdecl",
        "bswap32",
        "mov-eax-edi-bswap-eax-ret",
        "edi",
        "unsigned int value",
        [
            "((value & 0x000000ffu) << 24)",
            "((value & 0x0000ff00u) << 8)",
            "((value >> 8) & 0x0000ff00u)",
            "(value >> 24)",
        ],
    ),
    (
        "bswap64",
        "4889f8480fc8c3",
        "x86-64-arg-bswap64-cdecl",
        "bswap64",
        "mov-rax-rdi-bswap-rax-ret",
        "rdi",
        "unsigned long long value",
        [
            "((value & 0x00000000000000ffull) << 56)",
            "((value & 0x000000000000ff00ull) << 40)",
            "((value & 0x0000000000ff0000ull) << 24)",
            "((value & 0x00000000ff000000ull) << 8)",
            "((value >> 8) & 0x00000000ff000000ull)",
            "((value >> 24) & 0x0000000000ff0000ull)",
            "((value >> 40) & 0x000000000000ff00ull)",
            "(value >> 56)",
        ],
    ),
]

tasks = []
index = 0
for target_format in [None, "macho"]:
    prefix = "macho" if target_format else "elf"
    for suffix, hex_bytes, rule, operation, pattern, register_arg, arg_decl, expression_parts in patterns:
        name = f"{prefix}_{suffix}"
        data = bytes.fromhex(hex_bytes)
        address = 0x690000 + index * 0x10
        index += 1
        task = {"name": name, "address": address, "architectureHint": "x86_64"}
        if target_format:
            task["targetFormat"] = target_format

        sourcegen_candidate = generated_candidate_from_target_bytes(task, data)
        assert sourcegen_candidate is not None, name
        assert sourcegen_candidate["generator"]["rule"] == rule, sourcegen_candidate
        assert sourcegen_candidate["generator"]["operation"] == operation, sourcegen_candidate
        assert sourcegen_candidate["generator"]["pattern"] == pattern, sourcegen_candidate
        assert sourcegen_candidate["generator"]["registerArg"] == register_arg, sourcegen_candidate
        assert sourcegen_candidate["language"] == "c", sourcegen_candidate
        assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate
        assert arg_decl in sourcegen_candidate["source"], sourcegen_candidate["source"]
        for part in expression_parts:
            assert part in sourcegen_candidate["source"], sourcegen_candidate["source"]

        synthesis_candidates = generate({**task, "entry": hex(address), "bytes": hex_bytes}, 16)
        assert synthesis_candidates, name
        assert synthesis_candidates[0].rule == rule, synthesis_candidates

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
  --limit 4 \
  --max-variants-per-function 1 \
  --timeout 30 >/dev/null

jq -e '.compiler == "clang" and .generatedCandidates == 4 and .attemptedCandidates == 4 and .semanticCodeSliceMatchedCandidates == 4 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and .generatedBySourceQuality["high-level-c"] == 4 and .semanticCodeSliceMatchedBySourceQuality["high-level-c"] == 4' "$TMP_DIR/out/summary.json" >/dev/null
jq -s -e '
  length == 4 and
  ([.[] | select(.status == "code-slice-matched" and .differences == 0)] | length) == 4 and
  ([.[] | select(.rule == "x86-64-arg-bswap32-cdecl")] | length) == 2 and
  ([.[] | select(.rule == "x86-64-arg-bswap64-cdecl")] | length) == 2 and
  ([.[] | select(.generationEvidence.operation == "bswap32")] | length) == 2 and
  ([.[] | select(.generationEvidence.operation == "bswap64")] | length) == 2 and
  ([.[] | select(.generationEvidence.pattern == "mov-eax-edi-bswap-eax-ret")] | length) == 2 and
  ([.[] | select(.generationEvidence.pattern == "mov-rax-rdi-bswap-rax-ret")] | length) == 2
' "$TMP_DIR/out/attempts.jsonl" >/dev/null

echo "ok"
