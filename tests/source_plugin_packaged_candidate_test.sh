#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

if ! command -v objdiff >/dev/null 2>&1; then
  echo "skip: objdiff not installed"
  exit 0
fi

cat >"$TMP_DIR/recovered.c" <<'C'
unsigned int recovered(void) {
    return 0x12345678u;
}
C

printf '\xb8\x78\x56\x34\x12\xc3' >"$TMP_DIR/recovered.target.bin"

TASKS="$TMP_DIR/tasks.jsonl"
TMP_DIR="$TMP_DIR" TASKS="$TASKS" python3 - <<'PY'
import json
import os
from pathlib import Path

tmp = Path(os.environ["TMP_DIR"])
task = {
    "schema": "mizuchi.source-generation-task.v1",
    "status": "generated-unverified",
    "name": "recovered",
    "entry": "0x1000",
    "address": 0x1000,
    "source": str(tmp / "recovered.c"),
    "sourceLanguage": "c",
    "sourceQuality": "high-level-c",
    "sourceRecoveryScope": "whole-function",
    "semanticSource": True,
    "sourceOrigin": "test packaged source candidate",
    "automaticGenerator": {
        "rule": "packaged-source",
        "sourceTier": "external decompiler high-level C",
    },
    "compilerProfileHints": {
        "compiler": "clang",
        "args": [
            "-m32",
            "-O2",
            "-ffreestanding",
            "-fno-pic",
            "-fno-pie",
            "-fno-asynchronous-unwind-tables",
            "-fno-stack-protector",
            "-fno-ident",
        ],
        "reason": "fixture target bytes were compiled with this profile",
    },
    "targetSlice": {
        "status": "complete",
        "section": ".text",
        "bytesPath": str(tmp / "recovered.target.bin"),
        "boundaryQuality": {"status": "complete"},
    },
}
Path(os.environ["TASKS"]).write_text(json.dumps(task, sort_keys=True) + "\n", encoding="utf-8")
PY

PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" TASKS="$TASKS" OUT_DIR="$TMP_DIR/out" python3 - <<'PY'
import json
import os
from pathlib import Path

from mizuchi_re.source_plugin_runner import SourcePluginRunConfig, run_source_plugin_pipeline
from mizuchi_re.source_parity_synthesize import generate
from mizuchi_re.sourcegen import generated_candidate_from_target_bytes

summary = run_source_plugin_pipeline(
    SourcePluginRunConfig(
        source_tasks=[Path(os.environ["TASKS"])],
        source_tasks_only=True,
        out_dir=Path(os.environ["OUT_DIR"]),
        limit=1,
        max_variants_per_function=1,
        max_retries=1,
        strategies={"packaged-source"},
        source_qualities={"high-level-c"},
        compiler="clang",
        clean=True,
        timeout=30,
    )
)
assert summary["inspectedFunctions"] == 1, summary
assert summary["successfulFunctions"] == 1, summary
assert summary["highLevelSourceMatches"] == 1, summary
attempts = Path(summary["attemptsPath"]).read_text(encoding="utf-8").splitlines()
assert attempts, summary
records = [json.loads(line) for line in attempts if line.strip()]
assert any(
    row["rule"] == "packaged-source"
    and row["status"] == "code-slice-matched"
    and row["differences"] == 0
    and row["sourceQuality"] == "high-level-c"
    for row in records
), records

framed_bytes = bytes.fromhex("5589e5b8785634125dc3")
sourcegen_candidate = generated_candidate_from_target_bytes({"name": "framed", "address": 0x2000}, framed_bytes)
assert sourcegen_candidate is not None, "sourcegen should lift framed immediate return"
assert sourcegen_candidate["generator"]["rule"] == "framed-return-immediate-cdecl", sourcegen_candidate
assert sourcegen_candidate["compilerProfileHints"]["compiler"] == "clang", sourcegen_candidate

synthesis_candidates = generate({"name": "framed", "entry": "0x2000", "bytes": framed_bytes.hex()}, 8)
assert any(candidate.rule == "framed-return-immediate-cdecl" for candidate in synthesis_candidates), synthesis_candidates

