#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PYTHONPATH="$ROOT/src" python3 - "$TMP_DIR" <<'PY'
import json
import sys
from pathlib import Path

from reconkit_re.sourcegen import generate_source_candidates
from reconkit_re.source_parity_synthesize import main as synthesize_main
from reconkit_re.pipeline import RecoveryConfig, RecoveryRunner
from reconkit_re.strategy import build_strategy
from reconkit_re.targets import TargetIdentity

tmp = Path(sys.argv[1])
binary = tmp / "sample.bin"
binary.write_bytes(b"\x31\xc0\xc3\x90")

run_dir = tmp / "run"
profile_dir = run_dir / "source-parity-profile"
profile_dir.mkdir(parents=True)
(profile_dir / "summary.json").write_text(
    json.dumps(
        {
            "schema": "reconkit.source-parity-profile-corpus-summary.v1",
            "status": "complete",
            "compilerProfiles": [{"name": "vc71", "root": "/toolchains/vc71"}],
            "profileFlagMatches": {"vc71 /O2 /GS- /Oy": 3},
            "summaryJsonl": "summary.jsonl",
            "selectedCasesPath": "selected-cases.json",
        }
    )
    + "\n",
    encoding="utf-8",
)

facts_path = tmp / "facts.jsonl"
facts_path.write_text(
    json.dumps(
        {
            "name": "return_zero",
            "entry": "0x401000",
            "entryOffset": 0x401000,
            "bodyBytes": 3,
            "instructionCount": 2,
            "bytes": "33c0c3",
            "asm": "xor eax,eax\nret",
            "prototype": {"returnType": "int", "callingConvention": "cdecl", "language": "c"},
            "calls": ["puts"],
            "globals": ["gFlag"],
            "locals": [{"name": "tmp", "type": "int"}],
            "stack": {"args": 0},
            "controlFlow": {"blocks": 1},
            "objectModel": {"usesThis": False},
            "compilerHints": {"compiler": "msvc", "callingConvention": "cdecl"},
        }
    )
    + "\n",
    encoding="utf-8",
)

target = {
    "stableId": "sample",
    "binaryPath": str(binary),
    "format": "pe",
    "architectureHint": "x86",
}
inventory = {
    "target": {"binaryPath": str(binary)},
    "format": "pe",
    "status": "complete",
    "imageBase": 0x400000,
    "codeRanges": [{"name": ".text", "rva": 0x1000, "size": 4, "fileOffset": 0, "fileSize": 4}],
}
function_candidates = {
    "schema": "reconkit.function-candidates.v1",
    "candidates": [
        {
            "name": "return_zero",
            "address": 0x401000,
            "rva": 0x1000,
            "size": 3,
            "source": "objdump-label",
            "confidence": "medium",
        }
    ],
}

summary = generate_source_candidates(
    target=target,
    function_candidates=function_candidates,
    out_dir=run_dir / "source-generation",
    inventory=inventory,
    function_facts_jsonl=facts_path,
)

assert summary["status"] == "generated-unverified", summary
assert summary["generatedSourceCandidates"] == 1, summary
assert summary["targetSlices"] == 1, summary
assert summary["functionFactArtifacts"]["factCount"] == 1, summary
assert summary["compilerProfileArtifacts"]["status"] == "available", summary
assert summary["generatedByRule"]["return-zero-cdecl"] == 1, summary
assert summary["semanticByRule"]["return-zero-cdecl"] == 1, summary
assert summary["generatedByLanguage"]["c"] == 1, summary
assert summary["semanticByLanguage"]["c"] == 1, summary
assert summary["generatedBySourceQuality"]["high-level-c"] == 1, summary
assert summary["highLevelSourceCandidates"] == 1, summary
assert summary["inlineAsmSourceCandidates"] == 0, summary
assert summary["byteEmissionSourceCandidates"] == 0, summary
coverage_artifacts = summary["sourceCoverageArtifacts"]
assert coverage_artifacts["semanticGeneratedRatio"] == 1.0, coverage_artifacts
assert coverage_artifacts["highLevelGeneratedRatio"] == 1.0, coverage_artifacts
assert coverage_artifacts["highLevelSemanticRatio"] == 1.0, coverage_artifacts
assert Path(coverage_artifacts["generatorOpportunities"]).exists(), coverage_artifacts
opportunities = json.loads(Path(coverage_artifacts["generatorOpportunities"]).read_text(encoding="utf-8"))
assert opportunities["classes"] == {}, opportunities
coverage = json.loads(Path(coverage_artifacts["semanticCoverage"]).read_text(encoding="utf-8"))
assert coverage["semanticTargetSliceRatio"] == 1.0, coverage
assert coverage["highLevelSourceCandidates"] == 1, coverage
assert coverage["generatedBySourceQuality"]["high-level-c"] == 1, coverage
assert coverage["generatorOpportunities"] == {}, coverage
assert coverage["topNonsemanticSlices"] == [], coverage

