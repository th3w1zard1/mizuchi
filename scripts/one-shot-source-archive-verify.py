#!/usr/bin/env python3
"""Verify a portable one-shot source package archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_json_replay_report_shapes() -> dict[str, Any]:
    return {
        "preflight": {
            "schema": "reconkit.one-shot-source-reconstruction-json-preflight.v1",
            "buildOverrideCount": "number of response candidate paths with build overrides",
            "buildOverridePaths": ["all response candidate paths with build overrides"],
            "buildOverrideExpectedPaths": ["expected candidate paths with build overrides"],
            "buildOverrideExtraPaths": ["extra candidate paths with build overrides"],
        },
        "import": {
            "schema": "reconkit.one-shot-source-reconstruction-json-import.v1",
            "buildOverrideCount": "number of response candidate paths with build overrides, including extras",
            "buildOverridePaths": ["all response candidate paths with build overrides"],
            "buildOverrideExpectedPaths": ["importable expected candidate paths with build overrides"],
            "buildOverrideExtraPaths": ["extra candidate paths with build overrides that were not imported"],
        },
    }


def safe_members(archive: tarfile.TarFile) -> tuple[list[tarfile.TarInfo], str]:
    members = archive.getmembers()
    if not members:
        raise SystemExit("archive is empty")
    roots = set()
    for member in members:
        member_path = Path(member.name)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise SystemExit(f"unsafe archive member path: {member.name}")
        if member.issym() or member.islnk():
            raise SystemExit(f"archive links are not allowed: {member.name}")
        if not member_path.parts:
            raise SystemExit(f"invalid archive member path: {member.name}")
        roots.add(member_path.parts[0])
    if len(roots) != 1:
        raise SystemExit(f"archive must contain exactly one package root, found: {sorted(roots)}")
    root = next(iter(roots))
    expected_prefix = f"{root}/"
    for member in members:
        if member.name != root and not member.name.startswith(expected_prefix):
            raise SystemExit(f"archive member outside package root: {member.name}")
    return members, root


def verify_archive(
    archive_path: Path,
    timeout: int,
    expect_archive_sha256: str | None = None,
    expect_content_identity: str | None = None,
) -> dict[str, Any]:
    archive_sha = sha256_file(archive_path)
    archive_sha_matches = expect_archive_sha256 is None or archive_sha == expect_archive_sha256
    with tempfile.TemporaryDirectory(prefix="reconkit-one-shot-archive-verify-") as tmp:
        tmp_dir = Path(tmp)
        with tarfile.open(archive_path, "r:gz") as archive:
            members, root = safe_members(archive)
            archive.extractall(tmp_dir, members)
        package_dir = tmp_dir / root
        verifier = package_dir / "VERIFY.py"
        if not verifier.exists():
            raise SystemExit(f"archive package has no VERIFY.py: {root}")
        env = os.environ.copy()
        env["CCACHE_DISABLE"] = "1"
        env["CCACHE_DIR"] = str(package_dir / ".ccache")
        try:
            proc = subprocess.run(
                [os.environ.get("PYTHON", "python3"), "VERIFY.py"],
                cwd=package_dir,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return {
                "schema": "reconkit.one-shot-source-archive-verify.v1",
                "archive": str(archive_path),
                "archiveSha256": archive_sha,
                "packageRoot": root,
                "status": "failed",
                "ok": False,
                "error": f"VERIFY.py timed out after {timeout}s",
            }
        claims_path = package_dir / "CLAIMS.json"
        content_path = package_dir / "CONTENT_MANIFEST.json"
        gates_path = package_dir / "AUTHORITY_GATES.json"
        candidates_path = package_dir / "VERIFIED_SOURCE_CANDIDATES.json"
        recipe_path = package_dir / "CANDIDATE_BUILD_RECIPE.json"
        package_proof_path = package_dir / "PACKAGE_PROOF.json"
        binary_evidence_path = package_dir / "BINARY_EVIDENCE.json"
        boundary_candidates_path = package_dir / "FUNCTION_BOUNDARY_CANDIDATES.json"
        function_byte_slices_path = package_dir / "FUNCTION_BYTE_SLICES.json"
        function_reconstruction_results_path = package_dir / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json"
        function_reconstruction_tasks_path = package_dir / "FUNCTION_RECONSTRUCTION_TASKS.json"
        function_slice_sources_path = package_dir / "FUNCTION_SLICE_SOURCES.json"
        source_roles_path = package_dir / "SOURCE_ROLES.json"
        semantic_readiness_path = package_dir / "SEMANTIC_READINESS.json"
        semantic_authority_evaluation_path = package_dir / "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json"
        semantic_authority_evaluator_path = package_dir / "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py"
        toolchain_path = package_dir / "TOOLCHAIN_PROVENANCE.json"
        authority_summary_path = package_dir / "AUTHORITY_SUMMARY.json"
        proof_commands_path = package_dir / "PROOF_COMMANDS.json"
        one_shot_importer_path = package_dir / "IMPORT_RECONSTRUCTION_CANDIDATES.py"
        one_shot_json_importer_path = package_dir / "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py"
        one_shot_json_validator_path = package_dir / "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py"
        one_shot_receipt_refresher_path = package_dir / "REFRESH_RECONSTRUCTION_RECEIPTS.py"
        one_shot_request_path = package_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.md"
        one_shot_request_json_path = package_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.json"
        one_shot_request_bundle_path = package_dir / "ONE_SHOT_RECONSTRUCTION_BUNDLE.json"
        one_shot_response_template_path = package_dir / "RECONSTRUCTION_RESPONSE_TEMPLATE.json"
        one_shot_response_template_exporter_path = package_dir / "EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py"
        one_shot_byte_exporter_path = package_dir / "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py"
        one_shot_byte_prover_path = package_dir / "PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py"
        reconstruction_replay_path = package_dir / "REPLAY_RECONSTRUCTION_CANDIDATES.py"
        claims: dict[str, Any] = {}
        content: dict[str, Any] = {}
        gates: dict[str, Any] = {}
        candidates: dict[str, Any] = {}
        recipe: dict[str, Any] = {}
        package_proof: dict[str, Any] = {}
        binary_evidence: dict[str, Any] = {}
        boundary_candidates: dict[str, Any] = {}
        function_byte_slices: dict[str, Any] = {}
        function_reconstruction_results: dict[str, Any] = {}
        function_reconstruction_tasks: dict[str, Any] = {}
        function_slice_sources: dict[str, Any] = {}
        source_roles: dict[str, Any] = {}
        semantic_readiness: dict[str, Any] = {}
        semantic_authority_evaluation: dict[str, Any] = {}
        toolchain: dict[str, Any] = {}
        package_authority_summary: dict[str, Any] = {}
        proof_commands: dict[str, Any] = {}
        one_shot_request_bundle: dict[str, Any] = {}
        one_shot_response_template: dict[str, Any] = {}
        one_shot_importer_sha256: str | None = sha256_file(one_shot_importer_path) if one_shot_importer_path.exists() else None
        one_shot_json_importer_sha256: str | None = (
            sha256_file(one_shot_json_importer_path) if one_shot_json_importer_path.exists() else None
        )
        one_shot_json_validator_sha256: str | None = (
            sha256_file(one_shot_json_validator_path) if one_shot_json_validator_path.exists() else None
        )
        one_shot_receipt_refresher_sha256: str | None = (
            sha256_file(one_shot_receipt_refresher_path) if one_shot_receipt_refresher_path.exists() else None
        )
        one_shot_request_sha256: str | None = sha256_file(one_shot_request_path) if one_shot_request_path.exists() else None
        one_shot_request_json_sha256: str | None = (
            sha256_file(one_shot_request_json_path) if one_shot_request_json_path.exists() else None
        )
        one_shot_request_bundle_sha256: str | None = (
            sha256_file(one_shot_request_bundle_path) if one_shot_request_bundle_path.exists() else None
        )
        one_shot_response_template_sha256: str | None = (
            sha256_file(one_shot_response_template_path) if one_shot_response_template_path.exists() else None
        )
        one_shot_response_template_exporter_sha256: str | None = (
            sha256_file(one_shot_response_template_exporter_path)
            if one_shot_response_template_exporter_path.exists()
            else None
        )
        one_shot_byte_exporter_sha256: str | None = (
            sha256_file(one_shot_byte_exporter_path) if one_shot_byte_exporter_path.exists() else None
        )
        one_shot_byte_prover_sha256: str | None = (
            sha256_file(one_shot_byte_prover_path) if one_shot_byte_prover_path.exists() else None
        )
        semantic_authority_evaluator_sha256: str | None = (
            sha256_file(semantic_authority_evaluator_path) if semantic_authority_evaluator_path.exists() else None
        )
        binary_evidence_sha256: str | None = None
        boundary_candidates_sha256: str | None = None
        function_byte_slices_sha256: str | None = None
        function_reconstruction_tasks_sha256: str | None = None
        function_reconstruction_results_sha256: str | None = None
        reconstruction_replay_sha256: str | None = sha256_file(reconstruction_replay_path) if reconstruction_replay_path.exists() else None
        function_slice_sources_sha256: str | None = None
        semantic_readiness_sha256: str | None = None
        one_shot_request_json: dict[str, Any] = {}
        function_byte_slice_errors: list[str] = []
        function_reconstruction_task_errors: list[str] = []
        function_slice_source_errors: list[str] = []
        one_shot_bundle_evidence_errors: list[str] = []
        if claims_path.exists():
            claims = json.loads(claims_path.read_text())
        if content_path.exists():
            content = json.loads(content_path.read_text())
        if gates_path.exists():
            gates = json.loads(gates_path.read_text())
        if candidates_path.exists():
            candidates = json.loads(candidates_path.read_text())
        if recipe_path.exists():
            recipe = json.loads(recipe_path.read_text())
        if package_proof_path.exists():
            package_proof = json.loads(package_proof_path.read_text())
        if binary_evidence_path.exists():
            binary_evidence = json.loads(binary_evidence_path.read_text())
            binary_evidence_sha256 = sha256_file(binary_evidence_path)
        if boundary_candidates_path.exists():
            boundary_candidates = json.loads(boundary_candidates_path.read_text())
            boundary_candidates_sha256 = sha256_file(boundary_candidates_path)
        if function_byte_slices_path.exists():
            function_byte_slices = json.loads(function_byte_slices_path.read_text())
            function_byte_slices_sha256 = sha256_file(function_byte_slices_path)
            original_path = package_dir / "original.bin"
            original_bytes = original_path.read_bytes() if original_path.exists() else b""
            slices = function_byte_slices.get("slices")
            if isinstance(slices, list):
                for item in slices:
                    if not isinstance(item, dict):
                        function_byte_slice_errors.append("FUNCTION_BYTE_SLICES.json contains non-object slice")
                        continue
                    if item.get("verifiedAgainstSource") is not False:
                        function_byte_slice_errors.append(f"function byte slice overclaims source verification: {item.get('name')}")
                    offset = item.get("fileOffset")
                    size = item.get("size")
                    if not isinstance(offset, int) or not isinstance(size, int) or offset < 0 or size <= 0:
                        function_byte_slice_errors.append(f"function byte slice has invalid offset/size: {item.get('name')}")
                        continue
                    if offset + size > len(original_bytes):
                        function_byte_slice_errors.append(f"function byte slice is outside original.bin: {item.get('name')}")
                        continue
                    if hashlib.sha256(original_bytes[offset : offset + size]).hexdigest() != item.get("sha256"):
                        function_byte_slice_errors.append(f"function byte slice hash mismatch: {item.get('name')}")
        if function_slice_sources_path.exists():
            function_slice_sources = json.loads(function_slice_sources_path.read_text())
            function_slice_sources_sha256 = sha256_file(function_slice_sources_path)
            function_sources = function_slice_sources.get("sources")
            if isinstance(function_sources, list):
                for item in function_sources:
                    if not isinstance(item, dict):
                        function_slice_source_errors.append("FUNCTION_SLICE_SOURCES.json contains non-object source")
                        continue
                    rel = item.get("path")
                    rel_path = Path(str(rel or ""))
                    if not isinstance(rel, str) or rel_path.is_absolute() or ".." in rel_path.parts:
                        function_slice_source_errors.append("FUNCTION_SLICE_SOURCES.json has unsafe source path")
                        continue
                    source_path = package_dir / rel_path
                    if not source_path.exists():
                        function_slice_source_errors.append(f"function slice source missing: {rel}")
                    elif sha256_file(source_path) != item.get("sourceSha256"):
                        function_slice_source_errors.append(f"function slice source hash mismatch: {rel}")
                    if item.get("semanticDecompilation") is not False or item.get("verifiedAgainstSource") is not False:
                        function_slice_source_errors.append(f"function slice source overclaims semantic/source verification: {rel}")
        if function_reconstruction_tasks_path.exists():
            function_reconstruction_tasks = json.loads(function_reconstruction_tasks_path.read_text())
            function_reconstruction_tasks_sha256 = sha256_file(function_reconstruction_tasks_path)
            tasks = function_reconstruction_tasks.get("tasks")
            if isinstance(tasks, list):
                for item in tasks:
                    if not isinstance(item, dict):
                        function_reconstruction_task_errors.append("FUNCTION_RECONSTRUCTION_TASKS.json contains non-object task")
                        continue
                    if item.get("semanticDecompilation") is not False or item.get("verifiedAgainstSource") is not False:
                        function_reconstruction_task_errors.append(
                            f"function reconstruction task overclaims semantic/source verification: {item.get('name')}"
                        )
                    for key, hash_key in (
                        ("taskJson", "taskJsonSha256"),
                        ("readme", "readmeSha256"),
                        ("candidateVerifier", "candidateVerifierSha256"),
                        ("oneShotPrompt", "oneShotPromptSha256"),
                        ("targetBytes", "targetBytesSha256"),
                    ):
                        rel = item.get(key)
                        rel_path = Path(str(rel or ""))
                        if not isinstance(rel, str) or rel_path.is_absolute() or ".." in rel_path.parts:
                            function_reconstruction_task_errors.append(f"FUNCTION_RECONSTRUCTION_TASKS.json has unsafe {key} path")
                            continue
                        task_file = package_dir / rel_path
                        if not task_file.exists():
                            function_reconstruction_task_errors.append(f"function reconstruction task file missing: {rel}")
                        elif sha256_file(task_file) != item.get(hash_key):
                            function_reconstruction_task_errors.append(f"function reconstruction task hash mismatch: {rel}")
        if function_reconstruction_results_path.exists():
            function_reconstruction_results = json.loads(function_reconstruction_results_path.read_text())
            function_reconstruction_results_sha256 = sha256_file(function_reconstruction_results_path)
        if one_shot_request_json_path.exists():
            one_shot_request_json = json.loads(one_shot_request_json_path.read_text())
        if one_shot_request_bundle_path.exists():
            one_shot_request_bundle = json.loads(one_shot_request_bundle_path.read_text())
        if one_shot_response_template_path.exists():
            one_shot_response_template = json.loads(one_shot_response_template_path.read_text())
        if source_roles_path.exists():
            source_roles = json.loads(source_roles_path.read_text())
        if semantic_readiness_path.exists():
            semantic_readiness = json.loads(semantic_readiness_path.read_text())
            semantic_readiness_sha256 = sha256_file(semantic_readiness_path)
        if semantic_authority_evaluation_path.exists():
            semantic_authority_evaluation = json.loads(semantic_authority_evaluation_path.read_text())
        if toolchain_path.exists():
            toolchain = json.loads(toolchain_path.read_text())
        if authority_summary_path.exists():
            package_authority_summary = json.loads(authority_summary_path.read_text())
        authority_summary_sha256 = sha256_file(authority_summary_path) if authority_summary_path.exists() else None
        if proof_commands_path.exists():
            proof_commands = json.loads(proof_commands_path.read_text())
        bundle_tasks_for_evidence = one_shot_request_bundle.get("tasks")
        if isinstance(bundle_tasks_for_evidence, list):
            for row in bundle_tasks_for_evidence:
                if not isinstance(row, dict):
                    one_shot_bundle_evidence_errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json contains non-object task")
                    continue
                target = row.get("targetBytes")
                if not isinstance(target, dict):
                    one_shot_bundle_evidence_errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json task has no targetBytes object")
                else:
                    target_rel = target.get("path")
                    target_path = package_dir / str(target_rel or "")
                    if not isinstance(target_rel, str) or Path(target_rel).is_absolute() or ".." in Path(target_rel).parts:
                        one_shot_bundle_evidence_errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json has unsafe target bytes path")
                    elif not target_path.exists():
                        one_shot_bundle_evidence_errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json target bytes missing: {target_rel}")
                    else:
                        target_bytes = target_path.read_bytes()
                        if target.get("sha256") != sha256_file(target_path):
                            one_shot_bundle_evidence_errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json target bytes hash mismatch: {target_rel}")
                        if target.get("size") != len(target_bytes):
                            one_shot_bundle_evidence_errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json target bytes size mismatch: {target_rel}")
                        if target.get("hex") != target_bytes.hex():
                            one_shot_bundle_evidence_errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json target bytes hex mismatch: {target_rel}")
                reference = row.get("referenceByteEmitter")
                if not isinstance(reference, dict):
                    one_shot_bundle_evidence_errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json task has no referenceByteEmitter object")
                else:
                    reference_rel = reference.get("path")
                    reference_path = package_dir / str(reference_rel or "")
                    if not isinstance(reference_rel, str) or Path(reference_rel).is_absolute() or ".." in Path(reference_rel).parts:
                        one_shot_bundle_evidence_errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json has unsafe reference byte-emitter path")
                    elif not reference_path.exists():
                        one_shot_bundle_evidence_errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json reference byte-emitter missing: {reference_rel}")
                    else:
                        if reference.get("sha256") != sha256_file(reference_path):
                            one_shot_bundle_evidence_errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json reference byte-emitter hash mismatch: {reference_rel}")
                        if reference.get("sourceText") != reference_path.read_text():
                            one_shot_bundle_evidence_errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json reference byte-emitter text mismatch: {reference_rel}")
    content_identity = content.get("contentIdentity") or claims.get("contentIdentity")
    content_identity_matches = expect_content_identity is None or content_identity == expect_content_identity
    authority_errors: list[str] = []
    if claims.get("status") != "authoritative":
        authority_errors.append("CLAIMS.json status is not authoritative")
    if claims.get("authorityClass") != "byte-authoritative-source":
        authority_errors.append("CLAIMS.json authorityClass is not byte-authoritative-source")
    if claims.get("accuracyClass") != "byte-exact":
        authority_errors.append("CLAIMS.json accuracyClass is not byte-exact")
    proven = claims.get("proven") if isinstance(claims.get("proven"), dict) else {}
    for key in (
        "selfContainedPackage",
        "originalBytesIncluded",
        "assemblerSourceRebuildsOriginalBytes",
        "cSourceEmitsOriginalBytes",
        "packageLocalVerifierPassedAtGeneration",
    ):
        if proven.get(key) is not True:
            authority_errors.append(f"CLAIMS.json does not prove {key}")
    if proven.get("semanticDecompilation") is not False:
        authority_errors.append("CLAIMS.json overclaims semantic decompilation")
    source_accuracy = claims.get("sourceAccuracy") if isinstance(claims.get("sourceAccuracy"), dict) else {}
    for key in ("assembler", "cByteEmitter"):
        item = source_accuracy.get(key)
        if not isinstance(item, dict) or item.get("byteIdentical") is not True:
            authority_errors.append(f"CLAIMS.json sourceAccuracy does not prove {key}")
    if gates.get("status") != "passed":
        authority_errors.append("AUTHORITY_GATES.json status is not passed")
    if gates.get("authorityClass") != "byte-authoritative-source":
        authority_errors.append("AUTHORITY_GATES.json authorityClass is not byte-authoritative-source")
    if gates.get("accuracyClass") != "byte-exact":
        authority_errors.append("AUTHORITY_GATES.json accuracyClass is not byte-exact")
    for gate in gates.get("gates") if isinstance(gates.get("gates"), list) else []:
        if isinstance(gate, dict) and gate.get("status") != "passed":
            authority_errors.append(f"authority gate failed: {gate.get('id')}")
    if candidates.get("status") != "authoritative":
        authority_errors.append("VERIFIED_SOURCE_CANDIDATES.json status is not authoritative")
    if candidates.get("authorityClass") != "byte-authoritative-source":
        authority_errors.append("VERIFIED_SOURCE_CANDIDATES.json authorityClass is not byte-authoritative-source")
    if candidates.get("accuracyClass") != "byte-exact":
        authority_errors.append("VERIFIED_SOURCE_CANDIDATES.json accuracyClass is not byte-exact")
    if candidates.get("semanticDecompilation") is not False:
        authority_errors.append("VERIFIED_SOURCE_CANDIDATES.json overclaims semantic decompilation")
    for candidate in candidates.get("candidates") if isinstance(candidates.get("candidates"), list) else []:
        if not isinstance(candidate, dict):
            authority_errors.append("VERIFIED_SOURCE_CANDIDATES.json contains non-object candidate")
            continue
        if candidate.get("byteIdentical") is not True:
            authority_errors.append(f"source candidate is not byte-identical: {candidate.get('path')}")
        if candidate.get("accuracyClass") != "byte-exact":
            authority_errors.append(f"source candidate accuracyClass is not byte-exact: {candidate.get('path')}")
        if candidate.get("semanticDecompilation") is not False:
            authority_errors.append(f"source candidate overclaims semantic decompilation: {candidate.get('path')}")
    if package_proof.get("status") != "authoritative":
        authority_errors.append("PACKAGE_PROOF.json status is not authoritative")
    if package_proof.get("authorityClass") != "byte-authoritative-source":
        authority_errors.append("PACKAGE_PROOF.json authorityClass is not byte-authoritative-source")
    if package_proof.get("accuracyClass") != "byte-exact":
        authority_errors.append("PACKAGE_PROOF.json accuracyClass is not byte-exact")
    if binary_evidence.get("schema") != "reconkit.one-shot-source-binary-evidence.v1":
        authority_errors.append("BINARY_EVIDENCE.json schema mismatch")
    if binary_evidence.get("status") != "recorded":
        authority_errors.append("BINARY_EVIDENCE.json status is not recorded")
    original_evidence = binary_evidence.get("original") if isinstance(binary_evidence.get("original"), dict) else {}
    if original_evidence.get("sha256") != claims.get("sourceAccuracy", {}).get("assembler", {}).get("rebuildOutputSha256"):
        authority_errors.append("BINARY_EVIDENCE.json original hash mismatch")
    hints = binary_evidence.get("functionBoundaryHints")
    if not isinstance(hints, dict):
        authority_errors.append("BINARY_EVIDENCE.json has no functionBoundaryHints object")
    elif hints.get("verifiedAgainstSource") is not False:
        authority_errors.append("BINARY_EVIDENCE.json overclaims verified source boundaries")
    if package_proof.get("binaryEvidence") != binary_evidence:
        authority_errors.append("PACKAGE_PROOF.json binaryEvidence mismatch")
    if boundary_candidates.get("schema") != "reconkit.one-shot-source-function-boundary-candidates.v1":
        authority_errors.append("FUNCTION_BOUNDARY_CANDIDATES.json schema mismatch")
    if boundary_candidates.get("status") not in ("hints-present", "absent"):
        authority_errors.append("FUNCTION_BOUNDARY_CANDIDATES.json status mismatch")
    if boundary_candidates.get("verifiedAgainstSource") is not False:
        authority_errors.append("FUNCTION_BOUNDARY_CANDIDATES.json overclaims verified source boundaries")
    if boundary_candidates.get("binaryEvidenceSha256") != binary_evidence_sha256:
        authority_errors.append("FUNCTION_BOUNDARY_CANDIDATES.json binaryEvidenceSha256 mismatch")
    boundary_items = boundary_candidates.get("candidates")
    if not isinstance(boundary_items, list):
        authority_errors.append("FUNCTION_BOUNDARY_CANDIDATES.json has no candidates list")
    elif boundary_candidates.get("candidateCount") != len(boundary_items):
        authority_errors.append("FUNCTION_BOUNDARY_CANDIDATES.json candidateCount mismatch")
    if package_proof.get("functionBoundaryCandidates") != boundary_candidates:
        authority_errors.append("PACKAGE_PROOF.json functionBoundaryCandidates mismatch")
    if function_byte_slices.get("schema") != "reconkit.one-shot-source-function-byte-slices.v1":
        authority_errors.append("FUNCTION_BYTE_SLICES.json schema mismatch")
    if function_byte_slices.get("status") not in ("slices-present", "absent"):
        authority_errors.append("FUNCTION_BYTE_SLICES.json status mismatch")
    if function_byte_slices.get("binaryEvidenceSha256") != binary_evidence_sha256:
        authority_errors.append("FUNCTION_BYTE_SLICES.json binaryEvidenceSha256 mismatch")
    if function_byte_slices.get("functionBoundaryCandidatesSha256") != boundary_candidates_sha256:
        authority_errors.append("FUNCTION_BYTE_SLICES.json functionBoundaryCandidatesSha256 mismatch")
    if function_byte_slices.get("verifiedAgainstSource") is not False:
        authority_errors.append("FUNCTION_BYTE_SLICES.json overclaims source verification")
    slice_items = function_byte_slices.get("slices")
    if not isinstance(slice_items, list):
        authority_errors.append("FUNCTION_BYTE_SLICES.json has no slices list")
    elif function_byte_slices.get("sliceCount") != len(slice_items):
        authority_errors.append("FUNCTION_BYTE_SLICES.json sliceCount mismatch")
    authority_errors.extend(function_byte_slice_errors)
    if package_proof.get("functionByteSlices") != function_byte_slices:
        authority_errors.append("PACKAGE_PROOF.json functionByteSlices mismatch")
    if function_slice_sources.get("schema") != "reconkit.one-shot-source-function-slice-sources.v1":
        authority_errors.append("FUNCTION_SLICE_SOURCES.json schema mismatch")
    if function_slice_sources.get("status") not in ("sources-present", "absent"):
        authority_errors.append("FUNCTION_SLICE_SOURCES.json status mismatch")
    if function_slice_sources.get("functionByteSlicesSha256") != function_byte_slices_sha256:
        authority_errors.append("FUNCTION_SLICE_SOURCES.json functionByteSlicesSha256 mismatch")
    if function_slice_sources.get("semanticDecompilation") is not False or function_slice_sources.get("verifiedAgainstSource") is not False:
        authority_errors.append("FUNCTION_SLICE_SOURCES.json overclaims semantic/source verification")
    function_sources = function_slice_sources.get("sources")
    if not isinstance(function_sources, list):
        authority_errors.append("FUNCTION_SLICE_SOURCES.json has no sources list")
    elif function_slice_sources.get("sourceCount") != len(function_sources):
        authority_errors.append("FUNCTION_SLICE_SOURCES.json sourceCount mismatch")
    authority_errors.extend(function_slice_source_errors)
    if package_proof.get("functionSliceSources") != function_slice_sources:
        authority_errors.append("PACKAGE_PROOF.json functionSliceSources mismatch")
    if function_reconstruction_tasks.get("schema") != "reconkit.one-shot-source-function-reconstruction-tasks.v1":
        authority_errors.append("FUNCTION_RECONSTRUCTION_TASKS.json schema mismatch")
    if function_reconstruction_tasks.get("status") not in ("tasks-present", "absent"):
        authority_errors.append("FUNCTION_RECONSTRUCTION_TASKS.json status mismatch")
    if function_reconstruction_tasks.get("functionByteSlicesSha256") != function_byte_slices_sha256:
        authority_errors.append("FUNCTION_RECONSTRUCTION_TASKS.json functionByteSlicesSha256 mismatch")
    if function_reconstruction_tasks.get("functionSliceSourcesSha256") != function_slice_sources_sha256:
        authority_errors.append("FUNCTION_RECONSTRUCTION_TASKS.json functionSliceSourcesSha256 mismatch")
    if (
        function_reconstruction_tasks.get("semanticDecompilation") is not False
        or function_reconstruction_tasks.get("verifiedAgainstSource") is not False
    ):
        authority_errors.append("FUNCTION_RECONSTRUCTION_TASKS.json overclaims semantic/source verification")
    task_items = function_reconstruction_tasks.get("tasks")
    if not isinstance(task_items, list):
        authority_errors.append("FUNCTION_RECONSTRUCTION_TASKS.json has no tasks list")
    elif function_reconstruction_tasks.get("taskCount") != len(task_items):
        authority_errors.append("FUNCTION_RECONSTRUCTION_TASKS.json taskCount mismatch")
    authority_errors.extend(function_reconstruction_task_errors)
    if package_proof.get("functionReconstructionTasks") != function_reconstruction_tasks:
        authority_errors.append("PACKAGE_PROOF.json functionReconstructionTasks mismatch")
    if function_reconstruction_results.get("schema") != "reconkit.one-shot-source-function-reconstruction-candidate-replay.v1":
        authority_errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json schema mismatch")
    if function_reconstruction_results.get("status") not in ("no-candidates", "partial", "matched", "failed"):
        authority_errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json status mismatch")
    if function_reconstruction_results.get("functionReconstructionTasksSha256") != function_reconstruction_tasks_sha256:
        authority_errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json functionReconstructionTasksSha256 mismatch")
    if function_reconstruction_results.get("replayScriptSha256") != reconstruction_replay_sha256:
        authority_errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json replayScriptSha256 mismatch")
    if function_reconstruction_results.get("semanticDecompilation") is not False:
        authority_errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json overclaims semantic decompilation")
    result_items = function_reconstruction_results.get("tasks")
    if not isinstance(result_items, list):
        authority_errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json has no tasks list")
    elif function_reconstruction_results.get("taskCount") != function_reconstruction_tasks.get("taskCount"):
        authority_errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json taskCount mismatch")
    elif isinstance(result_items, list):
        matched = function_reconstruction_results.get("matchedCount")
        failed = function_reconstruction_results.get("failedCount")
        skipped = function_reconstruction_results.get("skippedCount")
        if not all(isinstance(value, int) and value >= 0 for value in (matched, failed, skipped)):
            authority_errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json has invalid counts")
        elif matched + failed + skipped != len(result_items):
            authority_errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json count totals mismatch")
        else:
            row_matched = sum(1 for row in result_items if isinstance(row, dict) and row.get("status") == "matched")
            row_failed = sum(1 for row in result_items if isinstance(row, dict) and row.get("status") == "failed")
            row_skipped = sum(1 for row in result_items if isinstance(row, dict) and row.get("status") == "skipped")
            if (matched, failed, skipped) != (row_matched, row_failed, row_skipped):
                authority_errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json count row mismatch")
            result_status = function_reconstruction_results.get("status")
            if result_status == "matched" and not (matched > 0 and failed == 0 and skipped == 0):
                authority_errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json matched status/count mismatch")
            elif result_status == "partial" and not (matched > 0 and failed == 0 and skipped > 0):
                authority_errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json partial status/count mismatch")
            elif result_status == "failed" and failed <= 0:
                authority_errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json failed status/count mismatch")
            elif result_status == "no-candidates" and not (matched == 0 and failed == 0):
                authority_errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json no-candidates status/count mismatch")
        for row in result_items:
            if not isinstance(row, dict):
                authority_errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json contains non-object task row")
                continue
            if row.get("status") not in ("matched", "failed", "skipped"):
                authority_errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json task row status mismatch")
            if row.get("status") in ("matched", "failed"):
                candidate_rel = row.get("candidate")
                candidate_path = package_dir / str(candidate_rel or "")
                if not isinstance(candidate_rel, str) or Path(candidate_rel).is_absolute() or ".." in Path(candidate_rel).parts:
                    authority_errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json has unsafe candidate path")
                elif not candidate_path.exists():
                    authority_errors.append(f"candidate source listed in results is missing: {candidate_rel}")
                elif row.get("candidateSourceSha256") != sha256_file(candidate_path):
                    authority_errors.append(f"candidate source hash mismatch: {candidate_rel}")
                build_env_rel = row.get("candidateBuildEnv")
                if build_env_rel is not None:
                    build_env_path = package_dir / str(build_env_rel or "")
                    if (
                        not isinstance(build_env_rel, str)
                        or Path(build_env_rel).is_absolute()
                        or ".." in Path(build_env_rel).parts
                    ):
                        authority_errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json has unsafe candidateBuildEnv path")
                    elif not build_env_path.exists():
                        authority_errors.append(f"candidate build env listed in results is missing: {build_env_rel}")
                    elif row.get("candidateBuildEnvSha256") != sha256_file(build_env_path):
                        authority_errors.append(f"candidate build env hash mismatch: {build_env_rel}")
                if not isinstance(row.get("candidateOutputSha256"), str):
                    authority_errors.append(f"candidate output hash missing: {candidate_rel}")
                if row.get("byteIdentical") is not (row.get("status") == "matched"):
                    authority_errors.append(f"candidate byteIdentical flag mismatch: {candidate_rel}")
    if package_proof.get("functionReconstructionCandidateResults") != function_reconstruction_results:
        authority_errors.append("PACKAGE_PROOF.json functionReconstructionCandidateResults mismatch")
    one_shot_request = package_proof.get("oneShotReconstructionRequest")
    if not isinstance(one_shot_request, dict):
        authority_errors.append("PACKAGE_PROOF.json missing oneShotReconstructionRequest")
    else:
        if one_shot_request.get("path") != "ONE_SHOT_RECONSTRUCTION_REQUEST.md":
            authority_errors.append("PACKAGE_PROOF.json oneShotReconstructionRequest path mismatch")
        if one_shot_request.get("sha256") != one_shot_request_sha256:
            authority_errors.append("PACKAGE_PROOF.json oneShotReconstructionRequest hash mismatch")
        if one_shot_request.get("taskCount") != function_reconstruction_tasks.get("taskCount"):
            authority_errors.append("PACKAGE_PROOF.json oneShotReconstructionRequest taskCount mismatch")
        if one_shot_request.get("semanticDecompilation") is not False:
            authority_errors.append("PACKAGE_PROOF.json oneShotReconstructionRequest overclaims semantic decompilation")
    one_shot_request_json_proof = package_proof.get("oneShotReconstructionRequestJson")
    if not isinstance(one_shot_request_json_proof, dict):
        authority_errors.append("PACKAGE_PROOF.json missing oneShotReconstructionRequestJson")
    else:
        if one_shot_request_json.get("schema") != "reconkit.one-shot-source-reconstruction-request.v1":
            authority_errors.append("ONE_SHOT_RECONSTRUCTION_REQUEST.json schema mismatch")
        if one_shot_request_json.get("taskCount") != function_reconstruction_tasks.get("taskCount"):
            authority_errors.append("ONE_SHOT_RECONSTRUCTION_REQUEST.json taskCount mismatch")
        if one_shot_request_json.get("semanticDecompilation") is not False:
            authority_errors.append("ONE_SHOT_RECONSTRUCTION_REQUEST.json overclaims semantic decompilation")
        preferred = one_shot_request_json.get("preferredResponse")
        if not isinstance(preferred, dict) or preferred.get("schema") != "reconkit.one-shot-source-reconstruction-response.v1":
            authority_errors.append("ONE_SHOT_RECONSTRUCTION_REQUEST.json preferred response mismatch")
        elif not isinstance(preferred.get("structuredShape"), dict) or preferred["structuredShape"].get("schema") != "reconkit.one-shot-source-reconstruction-response.v1":
            authority_errors.append("ONE_SHOT_RECONSTRUCTION_REQUEST.json structured preferred response mismatch")
        elif preferred.get("replayReportShapes") != expected_json_replay_report_shapes():
            authority_errors.append("ONE_SHOT_RECONSTRUCTION_REQUEST.json replay report shapes mismatch")
        request_commands = one_shot_request_json.get("commands")
        if not isinstance(request_commands, dict):
            authority_errors.append("ONE_SHOT_RECONSTRUCTION_REQUEST.json has no commands object")
        else:
            expected_request_commands = {
                "validateJson": "./VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json",
                "validateJsonWithBuildCommand": "./VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json --allow-build-command",
                "importJson": "./IMPORT_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json",
                "importJsonWithBuildCommand": "./IMPORT_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json --allow-build-command",
                "refreshReceipts": "./REFRESH_RECONSTRUCTION_RECEIPTS.py",
            }
            for key, expected_command in expected_request_commands.items():
                if request_commands.get(key) != expected_command:
                    authority_errors.append(f"ONE_SHOT_RECONSTRUCTION_REQUEST.json {key} command mismatch")
        if one_shot_request_json_proof.get("path") != "ONE_SHOT_RECONSTRUCTION_REQUEST.json":
            authority_errors.append("PACKAGE_PROOF.json oneShotReconstructionRequestJson path mismatch")
        if one_shot_request_json_proof.get("sha256") != one_shot_request_json_sha256:
            authority_errors.append("PACKAGE_PROOF.json oneShotReconstructionRequestJson hash mismatch")
        if one_shot_request_json_proof.get("taskCount") != function_reconstruction_tasks.get("taskCount"):
            authority_errors.append("PACKAGE_PROOF.json oneShotReconstructionRequestJson taskCount mismatch")
    one_shot_request_bundle_proof = package_proof.get("oneShotReconstructionBundle")
    if not isinstance(one_shot_request_bundle_proof, dict):
        authority_errors.append("PACKAGE_PROOF.json missing oneShotReconstructionBundle")
    else:
        if one_shot_request_bundle.get("schema") != "reconkit.one-shot-source-reconstruction-request-bundle.v1":
            authority_errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json schema mismatch")
        if one_shot_request_bundle.get("status") != "candidate-source-request-bundle":
            authority_errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json status mismatch")
        if one_shot_request_bundle.get("taskCount") != function_reconstruction_tasks.get("taskCount"):
            authority_errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json taskCount mismatch")
        if one_shot_request_bundle.get("semanticDecompilation") is not False:
            authority_errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json overclaims semantic decompilation")
        if one_shot_request_bundle_proof.get("path") != "ONE_SHOT_RECONSTRUCTION_BUNDLE.json":
            authority_errors.append("PACKAGE_PROOF.json oneShotReconstructionBundle path mismatch")
        if one_shot_request_bundle_proof.get("sha256") != one_shot_request_bundle_sha256:
            authority_errors.append("PACKAGE_PROOF.json oneShotReconstructionBundle hash mismatch")
        if one_shot_request_bundle_proof.get("taskCount") != function_reconstruction_tasks.get("taskCount"):
            authority_errors.append("PACKAGE_PROOF.json oneShotReconstructionBundle taskCount mismatch")
        bundle_request = one_shot_request_bundle.get("request")
        if not isinstance(bundle_request, dict) or bundle_request.get("sha256") != one_shot_request_json_sha256:
            authority_errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json request hash mismatch")
        bundle_template = one_shot_request_bundle.get("responseTemplate")
        if not isinstance(bundle_template, dict) or bundle_template.get("sha256") != one_shot_response_template_sha256:
            authority_errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json response template hash mismatch")
        elif bundle_template.get("jsonReplayReportShapes") != expected_json_replay_report_shapes():
            authority_errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json response template replay report shapes mismatch")
        bundle_artifacts = one_shot_request_bundle.get("sourceArtifacts")
        if not isinstance(bundle_artifacts, dict):
            authority_errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json has no sourceArtifacts object")
        else:
            expected_bundle_artifacts = {
                "functionReconstructionTasks": ("FUNCTION_RECONSTRUCTION_TASKS.json", function_reconstruction_tasks_sha256),
                "markdownRequest": ("ONE_SHOT_RECONSTRUCTION_REQUEST.md", one_shot_request_sha256),
                "candidateImporter": ("IMPORT_RECONSTRUCTION_CANDIDATES.py", one_shot_importer_sha256),
                "byteAccurateResponseExporter": (
                    "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py",
                    one_shot_byte_exporter_sha256,
                ),
                "jsonImporter": ("IMPORT_RECONSTRUCTION_RESPONSE_JSON.py", one_shot_json_importer_sha256),
                "jsonValidator": ("VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py", one_shot_json_validator_sha256),
                "receiptRefresher": ("REFRESH_RECONSTRUCTION_RECEIPTS.py", one_shot_receipt_refresher_sha256),
                "candidateReplay": ("REPLAY_RECONSTRUCTION_CANDIDATES.py", reconstruction_replay_sha256),
                "semanticAuthorityEvaluator": (
                    "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py",
                    semantic_authority_evaluator_sha256,
                ),
            }
            for artifact_name, (expected_path, expected_sha256) in expected_bundle_artifacts.items():
                artifact = bundle_artifacts.get(artifact_name)
                if not isinstance(artifact, dict):
                    authority_errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json missing {artifact_name} artifact")
                    continue
                if artifact.get("path") != expected_path:
                    authority_errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json {artifact_name} path mismatch")
                if artifact.get("sha256") != expected_sha256:
                    authority_errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json {artifact_name} hash mismatch")
        bundle_tasks = one_shot_request_bundle.get("tasks")
        if not isinstance(bundle_tasks, list):
            authority_errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json has no tasks list")
        elif len(bundle_tasks) != function_reconstruction_tasks.get("taskCount"):
            authority_errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json tasks length mismatch")
        authority_errors.extend(one_shot_bundle_evidence_errors)
    one_shot_importer = package_proof.get("oneShotCandidateImporter")
    if not isinstance(one_shot_importer, dict):
        authority_errors.append("PACKAGE_PROOF.json missing oneShotCandidateImporter")
    else:
        if one_shot_importer.get("path") != "IMPORT_RECONSTRUCTION_CANDIDATES.py":
            authority_errors.append("PACKAGE_PROOF.json oneShotCandidateImporter path mismatch")
        if one_shot_importer.get("sha256") != one_shot_importer_sha256:
            authority_errors.append("PACKAGE_PROOF.json oneShotCandidateImporter hash mismatch")
        if one_shot_importer.get("semanticDecompilation") is not False:
            authority_errors.append("PACKAGE_PROOF.json oneShotCandidateImporter overclaims semantic decompilation")
    one_shot_json_importer = package_proof.get("oneShotResponseJsonImporter")
    if not isinstance(one_shot_json_importer, dict):
        authority_errors.append("PACKAGE_PROOF.json missing oneShotResponseJsonImporter")
    else:
        if one_shot_json_importer.get("path") != "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py":
            authority_errors.append("PACKAGE_PROOF.json oneShotResponseJsonImporter path mismatch")
        if one_shot_json_importer.get("sha256") != one_shot_json_importer_sha256:
            authority_errors.append("PACKAGE_PROOF.json oneShotResponseJsonImporter hash mismatch")
        if one_shot_json_importer.get("semanticDecompilation") is not False:
            authority_errors.append("PACKAGE_PROOF.json oneShotResponseJsonImporter overclaims semantic decompilation")
    one_shot_json_validator = package_proof.get("oneShotResponseJsonValidator")
    if not isinstance(one_shot_json_validator, dict):
        authority_errors.append("PACKAGE_PROOF.json missing oneShotResponseJsonValidator")
    else:
        if one_shot_json_validator.get("path") != "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py":
            authority_errors.append("PACKAGE_PROOF.json oneShotResponseJsonValidator path mismatch")
        if one_shot_json_validator.get("sha256") != one_shot_json_validator_sha256:
            authority_errors.append("PACKAGE_PROOF.json oneShotResponseJsonValidator hash mismatch")
        if one_shot_json_validator.get("semanticDecompilation") is not False:
            authority_errors.append("PACKAGE_PROOF.json oneShotResponseJsonValidator overclaims semantic decompilation")
    one_shot_receipt_refresher = package_proof.get("oneShotReceiptRefresher")
    if not isinstance(one_shot_receipt_refresher, dict):
        authority_errors.append("PACKAGE_PROOF.json missing oneShotReceiptRefresher")
    else:
        if one_shot_receipt_refresher.get("path") != "REFRESH_RECONSTRUCTION_RECEIPTS.py":
            authority_errors.append("PACKAGE_PROOF.json oneShotReceiptRefresher path mismatch")
        if one_shot_receipt_refresher.get("sha256") != one_shot_receipt_refresher_sha256:
            authority_errors.append("PACKAGE_PROOF.json oneShotReceiptRefresher hash mismatch")
        if one_shot_receipt_refresher.get("semanticDecompilation") is not False:
            authority_errors.append("PACKAGE_PROOF.json oneShotReceiptRefresher overclaims semantic decompilation")
    proof_response_template = package_proof.get("oneShotResponseTemplate")
    if one_shot_response_template.get("schema") != "reconkit.one-shot-source-reconstruction-response-template.v1":
        authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json schema mismatch")
    if one_shot_response_template.get("status") != "empty-template":
        authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json status mismatch")
    if one_shot_response_template.get("semanticDecompilation") is not False:
        authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json overclaims semantic decompilation")
    if one_shot_response_template.get("taskCount") != function_reconstruction_tasks.get("taskCount"):
        authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json taskCount mismatch")
    template_candidates = one_shot_response_template.get("expectedCandidates")
    task_rows = function_reconstruction_tasks.get("tasks")
    if isinstance(template_candidates, list) and isinstance(task_rows, list):
        expected_paths = [f"{task.get('path')}/candidate.c" for task in task_rows if isinstance(task, dict)]
        template_paths = [row.get("path") for row in template_candidates if isinstance(row, dict)]
        if template_paths != expected_paths:
            authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json expected candidate paths mismatch")
    else:
        authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json has no expectedCandidates list")
    if one_shot_response_template.get("oneShotReconstructionRequestSha256") != one_shot_request_sha256:
        authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json request hash mismatch")
    if one_shot_response_template.get("oneShotReconstructionRequestJsonSha256") != one_shot_request_json_sha256:
        authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json request JSON hash mismatch")
    if one_shot_response_template.get("functionReconstructionTasksSha256") != function_reconstruction_tasks_sha256:
        authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json tasks hash mismatch")
    if one_shot_response_template.get("importerSha256") != one_shot_importer_sha256:
        authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json importer hash mismatch")
    if one_shot_response_template.get("jsonImporterSha256") != one_shot_json_importer_sha256:
        authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json JSON importer hash mismatch")
    if one_shot_response_template.get("jsonValidatorSha256") != one_shot_json_validator_sha256:
        authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json JSON validator hash mismatch")
    if one_shot_response_template.get("exporterSha256") != one_shot_response_template_exporter_sha256:
        authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json exporter hash mismatch")
    response_shape = one_shot_response_template.get("jsonResponseShape")
    if not isinstance(response_shape, dict) or response_shape.get("schema") != "reconkit.one-shot-source-reconstruction-response.v1":
        authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json JSON response shape mismatch")
    elif "candidates" in response_shape or not isinstance(response_shape.get("files"), dict):
        authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json JSON response shape must use files only")
    if one_shot_response_template.get("jsonImportCommandWithBuildCommand") != "./IMPORT_RECONSTRUCTION_RESPONSE_JSON.py --response-json <response.json> --allow-build-command":
        authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json build-command JSON import command mismatch")
    if one_shot_response_template.get("jsonValidateCommandWithBuildCommand") != "./VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json <response.json> --allow-build-command":
        authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json build-command JSON validate command mismatch")
    structured_response_shape = one_shot_response_template.get("jsonStructuredResponseShape")
    if not isinstance(structured_response_shape, dict) or structured_response_shape.get("schema") != "reconkit.one-shot-source-reconstruction-response.v1":
        authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json structured JSON response shape mismatch")
    else:
        structured_candidates = structured_response_shape.get("candidates")
        if not isinstance(structured_candidates, list) or not structured_candidates:
            authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json structured JSON response shape has no candidates")
        else:
            build = structured_candidates[0].get("build") if isinstance(structured_candidates[0], dict) else None
            if not isinstance(build, dict) or build.get("command") != "optional custom command that writes $CANDIDATE_OUTPUT; requires --allow-build-command":
                authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json structured JSON response build command mismatch")
    if one_shot_response_template.get("jsonReplayReportShapes") != expected_json_replay_report_shapes():
        authority_errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json JSON replay report shapes mismatch")
    if proof_response_template != one_shot_response_template:
        authority_errors.append("PACKAGE_PROOF.json oneShotResponseTemplate mismatch")
    one_shot_response_template_exporter = package_proof.get("oneShotResponseTemplateExporter")
    if not isinstance(one_shot_response_template_exporter, dict):
        authority_errors.append("PACKAGE_PROOF.json missing oneShotResponseTemplateExporter")
    else:
        if one_shot_response_template_exporter.get("path") != "EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py":
            authority_errors.append("PACKAGE_PROOF.json oneShotResponseTemplateExporter path mismatch")
        if one_shot_response_template_exporter.get("sha256") != one_shot_response_template_exporter_sha256:
            authority_errors.append("PACKAGE_PROOF.json oneShotResponseTemplateExporter hash mismatch")
        if one_shot_response_template_exporter.get("semanticDecompilation") is not False:
            authority_errors.append("PACKAGE_PROOF.json oneShotResponseTemplateExporter overclaims semantic decompilation")
    one_shot_byte_exporter = package_proof.get("oneShotByteAccurateResponseExporter")
    if not isinstance(one_shot_byte_exporter, dict):
        authority_errors.append("PACKAGE_PROOF.json missing oneShotByteAccurateResponseExporter")
    else:
        if one_shot_byte_exporter.get("path") != "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py":
            authority_errors.append("PACKAGE_PROOF.json oneShotByteAccurateResponseExporter path mismatch")
        if one_shot_byte_exporter.get("sha256") != one_shot_byte_exporter_sha256:
            authority_errors.append("PACKAGE_PROOF.json oneShotByteAccurateResponseExporter hash mismatch")
        if one_shot_byte_exporter.get("semanticDecompilation") is not False:
            authority_errors.append("PACKAGE_PROOF.json oneShotByteAccurateResponseExporter overclaims semantic decompilation")
    one_shot_byte_prover = package_proof.get("oneShotByteAccurateResponseProver")
    if not isinstance(one_shot_byte_prover, dict):
        authority_errors.append("PACKAGE_PROOF.json missing oneShotByteAccurateResponseProver")
    else:
        if one_shot_byte_prover.get("path") != "PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py":
            authority_errors.append("PACKAGE_PROOF.json oneShotByteAccurateResponseProver path mismatch")
        if one_shot_byte_prover.get("sha256") != one_shot_byte_prover_sha256:
            authority_errors.append("PACKAGE_PROOF.json oneShotByteAccurateResponseProver hash mismatch")
        if one_shot_byte_prover.get("semanticDecompilation") is not False:
            authority_errors.append("PACKAGE_PROOF.json oneShotByteAccurateResponseProver overclaims semantic decompilation")
    if source_roles.get("schema") != "reconkit.one-shot-source-roles.v1":
        authority_errors.append("SOURCE_ROLES.json schema mismatch")
    if source_roles.get("status") != claims.get("status"):
        authority_errors.append("SOURCE_ROLES.json status mismatch")
    if source_roles.get("authorityClass") != "byte-authoritative-source":
        authority_errors.append("SOURCE_ROLES.json authorityClass is not byte-authoritative-source")
    if source_roles.get("accuracyClass") != "byte-exact":
        authority_errors.append("SOURCE_ROLES.json accuracyClass is not byte-exact")
    if source_roles.get("semanticDecompilation") is not False:
        authority_errors.append("SOURCE_ROLES.json overclaims semantic decompilation")
    roles = source_roles.get("roles")
    role_by_path = {role.get("path"): role for role in roles if isinstance(role, dict)} if isinstance(roles, list) else {}
    if not isinstance(roles, list) or len(roles) < 2:
        authority_errors.append("SOURCE_ROLES.json has no roles list")
    expected_roles = {
        "full-binary.S": "generated-assembler-byte-source",
        "full-binary.c": "generated-c-byte-emitter",
    }
    for rel, expected_role in expected_roles.items():
        role = role_by_path.get(rel)
        if not isinstance(role, dict):
            authority_errors.append(f"SOURCE_ROLES.json missing role: {rel}")
            continue
        if role.get("role") != expected_role:
            authority_errors.append(f"SOURCE_ROLES.json role mismatch: {rel}")
        if role.get("accuracyClass") != "byte-exact":
            authority_errors.append(f"SOURCE_ROLES.json accuracyClass is not byte-exact: {rel}")
        if role.get("semanticDecompilation") is not False:
            authority_errors.append(f"SOURCE_ROLES.json overclaims semantic decompilation: {rel}")
    if package_proof.get("sourceRoles") != source_roles.get("roles"):
        authority_errors.append("PACKAGE_PROOF.json sourceRoles mismatch")
    if semantic_readiness.get("schema") != "reconkit.one-shot-source-semantic-readiness.v1":
        authority_errors.append("SEMANTIC_READINESS.json schema mismatch")
    expected_semantic_status = "ready" if int(semantic_readiness.get("semanticSourceBundlesVerified") or 0) > 0 else "not-ready"
    if semantic_readiness.get("status") != expected_semantic_status:
        authority_errors.append("SEMANTIC_READINESS.json status mismatch")
    if semantic_readiness.get("authorityClass") != claims.get("authorityClass"):
        authority_errors.append("SEMANTIC_READINESS.json authorityClass mismatch")
    if semantic_readiness.get("accuracyClass") != claims.get("accuracyClass"):
        authority_errors.append("SEMANTIC_READINESS.json accuracyClass mismatch")
    if semantic_readiness.get("currentClaim") != "byte-exact-reproduction":
        authority_errors.append("SEMANTIC_READINESS.json currentClaim mismatch")
    if semantic_readiness.get("targetClaim") != "semantic-source-recovery":
        authority_errors.append("SEMANTIC_READINESS.json targetClaim mismatch")
    if semantic_readiness.get("semanticDecompilation") is not False:
        authority_errors.append("SEMANTIC_READINESS.json overclaims semantic decompilation")
    if semantic_readiness.get("functionBoundaryCandidateStatus") != boundary_candidates.get("status"):
        authority_errors.append("SEMANTIC_READINESS.json functionBoundaryCandidateStatus mismatch")
    if semantic_readiness.get("functionBoundaryCandidateCount") != boundary_candidates.get("candidateCount"):
        authority_errors.append("SEMANTIC_READINESS.json functionBoundaryCandidateCount mismatch")
    if semantic_readiness.get("functionByteSliceStatus") != function_byte_slices.get("status"):
        authority_errors.append("SEMANTIC_READINESS.json functionByteSliceStatus mismatch")
    if semantic_readiness.get("functionByteSliceCount") != function_byte_slices.get("sliceCount"):
        authority_errors.append("SEMANTIC_READINESS.json functionByteSliceCount mismatch")
    if semantic_readiness.get("functionSliceSourceStatus") != function_slice_sources.get("status"):
        authority_errors.append("SEMANTIC_READINESS.json functionSliceSourceStatus mismatch")
    if semantic_readiness.get("functionSliceSourceCount") != function_slice_sources.get("sourceCount"):
        authority_errors.append("SEMANTIC_READINESS.json functionSliceSourceCount mismatch")
    if semantic_readiness.get("functionReconstructionTaskStatus") != function_reconstruction_tasks.get("status"):
        authority_errors.append("SEMANTIC_READINESS.json functionReconstructionTaskStatus mismatch")
    if semantic_readiness.get("functionReconstructionTaskCount") != function_reconstruction_tasks.get("taskCount"):
        authority_errors.append("SEMANTIC_READINESS.json functionReconstructionTaskCount mismatch")
    if semantic_readiness.get("sourceRoles") != source_roles.get("roles"):
        authority_errors.append("SEMANTIC_READINESS.json sourceRoles mismatch")
    missing_semantic = semantic_readiness.get("missingForSemanticAuthority")
    if expected_semantic_status == "not-ready" and (not isinstance(missing_semantic, list) or not missing_semantic):
        authority_errors.append("SEMANTIC_READINESS.json has no semantic blockers")
    if package_proof.get("semanticReadiness") != semantic_readiness:
        authority_errors.append("PACKAGE_PROOF.json semanticReadiness mismatch")
    authority_eval_all_candidates_matched = (
        function_reconstruction_results.get("status") == "matched"
        and int(function_reconstruction_tasks.get("taskCount") or 0) > 0
        and int(function_reconstruction_results.get("matchedCount") or 0)
        == int(function_reconstruction_tasks.get("taskCount") or 0)
        and int(function_reconstruction_results.get("failedCount") or 0) == 0
        and int(function_reconstruction_results.get("skippedCount") or 0) == 0
    )
    expected_authority_eval_status = (
        "ready"
        if (
            authority_eval_all_candidates_matched
            and semantic_readiness.get("status") == "ready"
            and not semantic_readiness.get("missingForSemanticAuthority")
        )
        else "not-ready"
    )
    if semantic_authority_evaluation.get("schema") != "reconkit.one-shot-source-semantic-authority-evaluation.v1":
        authority_errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json schema mismatch")
    if semantic_authority_evaluation.get("status") != expected_authority_eval_status:
        authority_errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json status mismatch")
    if semantic_authority_evaluation.get("semanticDecompilation") is not False:
        authority_errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json overclaims semantic decompilation")
    if semantic_authority_evaluation.get("candidateReplayStatus") != function_reconstruction_results.get("status"):
        authority_errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json candidate replay status mismatch")
    if semantic_authority_evaluation.get("taskCount") != int(function_reconstruction_tasks.get("taskCount") or 0):
        authority_errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json taskCount mismatch")
    if (
        semantic_authority_evaluation.get("matchedCount") != int(function_reconstruction_results.get("matchedCount") or 0)
        or semantic_authority_evaluation.get("failedCount") != int(function_reconstruction_results.get("failedCount") or 0)
        or semantic_authority_evaluation.get("skippedCount") != int(function_reconstruction_results.get("skippedCount") or 0)
    ):
        authority_errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json candidate counts mismatch")
    if semantic_authority_evaluation.get("allCandidatesMatched") is not authority_eval_all_candidates_matched:
        authority_errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json allCandidatesMatched mismatch")
    if semantic_authority_evaluation.get("semanticReadinessStatus") != semantic_readiness.get("status"):
        authority_errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json semanticReadinessStatus mismatch")
    if semantic_authority_evaluation.get("functionReconstructionTasksSha256") != function_reconstruction_tasks_sha256:
        authority_errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json task hash mismatch")
    if semantic_authority_evaluation.get("functionReconstructionCandidateResultsSha256") != function_reconstruction_results_sha256:
        authority_errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json candidate results hash mismatch")
    if semantic_authority_evaluation.get("semanticReadinessSha256") != semantic_readiness_sha256:
        authority_errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json readiness hash mismatch")
    if semantic_authority_evaluation.get("evaluatorScriptSha256") != semantic_authority_evaluator_sha256:
        authority_errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json evaluator hash mismatch")
    authority_eval_blockers = semantic_authority_evaluation.get("blockers")
    if expected_authority_eval_status == "not-ready" and not authority_eval_blockers:
        authority_errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json has no blockers")
    elif isinstance(authority_eval_blockers, list):
        authority_eval_blocker_ids = {
            item.get("id")
            for item in authority_eval_blockers
            if isinstance(item, dict)
        }
        expected_authority_eval_blocker_ids: set[str] = set()
        if (
            function_reconstruction_results.get("status") == "no-candidates"
            or int(function_reconstruction_results.get("skippedCount") or 0)
        ):
            expected_authority_eval_blocker_ids.add("missing-reconstruction-candidates")
        if (
            function_reconstruction_results.get("status") == "failed"
            or int(function_reconstruction_results.get("failedCount") or 0)
        ):
            expected_authority_eval_blocker_ids.add("candidate-replay-failures")
        if not authority_eval_all_candidates_matched:
            expected_authority_eval_blocker_ids.add("all-candidates-not-byte-identical")
        if semantic_readiness.get("missingForSemanticAuthority"):
            expected_authority_eval_blocker_ids.add("semantic-evidence-incomplete")
        missing_authority_eval_blockers = sorted(
            expected_authority_eval_blocker_ids - authority_eval_blocker_ids
        )
        if missing_authority_eval_blockers:
            authority_errors.append(
                "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json missing blockers: "
                + ", ".join(missing_authority_eval_blockers)
            )
        if expected_authority_eval_status == "ready" and authority_eval_blockers:
            authority_errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json ready status has blockers")
    if package_proof.get("semanticAuthorityEvaluation") != semantic_authority_evaluation:
        authority_errors.append("PACKAGE_PROOF.json semanticAuthorityEvaluation mismatch")
    semantic_authority_evaluator = package_proof.get("semanticAuthorityEvaluator")
    if not isinstance(semantic_authority_evaluator, dict):
        authority_errors.append("PACKAGE_PROOF.json missing semanticAuthorityEvaluator")
    else:
        if semantic_authority_evaluator.get("path") != "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py":
            authority_errors.append("PACKAGE_PROOF.json semanticAuthorityEvaluator path mismatch")
        if semantic_authority_evaluator.get("sha256") != semantic_authority_evaluator_sha256:
            authority_errors.append("PACKAGE_PROOF.json semanticAuthorityEvaluator hash mismatch")
        if semantic_authority_evaluator.get("semanticDecompilation") is not False:
            authority_errors.append("PACKAGE_PROOF.json semanticAuthorityEvaluator overclaims semantic decompilation")
    for candidate in candidates.get("candidates") if isinstance(candidates.get("candidates"), list) else []:
        if not isinstance(candidate, dict):
            continue
        role = role_by_path.get(candidate.get("path"))
        if not isinstance(role, dict):
            authority_errors.append(f"SOURCE_ROLES.json missing candidate role: {candidate.get('path')}")
        elif candidate.get("sourceRole") != role.get("role"):
            authority_errors.append(f"source candidate role mismatch: {candidate.get('path')}")
    if proof_commands.get("schema") != "reconkit.one-shot-source-proof-commands.v1":
        authority_errors.append("PROOF_COMMANDS.json schema mismatch")
    if proof_commands.get("status") != claims.get("status"):
        authority_errors.append("PROOF_COMMANDS.json status mismatch")
    if proof_commands.get("authorityClass") != claims.get("authorityClass"):
        authority_errors.append("PROOF_COMMANDS.json authorityClass mismatch")
    if proof_commands.get("accuracyClass") != claims.get("accuracyClass"):
        authority_errors.append("PROOF_COMMANDS.json accuracyClass mismatch")
    if proof_commands.get("semanticDecompilation") is not False:
        authority_errors.append("PROOF_COMMANDS.json overclaims semantic decompilation")
    if proof_commands.get("artifactLayers") != ["package-directory", "source-archive", "deliverable-bundle"]:
        authority_errors.append("PROOF_COMMANDS.json artifactLayers mismatch")
    expected_prerequisites = {
        "packageLocal": ["python3", "gcc", "objcopy"],
        "workspaceReplay": ["RECONKIT_WORKSPACE", "scripts/decomp-cli.sh"],
        "optionalOverrides": ["RECONKIT_ARCHIVE_PATH", "RECONKIT_BUNDLE_PATH"],
    }
    if proof_commands.get("prerequisites") != expected_prerequisites:
        authority_errors.append("PROOF_COMMANDS.json prerequisites mismatch")
    entrypoints = proof_commands.get("entrypoints")
    if not isinstance(entrypoints, dict):
        authority_errors.append("PROOF_COMMANDS.json has no entrypoints object")
    else:
        for key in (
            "packageLocal",
            "strictPackageValidation",
            "sourceArchiveValidation",
            "portableBundleReplay",
            "byteAccurateResponseProof",
            "responseJsonPreflight",
            "responseJsonImport",
            "responseJsonPreflightWithBuildCommand",
            "responseJsonImportWithBuildCommand",
            "helper",
        ):
            value = entrypoints.get(key)
            if not isinstance(value, list) or not value:
                authority_errors.append(f"PROOF_COMMANDS.json missing entrypoint group: {key}")
    expected_success = {
        "packageLocal": "ONE_SHOT_SOURCE_PACKAGE_OK",
        "strictPackageValidation": "Status: `valid`",
        "sourceArchiveValidation": "Status: `valid`",
        "portableBundleReplay": "Status: `matched`",
        "byteAccurateResponseProof": "BYTE_ACCURATE_RECONSTRUCTION_RESPONSE_PROOF_OK",
        "responseJsonPreflight": "status valid, partial, or valid-with-extra depending on supplied flags",
        "responseJsonImport": "candidate replay matched, partial, failed, or no-candidates depending on supplied response",
        "responseJsonPreflightWithBuildCommand": "same as responseJsonPreflight; permits candidates[].build.command",
        "responseJsonImportWithBuildCommand": "same as responseJsonImport; permits candidates[].build.command",
        "bundleDeliverablePhase": "Deliverable phase: `pre-bundle-index`",
    }
    if proof_commands.get("expectedSuccess") != expected_success:
        authority_errors.append("PROOF_COMMANDS.json expectedSuccess mismatch")
    authority_summary = {
        "schema": "reconkit.one-shot-source-authority-summary.v1",
        "status": claims.get("status"),
        "authorityClass": claims.get("authorityClass"),
        "accuracyClass": claims.get("accuracyClass"),
        "authorityContractStatus": "passed" if not authority_errors else "failed",
        "authorityGateStatus": gates.get("status"),
        "sourceCandidateStatus": candidates.get("status"),
        "packageProofStatus": package_proof.get("status"),
        "contentIdentity": content_identity,
        "semanticDecompilation": proven.get("semanticDecompilation"),
        "claimBoundary": package_proof.get("claimBoundary"),
    }
    if not package_authority_summary:
        authority_errors.append("AUTHORITY_SUMMARY.json is missing")
    elif package_authority_summary != authority_summary:
        authority_errors.append("AUTHORITY_SUMMARY.json does not match replayed authority summary")
        authority_summary = package_authority_summary
    ok = (
        proc.returncode == 0
        and "ONE_SHOT_SOURCE_PACKAGE_OK" in proc.stdout
        and archive_sha_matches
        and content_identity_matches
        and not authority_errors
    )
    return {
        "schema": "reconkit.one-shot-source-archive-verify.v1",
        "archive": str(archive_path),
        "archiveSha256": archive_sha,
        "expectedArchiveSha256": expect_archive_sha256,
        "archiveSha256Matches": archive_sha_matches,
        "packageRoot": root,
        "status": "matched" if ok else "failed",
        "ok": ok,
        "claimStatus": claims.get("status"),
        "authorityClass": claims.get("authorityClass"),
        "accuracyClass": claims.get("accuracyClass"),
        "authorityContractStatus": "passed" if not authority_errors else "failed",
        "authorityContractErrors": authority_errors,
        "authoritySummary": authority_summary,
        "authoritySummarySha256": authority_summary_sha256,
        "proven": claims.get("proven"),
        "notProven": claims.get("notProven"),
        "contentIdentity": content_identity,
        "expectedContentIdentity": expect_content_identity,
        "contentIdentityMatches": content_identity_matches,
        "contentIdentityScope": content.get("identityScope") or claims.get("contentIdentityScope"),
        "replay": {
            "verifier": "VERIFY.py",
            "packageRoot": root,
            "status": "matched" if proc.returncode == 0 and "ONE_SHOT_SOURCE_PACKAGE_OK" in proc.stdout else "failed",
            "stdoutMarker": "ONE_SHOT_SOURCE_PACKAGE_OK",
            "archivePinned": archive_sha_matches,
            "contentPinned": content_identity_matches,
        },
        "authorityGateStatus": gates.get("status"),
        "authorityGates": gates.get("gates"),
        "packageProofStatus": package_proof.get("status"),
        "packageProofReplayEntrypoints": package_proof.get("replayEntrypoints"),
        "binaryEvidenceStatus": binary_evidence.get("status") if binary_evidence else None,
        "binaryEvidence": binary_evidence if binary_evidence else None,
        "functionBoundaryCandidateStatus": boundary_candidates.get("status") if boundary_candidates else None,
        "functionBoundaryCandidateCount": boundary_candidates.get("candidateCount") if boundary_candidates else None,
        "functionBoundaryCandidates": boundary_candidates if boundary_candidates else None,
        "functionByteSliceStatus": function_byte_slices.get("status") if function_byte_slices else None,
        "functionByteSliceCount": function_byte_slices.get("sliceCount") if function_byte_slices else None,
        "functionByteSlices": function_byte_slices if function_byte_slices else None,
        "functionSliceSourceStatus": function_slice_sources.get("status") if function_slice_sources else None,
        "functionSliceSourceCount": function_slice_sources.get("sourceCount") if function_slice_sources else None,
        "functionSliceSources": function_slice_sources if function_slice_sources else None,
        "functionReconstructionTaskStatus": function_reconstruction_tasks.get("status") if function_reconstruction_tasks else None,
        "functionReconstructionTaskCount": function_reconstruction_tasks.get("taskCount") if function_reconstruction_tasks else None,
        "functionReconstructionTasks": function_reconstruction_tasks if function_reconstruction_tasks else None,
        "functionReconstructionCandidateResultStatus": function_reconstruction_results.get("status") if function_reconstruction_results else None,
        "functionReconstructionCandidateResults": function_reconstruction_results if function_reconstruction_results else None,
        "oneShotReconstructionRequest": one_shot_request if isinstance(one_shot_request, dict) else None,
        "oneShotReconstructionRequestJson": one_shot_request_json_proof
        if isinstance(one_shot_request_json_proof, dict)
        else None,
        "oneShotReconstructionBundle": one_shot_request_bundle_proof
        if isinstance(one_shot_request_bundle_proof, dict)
        else None,
        "oneShotCandidateImporter": one_shot_importer if isinstance(one_shot_importer, dict) else None,
        "oneShotResponseJsonImporter": one_shot_json_importer if isinstance(one_shot_json_importer, dict) else None,
        "oneShotResponseJsonValidator": one_shot_json_validator if isinstance(one_shot_json_validator, dict) else None,
        "oneShotReceiptRefresher": one_shot_receipt_refresher if isinstance(one_shot_receipt_refresher, dict) else None,
        "oneShotResponseTemplate": one_shot_response_template if one_shot_response_template else None,
        "oneShotResponseTemplateSha256": one_shot_response_template_sha256,
        "oneShotResponseTemplateExporter": one_shot_response_template_exporter
        if isinstance(one_shot_response_template_exporter, dict)
        else None,
        "oneShotByteAccurateResponseExporter": one_shot_byte_exporter
        if isinstance(one_shot_byte_exporter, dict)
        else None,
        "oneShotByteAccurateResponseProver": one_shot_byte_prover
        if isinstance(one_shot_byte_prover, dict)
        else None,
        "sourceRolesStatus": "recorded" if source_roles else None,
        "sourceRoles": source_roles.get("roles") if source_roles else None,
        "semanticReadinessStatus": semantic_readiness.get("status") if semantic_readiness else None,
        "semanticReadiness": semantic_readiness if semantic_readiness else None,
        "semanticAuthorityEvaluationStatus": semantic_authority_evaluation.get("status")
        if semantic_authority_evaluation
        else None,
        "semanticAuthorityEvaluation": semantic_authority_evaluation if semantic_authority_evaluation else None,
        "semanticAuthorityEvaluator": semantic_authority_evaluator
        if isinstance(semantic_authority_evaluator, dict)
        else None,
        "toolchainProvenanceStatus": toolchain.get("status"),
        "toolchainTools": toolchain.get("tools"),
        "proofCommandsStatus": "recorded" if proof_commands else None,
        "proofCommands": {
            "schema": proof_commands.get("schema"),
            "artifactLayers": proof_commands.get("artifactLayers"),
            "entrypoints": proof_commands.get("entrypoints"),
            "prerequisites": proof_commands.get("prerequisites"),
            "expectedSuccess": proof_commands.get("expectedSuccess"),
        }
        if proof_commands
        else None,
        "sourceCandidateStatus": candidates.get("status"),
        "sourceCandidates": candidates.get("candidates"),
        "candidateBuildRecipe": {
            "status": recipe.get("status"),
            "candidatePath": recipe.get("candidatePath"),
            "verificationMode": recipe.get("verificationMode"),
            "replayCommand": recipe.get("replayCommand"),
            "expectedOutput": recipe.get("expectedOutput"),
        }
        if recipe
        else None,
        "returnCode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--expect-archive-sha256", help="Fail unless the archive SHA256 matches this value.")
    parser.add_argument("--expect-content-identity", help="Fail unless the package contentIdentity matches this value.")
    parser.add_argument("--out", type=Path, help="Write the verification JSON report to this path.")
    parser.add_argument("--markdown", action="store_true", help="Print a concise Markdown report instead of JSON.")
    args = parser.parse_args()

    report = verify_archive(
        args.archive,
        args.timeout,
        expect_archive_sha256=args.expect_archive_sha256,
        expect_content_identity=args.expect_content_identity,
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.markdown:
        print("# One-Shot Source Archive Verification")
        print()
        print(f"Status: `{report['status']}`")
        print(f"Archive: `{report['archive']}`")
        print(f"Archive SHA256: `{report['archiveSha256']}`")
        if report.get("expectedArchiveSha256"):
            print(f"Archive SHA256 matches pin: `{str(report['archiveSha256Matches']).lower()}`")
        print(f"Package root: `{report['packageRoot']}`")
        print(f"Content identity: `{report.get('contentIdentity')}`")
        if report.get("expectedContentIdentity"):
            print(f"Content identity matches pin: `{str(report['contentIdentityMatches']).lower()}`")
        print(f"Claim status: `{report.get('claimStatus')}`")
        print(f"Authority class: `{report.get('authorityClass')}`")
        print(f"Accuracy class: `{report.get('accuracyClass')}`")
        print(f"Authority contract: `{report.get('authorityContractStatus')}`")
        print(f"Package proof: `{report.get('packageProofStatus')}`")
        print(f"Binary evidence: `{report.get('binaryEvidenceStatus')}`")
        print(f"Function boundary candidates: `{report.get('functionBoundaryCandidateStatus')}`")
        print(f"Function byte slices: `{report.get('functionByteSliceStatus')}`")
        print(f"Function slice sources: `{report.get('functionSliceSourceStatus')}`")
        print(f"Function reconstruction tasks: `{report.get('functionReconstructionTaskStatus')}`")
        print(f"Source roles: `{report.get('sourceRolesStatus')}`")
        print(f"Semantic readiness: `{report.get('semanticReadinessStatus')}`")
        print(f"Semantic authority evaluation: `{report.get('semanticAuthorityEvaluationStatus')}`")
        print(f"Toolchain provenance: `{report.get('toolchainProvenanceStatus')}`")
        replay = report.get("replay") if isinstance(report.get("replay"), dict) else {}
        print(f"Replay verifier: `{replay.get('verifier')}`")
        print(f"Replay status: `{replay.get('status')}`")
        print()
        errors = report.get("authorityContractErrors") if isinstance(report.get("authorityContractErrors"), list) else []
        if errors:
            print("## Authority contract errors")
            for error in errors:
                print(f"- {error}")
            print()
        print("## Proven")
        proven = report.get("proven") if isinstance(report.get("proven"), dict) else {}
        for key, value in sorted(proven.items()):
            if value is None:
                continue
            print(f"- `{key}`: `{str(value).lower()}`")
        candidates = report.get("sourceCandidates") if isinstance(report.get("sourceCandidates"), list) else []
        if candidates:
            print()
            print("## Verified source candidates")
            for item in candidates:
                if isinstance(item, dict):
                    print(
                        f"- `{item.get('path')}`: `{item.get('language')}`, "
                        f"mode=`{item.get('verificationMode')}`, byteIdentical=`{str(item.get('byteIdentical')).lower()}`"
                    )
        roles = report.get("sourceRoles") if isinstance(report.get("sourceRoles"), list) else []
        if roles:
            print()
            print("## Source roles")
            for item in roles:
                if isinstance(item, dict):
                    print(
                        f"- `{item.get('path')}`: role=`{item.get('role')}`, "
                        f"kind=`{item.get('sourceKind')}`, semantic=`{item.get('semanticStatus')}`"
                    )
        recipe = report.get("candidateBuildRecipe") if isinstance(report.get("candidateBuildRecipe"), dict) else {}
        if recipe:
            print()
            print("## Candidate build recipe")
            print(f"- status: `{recipe.get('status')}`")
            print(f"- candidate: `{recipe.get('candidatePath')}`")
            print(f"- mode: `{recipe.get('verificationMode')}`")
        gates = report.get("authorityGates") if isinstance(report.get("authorityGates"), list) else []
        if gates:
            print()
            print("## Authority gates")
            for gate in gates:
                if isinstance(gate, dict):
                    print(f"- `{gate.get('id')}`: `{gate.get('status')}`")
        print()
        print("## Not proven")
        not_proven = report.get("notProven") if isinstance(report.get("notProven"), list) else []
        for item in not_proven:
            print(f"- {item}")
        print()
        print(f"Verifier output: `{str(report.get('stdout') or '').strip()}`")
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