framed_source = Path(os.environ["OUT_DIR"]).parent / "framed.c"
framed_source.write_text(sourcegen_candidate["source"], encoding="utf-8")
framed_bytes_path = Path(os.environ["OUT_DIR"]).parent / "framed.target.bin"
framed_bytes_path.write_bytes(framed_bytes)
framed_tasks = Path(os.environ["OUT_DIR"]).parent / "framed.tasks.jsonl"
framed_task = {
    "schema": "mizuchi.source-generation-task.v1",
    "status": "generated-unverified",
    "name": "framed",
    "entry": "0x2000",
    "address": 0x2000,
    "source": str(framed_source),
    "sourceLanguage": "c",
    "sourceQuality": "high-level-c",
    "sourceRecoveryScope": "whole-function",
    "semanticSource": True,
    "sourceOrigin": sourcegen_candidate["origin"],
    "automaticGenerator": sourcegen_candidate["generator"],
    "compilerProfileHints": sourcegen_candidate["compilerProfileHints"],
    "targetSlice": {
        "status": "complete",
        "section": ".text",
        "bytesPath": str(framed_bytes_path),
        "boundaryQuality": {"status": "complete"},
    },
}
framed_tasks.write_text(json.dumps(framed_task, sort_keys=True) + "\n", encoding="utf-8")
framed_summary = run_source_plugin_pipeline(
    SourcePluginRunConfig(
        source_tasks=[framed_tasks],
        source_tasks_only=True,
        out_dir=Path(os.environ["OUT_DIR"]).parent / "framed-out",
        limit=1,
        max_variants_per_function=1,
        max_retries=1,
        strategies={"framed-return-immediate-cdecl"},
        source_qualities={"high-level-c"},
        compiler="clang",
        clean=True,
        timeout=30,
    )
)
assert framed_summary["inspectedFunctions"] == 1, framed_summary
assert framed_summary["successfulFunctions"] == 1, framed_summary
assert framed_summary["highLevelSourceMatches"] == 1, framed_summary

framed_arg_bytes = bytes.fromhex("5589e58b45085dc3")
framed_arg_candidate = generated_candidate_from_target_bytes({"name": "framed_arg", "address": 0x2100}, framed_arg_bytes)
assert framed_arg_candidate is not None, "sourcegen should lift framed stack-argument return"
assert framed_arg_candidate["generator"]["rule"] == "framed-return-first-stack-arg-cdecl", framed_arg_candidate
assert framed_arg_candidate["compilerProfileHints"]["compiler"] == "clang", framed_arg_candidate
assert "__attribute__((naked))" in framed_arg_candidate["source"], framed_arg_candidate["source"]

framed_arg_synthesis = generate({"name": "framed_arg", "entry": "0x2100", "bytes": framed_arg_bytes.hex()}, 8)
assert any(candidate.rule == "framed-return-first-stack-arg-cdecl" for candidate in framed_arg_synthesis), framed_arg_synthesis

framed_arg_source = Path(os.environ["OUT_DIR"]).parent / "framed_arg.c"
framed_arg_source.write_text(framed_arg_candidate["source"], encoding="utf-8")
framed_arg_bytes_path = Path(os.environ["OUT_DIR"]).parent / "framed_arg.target.bin"
framed_arg_bytes_path.write_bytes(framed_arg_bytes)
framed_arg_tasks = Path(os.environ["OUT_DIR"]).parent / "framed_arg.tasks.jsonl"
framed_arg_task = {
    "schema": "mizuchi.source-generation-task.v1",
    "status": "generated-unverified",
    "name": "framed_arg",
    "entry": "0x2100",
    "address": 0x2100,
    "source": str(framed_arg_source),
    "sourceLanguage": "c",
    "sourceQuality": "inline-asm-c",
    "sourceRecoveryScope": "whole-function",
    "semanticSource": True,
    "sourceOrigin": framed_arg_candidate["origin"],
    "automaticGenerator": framed_arg_candidate["generator"],
    "compilerProfileHints": framed_arg_candidate["compilerProfileHints"],
    "targetSlice": {
        "status": "complete",
        "section": ".text",
        "bytesPath": str(framed_arg_bytes_path),
        "boundaryQuality": {"status": "complete"},
    },
}
framed_arg_tasks.write_text(json.dumps(framed_arg_task, sort_keys=True) + "\n", encoding="utf-8")
framed_arg_summary = run_source_plugin_pipeline(
    SourcePluginRunConfig(
        source_tasks=[framed_arg_tasks],
        source_tasks_only=True,
        out_dir=Path(os.environ["OUT_DIR"]).parent / "framed-arg-out",
        limit=1,
        max_variants_per_function=1,
        max_retries=1,
        strategies={"framed-return-first-stack-arg-cdecl"},
        source_qualities={"inline-asm-c"},
        compiler="clang",
        clean=True,
        timeout=30,
    )
)
assert framed_arg_summary["inspectedFunctions"] == 1, framed_arg_summary
assert framed_arg_summary["successfulFunctions"] == 1, framed_arg_summary
assert framed_arg_summary["inlineAsmSourceMatches"] == 1, framed_arg_summary
print("ok")
PY