tasks_path = Path(summary["tasks"])
tasks = [json.loads(line) for line in tasks_path.read_text(encoding="utf-8").splitlines()]
assert len(tasks) == 1, tasks
task = tasks[0]
assert task["verificationTier"] == "source-generated-unverified", task
assert task["targetSlice"]["status"] == "complete", task
assert task["functionFact"]["callingConvention"] == "cdecl", task
assert task["functionFact"]["prototype"]["returnType"] == "int", task
assert task["compilerProfileArtifacts"]["artifacts"][0]["profileFlagMatches"]["vc71 /O2 /GS- /Oy"] == 3, task
assert task["acceptanceGate"].startswith("compile with selected compiler profile and objdiff-zero"), task

normalized = Path(summary["functionFactArtifacts"]["factsJsonl"])
rows = [json.loads(line) for line in normalized.read_text(encoding="utf-8").splitlines()]
assert rows[0]["schema"] == "reconkit.normalized-function-fact.v1", rows
assert rows[0]["calls"] == ["puts"], rows

candidate_json = run_dir / "source-generation" / "return_zero_401000" / "candidate.json"
candidate = json.loads(candidate_json.read_text(encoding="utf-8"))
assert candidate["sourceLanguage"] == "c", candidate
assert candidate["sourceQuality"] == "high-level-c", candidate
assert candidate["automaticGenerator"]["rule"] == "return-zero-cdecl", candidate
assert candidate["verificationTier"] == "source-generated-unverified", candidate

synth_out = run_dir / "synthesis"
synth_rc = synthesize_main(
    [
        "--queue",
        str(tmp / "missing-queue.jsonl"),
        "--source-tasks",
        str(tasks_path),
        "--remaining-features",
        str(tmp / "missing-features.jsonl"),
        "--retrieval",
        str(tmp / "missing-retrieval.jsonl"),
        "--out-dir",
        str(synth_out),
        "--dry-run",
        "--limit",
        "10",
    ]
)
assert synth_rc == 0, synth_rc
synth_summary = json.loads((synth_out / "summary.json").read_text(encoding="utf-8"))
assert synth_summary["generatedCandidates"] == 1, synth_summary
assert synth_summary["sourceTasks"] == [str(tasks_path)], synth_summary
assert Path(synth_summary["promotionTargetsPath"]).exists(), synth_summary
synth_attempts = [json.loads(line) for line in (synth_out / "attempts.jsonl").read_text(encoding="utf-8").splitlines()]
assert synth_attempts[0]["status"] == "generated-only", synth_attempts
assert synth_attempts[0]["rule"] == "return-zero", synth_attempts
assert synth_attempts[0]["compilerProfileName"] == "dry-run", synth_attempts

synth_verify_out = run_dir / "synthesis-verify"
synth_verify_rc = synthesize_main(
    [
        "--queue",
        str(tmp / "missing-queue.jsonl"),
        "--source-tasks",
        str(tasks_path),
        "--remaining-features",
        str(tmp / "missing-features.jsonl"),
        "--retrieval",
        str(tmp / "missing-retrieval.jsonl"),
        "--out-dir",
        str(synth_verify_out),
        "--compiler",
        "clang",
        "--limit",
        "10",
    ]
)
assert synth_verify_rc == 0, synth_verify_rc
synth_verify_summary = json.loads((synth_verify_out / "summary.json").read_text(encoding="utf-8"))
assert synth_verify_summary["acceptedCandidates"] == 0, synth_verify_summary
assert synth_verify_summary["codeSliceMatchedCandidates"] == 1, synth_verify_summary
assert synth_verify_summary["semanticCodeSliceMatchedCandidates"] == 1, synth_verify_summary
assert synth_verify_summary["nonSemanticCodeSliceMatchedCandidates"] == 0, synth_verify_summary
promotion_report = json.loads(Path(synth_verify_summary["promotionTargetsPath"]).read_text(encoding="utf-8"))
assert promotion_report["highLevelMatchedCandidates"] == 1, promotion_report
assert promotion_report["nonHighLevelMatchedCandidates"] == 0, promotion_report
assert promotion_report["promotionTargets"] == [], promotion_report
verified_attempts = [json.loads(line) for line in (synth_verify_out / "attempts.jsonl").read_text(encoding="utf-8").splitlines()]
assert verified_attempts[0]["status"] == "code-slice-matched", verified_attempts
assert verified_attempts[0]["differences"] == 0, verified_attempts
assert verified_attempts[0]["verificationTier"] == "synthetic-target-object-objdiff", verified_attempts
assert "synthetic object" in verified_attempts[0]["claimBoundary"], verified_attempts

pipeline_runner = RecoveryRunner(
    RecoveryConfig(
        input_path=binary,
        work_dir=run_dir,
        source_synthesis_mode="clang",
        source_synthesis_limit=10,
        source_synthesis_verify_packaged_source=True,
    )
)
pipeline_summary = pipeline_runner.stage_synthesize_source_tasks(pipeline_runner.stages[0])
assert pipeline_summary["status"] == "complete", pipeline_summary
assert pipeline_summary["verifyPackagedSource"] is True, pipeline_summary
assert pipeline_summary["generatedCandidates"] == 1, pipeline_summary
assert pipeline_summary["codeSliceMatchedCandidates"] == 1, pipeline_summary
assert pipeline_summary["semanticCodeSliceMatchedCandidates"] == 1, pipeline_summary
assert pipeline_summary["generatedBySourceQuality"] == {"high-level-c": 1}, pipeline_summary
assert pipeline_summary["semanticCodeSliceMatchedBySourceQuality"] == {"high-level-c": 1}, pipeline_summary
assert (run_dir / "source-synthesis" / "summary.json").exists(), pipeline_summary

strategy = build_strategy(
    TargetIdentity(
        input_path=binary,
        binary_path=binary,
        sha256="0" * 64,
        size=binary.stat().st_size,
        format="pe",
        architecture_hint="x86",
        stable_id="sample",
    ),
    {
        "tools": {"objdiff": {"available": True}, "wine": {"available": True}},
        "localSurfaces": {"oneShotSource": True},
    },
    inventory,
    {"summary": {"candidateCount": 1, "byConfidence": {"medium": 1}}},
    summary,
    pipeline_summary,
)
assert strategy["sourceGenerationSummary"]["targetSlices"] == 1, strategy
assert strategy["sourceGenerationSummary"]["functionFactArtifacts"]["factCount"] == 1, strategy
assert strategy["sourceGenerationSummary"]["compilerProfileArtifacts"]["status"] == "available", strategy
assert strategy["sourceSynthesisSummary"]["codeSliceMatchedCandidates"] == 1, strategy
assert strategy["sourceSynthesisSummary"]["semanticCodeSliceMatchedCandidates"] == 1, strategy
assert strategy["sourceSynthesisSummary"]["verifyPackagedSource"] is True, strategy
assert strategy["sourceSynthesisSummary"]["generatedBySourceQuality"] == {"high-level-c": 1}, strategy
assert strategy["sourceSynthesisSummary"]["semanticCodeSliceMatchedBySourceQuality"] == {"high-level-c": 1}, strategy
assert strategy["sourceSynthesisSummary"]["promotionTargetsPath"] == pipeline_summary["promotionTargetsPath"], strategy
assert strategy["lanes"][1]["status"] == "semantic-code-slice-evidence", strategy
accepted = [row for row in strategy["verificationLadder"] if row["acceptedSource"]]
assert accepted == [{"tier": "target-object-objdiff-match", "acceptedSource": True}], strategy
PY

echo "ok"
