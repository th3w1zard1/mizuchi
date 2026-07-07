#!/usr/bin/env python3
"""Validate one-shot source package structure without rebuilding."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import tempfile
from pathlib import Path
from typing import Any


REQUIRED_FILES = [
    "AUTHORITATIVE_SOURCE.md",
    "AUTHORITY_SUMMARY.json",
    "AUTHORITY_GATES.json",
    "BINARY_EVIDENCE.json",
    "CLAIMS.json",
    "CONTENT_MANIFEST.json",
    "FUNCTION_BOUNDARY_CANDIDATES.json",
    "FUNCTION_BYTE_SLICES.json",
    "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json",
    "FUNCTION_RECONSTRUCTION_TASKS.json",
    "FUNCTION_SLICE_SOURCES.json",
    "REFRESH_RECONSTRUCTION_RECEIPTS.py",
    "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py",
    "IMPORT_RECONSTRUCTION_CANDIDATES.py",
    "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py",
    "Makefile",
    "ONE_SHOT_RECONSTRUCTION_BUNDLE.json",
    "ONE_SHOT_RECONSTRUCTION_REQUEST.json",
    "ONE_SHOT_RECONSTRUCTION_REQUEST.md",
    "PACKAGE_PROOF.json",
    "PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py",
    "README.md",
    "REPLAY_RECONSTRUCTION_CANDIDATES.py",
    "RECONSTRUCTION_RESPONSE_TEMPLATE.json",
    "SEMANTIC_READINESS.json",
    "SHA256SUMS",
    "SOURCE_INDEX.json",
    "SOURCE_ROLES.json",
    "TOOLCHAIN_PROVENANCE.json",
    "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py",
    "VERIFIED_SOURCE_CANDIDATES.json",
    "VERIFY.py",
    "VERIFY.sh",
    "binary-source-roundtrip.json",
    "c-source-roundtrip.json",
    "full-binary.S",
    "full-binary.c",
    "one-shot-source-receipt.json",
    "original.bin",
    "package-manifest.json",
    "source-authority-report.json",
]

PACKAGE_MANIFEST_LISTED_FILES = [name for name in REQUIRED_FILES if name != "package-manifest.json"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise SystemExit(f"missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"unexpected JSON root in {path}")
    return data


def expected_json_replay_report_shapes() -> dict[str, Any]:
    return {
        "preflight": {
            "schema": "mizuchi.one-shot-source-reconstruction-json-preflight.v1",
            "buildOverrideCount": "number of response candidate paths with build overrides",
            "buildOverridePaths": ["all response candidate paths with build overrides"],
            "buildOverrideExpectedPaths": ["expected candidate paths with build overrides"],
            "buildOverrideExtraPaths": ["extra candidate paths with build overrides"],
        },
        "import": {
            "schema": "mizuchi.one-shot-source-reconstruction-json-import.v1",
            "buildOverrideCount": "number of response candidate paths with build overrides, including extras",
            "buildOverridePaths": ["all response candidate paths with build overrides"],
            "buildOverrideExpectedPaths": ["importable expected candidate paths with build overrides"],
            "buildOverrideExtraPaths": ["extra candidate paths with build overrides that were not imported"],
        },
    }


def resolve_recorded_path(value: object, base: Path) -> Path | None:
    if isinstance(value, dict):
        rel = value.get("relativePath")
        if isinstance(rel, str) and rel.strip():
            return base / rel
        value = value.get("path")
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    return path if path.is_absolute() else base / path


def validate_sha256sums(package: Path) -> list[str]:
    errors: list[str] = []
    path = package / "SHA256SUMS"
    if not path.exists():
        return ["missing SHA256SUMS"]
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            errors.append(f"invalid SHA256SUMS line: {line}")
            continue
        expected, rel = parts[0], parts[1].strip()
        rel_path = Path(rel)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            errors.append(f"unsafe SHA256SUMS path: {rel}")
            continue
        target = package / rel_path
        if not target.exists():
            errors.append(f"SHA256SUMS file missing: {rel}")
            continue
        actual = sha256_file(target)
        if actual != expected:
            errors.append(f"SHA256SUMS mismatch: {rel}")
    return errors


def validate_package_manifest(package: Path) -> list[str]:
    errors: list[str] = []
    manifest = read_json(package / "package-manifest.json")
    if manifest.get("schema") != "mizuchi.one-shot-source-package-manifest.v1":
        errors.append("package-manifest.json schema mismatch")
    files = manifest.get("files")
    if not isinstance(files, list):
        return errors + ["package-manifest.json has no files list"]
    seen = set()
    for item in files:
        if not isinstance(item, dict):
            errors.append("package-manifest.json contains non-object file row")
            continue
        rel = str(item.get("path") or "")
        seen.add(rel)
        target = package / rel
        if not rel or not target.exists():
            errors.append(f"package manifest file missing: {rel}")
            continue
        if target.stat().st_size != item.get("size"):
            errors.append(f"package manifest size mismatch: {rel}")
        if sha256_file(target) != item.get("sha256"):
            errors.append(f"package manifest hash mismatch: {rel}")
    for required in PACKAGE_MANIFEST_LISTED_FILES:
        if required not in seen:
            errors.append(f"package manifest missing required file: {required}")
    return errors


def validate_content_manifest(package: Path) -> list[str]:
    errors: list[str] = []
    manifest = read_json(package / "CONTENT_MANIFEST.json")
    if manifest.get("schema") != "mizuchi.one-shot-source-content-manifest.v1":
        errors.append("CONTENT_MANIFEST.json schema mismatch")
    files = manifest.get("files")
    if not isinstance(files, list):
        return errors + ["CONTENT_MANIFEST.json has no files list"]
    digest = hashlib.sha256()
    for item in files:
        if not isinstance(item, dict):
            errors.append("CONTENT_MANIFEST.json contains non-object row")
            continue
        rel = str(item.get("path") or "")
        target = package / rel
        if not rel or not target.exists():
            errors.append(f"content manifest file missing: {rel}")
            continue
        actual_sha = sha256_file(target)
        actual_size = target.stat().st_size
        if actual_sha != item.get("sha256"):
            errors.append(f"content manifest hash mismatch: {rel}")
        if actual_size != item.get("size"):
            errors.append(f"content manifest size mismatch: {rel}")
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(actual_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(actual_sha.encode("ascii"))
        digest.update(b"\n")
    if digest.hexdigest() != manifest.get("contentIdentity"):
        errors.append("contentIdentity mismatch")
    return errors


def validate_claims(package: Path) -> list[str]:
    errors: list[str] = []
    claims = read_json(package / "CLAIMS.json")
    content = read_json(package / "CONTENT_MANIFEST.json")
    if claims.get("schema") != "mizuchi.one-shot-source-claims.v1":
        errors.append("CLAIMS.json schema mismatch")
    if claims.get("status") != "authoritative":
        errors.append("CLAIMS.json status is not authoritative")
    if claims.get("authorityClass") != "byte-authoritative-source":
        errors.append("CLAIMS.json authorityClass is not byte-authoritative-source")
    if claims.get("accuracyClass") != "byte-exact":
        errors.append("CLAIMS.json accuracyClass is not byte-exact")
    if claims.get("contentIdentity") != content.get("contentIdentity"):
        errors.append("CLAIMS.json contentIdentity mismatch")
    source_accuracy = claims.get("sourceAccuracy")
    if not isinstance(source_accuracy, dict):
        errors.append("CLAIMS.json has no sourceAccuracy object")
    else:
        for key in ("assembler", "cByteEmitter"):
            item = source_accuracy.get(key)
            if not isinstance(item, dict) or item.get("byteIdentical") is not True:
                errors.append(f"CLAIMS.json sourceAccuracy does not prove {key}")
    proven = claims.get("proven")
    if not isinstance(proven, dict):
        return errors + ["CLAIMS.json has no proven object"]
    for key in ("selfContainedPackage", "originalBytesIncluded", "assemblerSourceRebuildsOriginalBytes", "cSourceEmitsOriginalBytes"):
        if proven.get(key) is not True:
            errors.append(f"CLAIMS.json does not prove {key}")
    if proven.get("semanticDecompilation") is not False:
        errors.append("CLAIMS.json claims semantic decompilation")
    return errors


def validate_authority_gates(package: Path) -> list[str]:
    errors: list[str] = []
    gates_doc = read_json(package / "AUTHORITY_GATES.json")
    claims = read_json(package / "CLAIMS.json")
    content = read_json(package / "CONTENT_MANIFEST.json")
    receipt = read_json(package / "one-shot-source-receipt.json")
    if gates_doc.get("schema") != "mizuchi.one-shot-source-authority-gates.v1":
        errors.append("AUTHORITY_GATES.json schema mismatch")
    if gates_doc.get("status") != "passed":
        errors.append("AUTHORITY_GATES.json status is not passed")
    if gates_doc.get("authorityClass") != "byte-authoritative-source":
        errors.append("AUTHORITY_GATES.json authorityClass is not byte-authoritative-source")
    if gates_doc.get("accuracyClass") != "byte-exact":
        errors.append("AUTHORITY_GATES.json accuracyClass is not byte-exact")
    if gates_doc.get("contentIdentity") != content.get("contentIdentity"):
        errors.append("AUTHORITY_GATES.json contentIdentity mismatch")
    if gates_doc.get("originalSha256") != receipt.get("originalSha256"):
        errors.append("AUTHORITY_GATES.json originalSha256 mismatch")
    if gates_doc.get("semanticDecompilation") is not False:
        errors.append("AUTHORITY_GATES.json claims semantic decompilation")
    gates = gates_doc.get("gates")
    if not isinstance(gates, list):
        return errors + ["AUTHORITY_GATES.json has no gates list"]
    by_id = {item.get("id"): item for item in gates if isinstance(item, dict)}
    required = {
        "self-contained-original-bytes",
        "assembler-rebuild-byte-identical",
        "c-emitter-byte-identical",
        "package-local-verifier",
        "stable-content-identity",
        "semantic-decompilation-boundary",
    }
    for gate_id in sorted(required):
        gate = by_id.get(gate_id)
        if not isinstance(gate, dict):
            errors.append(f"AUTHORITY_GATES.json missing gate: {gate_id}")
            continue
        if gate.get("status") != "passed":
            errors.append(f"AUTHORITY_GATES.json gate not passed: {gate_id}")
        evidence = gate.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"AUTHORITY_GATES.json gate has no evidence: {gate_id}")
            continue
        for rel in evidence:
            if not isinstance(rel, str) or not (package / rel).exists():
                errors.append(f"AUTHORITY_GATES.json evidence missing for {gate_id}: {rel}")
    proven = claims.get("proven") if isinstance(claims.get("proven"), dict) else {}
    if proven.get("packageLocalVerifierPassedAtGeneration") is not True:
        errors.append("CLAIMS.json packageLocalVerifierPassedAtGeneration is not true")
    return errors


def validate_authority_summary(
    package: Path,
    check_external_bundle: bool = True,
    expected_deliverable_phase: str = "final-package-index",
) -> list[str]:
    errors: list[str] = []
    summary = read_json(package / "AUTHORITY_SUMMARY.json")
    claims = read_json(package / "CLAIMS.json")
    content = read_json(package / "CONTENT_MANIFEST.json")
    gates = read_json(package / "AUTHORITY_GATES.json")
    candidates = read_json(package / "VERIFIED_SOURCE_CANDIDATES.json")
    binary_evidence = read_json(package / "BINARY_EVIDENCE.json")
    boundary_candidates = read_json(package / "FUNCTION_BOUNDARY_CANDIDATES.json")
    function_byte_slices = read_json(package / "FUNCTION_BYTE_SLICES.json")
    function_slice_sources = read_json(package / "FUNCTION_SLICE_SOURCES.json")
    source_roles = read_json(package / "SOURCE_ROLES.json")
    semantic_readiness = read_json(package / "SEMANTIC_READINESS.json")
    proof = read_json(package / "PACKAGE_PROOF.json")
    proof_commands = read_json(package / "PROOF_COMMANDS.json")
    expected = {
        "schema": "mizuchi.one-shot-source-authority-summary.v1",
        "status": claims.get("status"),
        "authorityClass": claims.get("authorityClass"),
        "accuracyClass": claims.get("accuracyClass"),
        "authorityContractStatus": "passed" if gates.get("status") == "passed" else "failed",
        "authorityGateStatus": gates.get("status"),
        "sourceCandidateStatus": candidates.get("status"),
        "packageProofStatus": proof.get("status"),
        "contentIdentity": content.get("contentIdentity"),
        "semanticDecompilation": False,
    }
    for key, value in expected.items():
        if summary.get(key) != value:
            errors.append(f"AUTHORITY_SUMMARY.json mismatch for {key}")
    if summary.get("claimBoundary") != proof.get("claimBoundary"):
        errors.append("AUTHORITY_SUMMARY.json claimBoundary mismatch")
    if proof_commands.get("schema") != "mizuchi.one-shot-source-proof-commands.v1":
        errors.append("PROOF_COMMANDS.json schema mismatch")
    if proof_commands.get("status") != claims.get("status"):
        errors.append("PROOF_COMMANDS.json status mismatch")
    if proof_commands.get("authorityClass") != claims.get("authorityClass"):
        errors.append("PROOF_COMMANDS.json authorityClass mismatch")
    if proof_commands.get("accuracyClass") != claims.get("accuracyClass"):
        errors.append("PROOF_COMMANDS.json accuracyClass mismatch")
    if proof_commands.get("semanticDecompilation") is not False:
        errors.append("PROOF_COMMANDS.json claims semantic decompilation")
    layers = proof_commands.get("artifactLayers")
    if layers != ["package-directory", "source-archive", "deliverable-bundle"]:
        errors.append("PROOF_COMMANDS.json artifactLayers mismatch")
    prerequisites = proof_commands.get("prerequisites")
    if not isinstance(prerequisites, dict):
        errors.append("PROOF_COMMANDS.json has no prerequisites object")
    else:
        expected_prerequisites = {
            "packageLocal": ["python3", "gcc", "objcopy"],
            "workspaceReplay": ["MIZUCHI_WORKSPACE", "scripts/decomp-cli.sh"],
            "optionalOverrides": ["MIZUCHI_ARCHIVE_PATH", "MIZUCHI_BUNDLE_PATH"],
        }
        for key, value in expected_prerequisites.items():
            if prerequisites.get(key) != value:
                errors.append(f"PROOF_COMMANDS.json prerequisites mismatch for {key}")
    entrypoints = proof_commands.get("entrypoints")
    if not isinstance(entrypoints, dict):
        errors.append("PROOF_COMMANDS.json has no entrypoints object")
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
                errors.append(f"PROOF_COMMANDS.json missing entrypoint group: {key}")
    expected_success = proof_commands.get("expectedSuccess")
    if not isinstance(expected_success, dict):
        errors.append("PROOF_COMMANDS.json has no expectedSuccess object")
    else:
        expected_success_values = {
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
        for key, value in expected_success_values.items():
            if expected_success.get(key) != value:
                errors.append(f"PROOF_COMMANDS.json expectedSuccess mismatch for {key}")
    deliverable_path = package / "receipts" / "deliverable.json"
    if deliverable_path.exists():
        deliverable = read_json(deliverable_path)
        deliverable_base = deliverable_path.parent
        expected_sha = sha256_file(package / "AUTHORITY_SUMMARY.json")
        allowed_deliverable_phases = {expected_deliverable_phase}
        if expected_deliverable_phase == "final-package-index":
            allowed_deliverable_phases.add("post-candidate-import-package-index")
        if deliverable.get("deliverablePhase") not in allowed_deliverable_phases:
            errors.append(f"deliverable phase is not one of {sorted(allowed_deliverable_phases)}")
        if deliverable.get("authoritySummary") != summary:
            errors.append("deliverable authoritySummary does not match AUTHORITY_SUMMARY.json")
        if deliverable.get("authoritySummarySha256") != expected_sha:
            errors.append("deliverable authoritySummarySha256 does not match AUTHORITY_SUMMARY.json")
        if deliverable.get("sourceRoles") != source_roles.get("roles"):
            errors.append("deliverable sourceRoles do not match SOURCE_ROLES.json")
        if deliverable.get("binaryEvidence") != binary_evidence:
            errors.append("deliverable binaryEvidence does not match BINARY_EVIDENCE.json")
        if deliverable.get("functionBoundaryCandidates") != boundary_candidates:
            errors.append("deliverable functionBoundaryCandidates do not match FUNCTION_BOUNDARY_CANDIDATES.json")
        if deliverable.get("functionByteSlices") != function_byte_slices:
            errors.append("deliverable functionByteSlices do not match FUNCTION_BYTE_SLICES.json")
        if deliverable.get("functionSliceSources") != function_slice_sources:
            errors.append("deliverable functionSliceSources do not match FUNCTION_SLICE_SOURCES.json")
        reconstruction_tasks = read_json(package / "FUNCTION_RECONSTRUCTION_TASKS.json")
        reconstruction_results = read_json(package / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json")
        if deliverable.get("functionReconstructionTasks") != reconstruction_tasks:
            errors.append("deliverable functionReconstructionTasks do not match FUNCTION_RECONSTRUCTION_TASKS.json")
        if deliverable.get("functionReconstructionCandidateResults") != reconstruction_results:
            errors.append("deliverable functionReconstructionCandidateResults do not match FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json")
        if deliverable.get("oneShotReconstructionRequest") != read_json(package / "PACKAGE_PROOF.json").get("oneShotReconstructionRequest"):
            errors.append("deliverable oneShotReconstructionRequest does not match PACKAGE_PROOF.json")
        if deliverable.get("oneShotCandidateImporter") != read_json(package / "PACKAGE_PROOF.json").get("oneShotCandidateImporter"):
            errors.append("deliverable oneShotCandidateImporter does not match PACKAGE_PROOF.json")
        if deliverable.get("oneShotReceiptRefresher") != read_json(package / "PACKAGE_PROOF.json").get("oneShotReceiptRefresher"):
            errors.append("deliverable oneShotReceiptRefresher does not match PACKAGE_PROOF.json")
        if deliverable.get("semanticReadiness") != semantic_readiness:
            errors.append("deliverable semanticReadiness does not match SEMANTIC_READINESS.json")
        result_path = package / "receipts" / "one-shot-source-result.json"
        if result_path.exists():
            result = read_json(result_path)
            complete_status = result.get("completeStatus")
            result_deliverable = result.get("deliverable")
            if not isinstance(complete_status, dict):
                errors.append("one-shot-source-result.json has no completeStatus")
            else:
                if complete_status.get("ok") is not True:
                    errors.append("one-shot-source-result.json completeStatus is not ok")
                if complete_status.get("package") != "authoritative":
                    errors.append("one-shot-source-result.json package status mismatch")
                if complete_status.get("standaloneVerifier") != "matched":
                    errors.append("one-shot-source-result.json standalone verifier status mismatch")
                if complete_status.get("archiveVerifier") != "matched":
                    errors.append("one-shot-source-result.json archive verifier status mismatch")
                if complete_status.get("proof") != "authoritative":
                    errors.append("one-shot-source-result.json proof status mismatch")
                if complete_status.get("byteAccurateResponseProof") != "matched":
                    errors.append("one-shot-source-result.json byte-accurate response proof status mismatch")
                if complete_status.get("deliverable") != deliverable.get("status"):
                    errors.append("one-shot-source-result.json deliverable status mismatch")
            if not isinstance(result_deliverable, dict):
                errors.append("one-shot-source-result.json has no deliverable object")
            else:
                if result_deliverable.get("authoritySummarySha256") != expected_sha:
                    errors.append("one-shot-source-result.json deliverable authoritySummarySha256 mismatch")
                if result_deliverable.get("status") != deliverable.get("status"):
                    errors.append("one-shot-source-result.json deliverable object status mismatch")
        response_proof_path = package / "receipts" / "byte-accurate-response-proof.json"
        if response_proof_path.exists():
            response_proof = read_json(response_proof_path)
            if response_proof.get("schema") != "mizuchi.one-shot-source-byte-accurate-response-proof.v1":
                errors.append("byte-accurate-response-proof.json schema mismatch")
            if response_proof.get("status") != "matched" or response_proof.get("ok") is not True:
                errors.append("byte-accurate-response-proof.json status is not matched")
            if response_proof.get("matchedCount") != response_proof.get("taskCount"):
                errors.append("byte-accurate-response-proof.json matched count mismatch")
            if response_proof.get("failedCount") != 0 or response_proof.get("skippedCount") != 0:
                errors.append("byte-accurate-response-proof.json has failed or skipped candidates")
            if response_proof.get("semanticDecompilation") is not False:
                errors.append("byte-accurate-response-proof.json claims semantic decompilation")
            if deliverable.get("oneShotByteAccurateResponseProof") != response_proof:
                errors.append("deliverable byte-accurate response proof mismatch")
        bundle = deliverable.get("bundle")
        bundle_path = resolve_recorded_path(bundle, deliverable_base) if isinstance(bundle, dict) else None
        if isinstance(bundle, dict) and check_external_bundle:
            if bundle_path is None:
                errors.append("deliverable bundle path is not resolvable")
            elif not bundle_path.exists():
                errors.append(f"deliverable bundle file is missing: {bundle_path}")
            else:
                if bundle_path.stat().st_size != bundle.get("size"):
                    errors.append("deliverable bundle size mismatch")
                if sha256_file(bundle_path) != bundle.get("sha256"):
                    errors.append("deliverable bundle sha256 mismatch")
        bundle_verify_path = package / "receipts" / "bundle-verify.json"
        if bundle_verify_path.exists():
            bundle_verify = read_json(bundle_verify_path)
            bundle_verifier = deliverable.get("bundleVerifier")
            if bundle_verify.get("schema") != "mizuchi.one-shot-source-deliverable-verify.v1":
                errors.append("bundle-verify.json schema mismatch")
            if bundle_verify.get("status") != "matched" or bundle_verify.get("ok") is not True:
                errors.append("bundle-verify.json status is not matched")
            if bundle_verify.get("bundleManifestStatus") != "matched":
                errors.append("bundle-verify.json bundleManifestStatus is not matched")
            if bundle_verify.get("deliverableAuthoritySummary") != summary:
                errors.append("bundle-verify.json deliverableAuthoritySummary mismatch")
            if bundle_verify.get("archiveAuthoritySummary") != summary:
                errors.append("bundle-verify.json archiveAuthoritySummary mismatch")
            if bundle_verify.get("deliverableAuthoritySummarySha256") != expected_sha:
                errors.append("bundle-verify.json deliverableAuthoritySummarySha256 mismatch")
            if bundle_verify.get("archiveAuthoritySummarySha256") != expected_sha:
                errors.append("bundle-verify.json archiveAuthoritySummarySha256 mismatch")
            if bundle_verify.get("bundleManifestAuthoritySummarySha256") != expected_sha:
                errors.append("bundle-verify.json bundleManifestAuthoritySummarySha256 mismatch")
            if bundle_verify.get("bundleManifestContentIdentity") != content.get("contentIdentity"):
                errors.append("bundle-verify.json bundleManifestContentIdentity mismatch")
            recorded_bundle_path = Path(str(bundle_verify.get("bundle"))).resolve() if bundle_verify.get("bundle") else None
            if bundle_path is not None and recorded_bundle_path != bundle_path.resolve():
                errors.append("bundle-verify.json bundle path mismatch")
            result_path = package / "receipts" / "one-shot-source-result.json"
            if result_path.exists():
                result = read_json(result_path)
                if result.get("bundleVerifier") != bundle_verify:
                    errors.append("one-shot-source-result.json bundleVerifier mismatch")
                result_deliverable = result.get("deliverable")
                if isinstance(result_deliverable, dict) and result_deliverable.get("bundleVerifier") != deliverable.get("bundleVerifier"):
                    errors.append("one-shot-source-result.json deliverable bundleVerifier mismatch")
            if not isinstance(bundle_verifier, dict):
                errors.append("deliverable bundleVerifier is missing")
            else:
                for key in (
                    "status",
                    "ok",
                    "bundleManifestStatus",
                    "bundleManifestSha256",
                    "bundleManifestAuthoritySummarySha256",
                    "bundleManifestContentIdentity",
                ):
                    if bundle_verifier.get(key) != bundle_verify.get(key):
                        errors.append(f"deliverable bundleVerifier mismatch for {key}")
    return errors


def validate_source_index(package: Path) -> list[str]:
    errors: list[str] = []
    index = read_json(package / "SOURCE_INDEX.json")
    receipt = read_json(package / "one-shot-source-receipt.json")
    roles_doc = read_json(package / "SOURCE_ROLES.json")
    if index.get("schema") != "mizuchi.one-shot-source-index.v1":
        errors.append("SOURCE_INDEX.json schema mismatch")
    if index.get("status") != "authoritative":
        errors.append("SOURCE_INDEX.json status is not authoritative")
    if index.get("authorityClass") != "byte-authoritative-source":
        errors.append("SOURCE_INDEX.json authorityClass is not byte-authoritative-source")
    if index.get("accuracyClass") != "byte-exact":
        errors.append("SOURCE_INDEX.json accuracyClass is not byte-exact")
    sources = index.get("sources")
    if not isinstance(sources, list):
        return errors + ["SOURCE_INDEX.json has no sources list"]
    by_path = {item.get("path"): item for item in sources if isinstance(item, dict)}
    roles_by_path = {item.get("path"): item for item in roles_doc.get("roles", []) if isinstance(item, dict)}
    expected = {"full-binary.S": receipt.get("sourceSha256"), "full-binary.c": receipt.get("cSourceSha256")}
    for rel, expected_sha in expected.items():
        item = by_path.get(rel)
        if not isinstance(item, dict):
            errors.append(f"SOURCE_INDEX.json missing {rel}")
            continue
        if item.get("sha256") != expected_sha:
            errors.append(f"SOURCE_INDEX.json hash mismatch: {rel}")
        if item.get("semanticDecompilation") is not False:
            errors.append(f"SOURCE_INDEX.json claims semantic decompilation: {rel}")
        role = roles_by_path.get(rel)
        if not isinstance(role, dict):
            errors.append(f"SOURCE_ROLES.json missing role for indexed source: {rel}")
        elif item.get("sourceRole") != role.get("role"):
            errors.append(f"SOURCE_INDEX.json sourceRole mismatch: {rel}")
        if sha256_file(package / rel) != expected_sha:
            errors.append(f"source file hash mismatch: {rel}")
    return errors


def validate_source_roles(package: Path) -> list[str]:
    errors: list[str] = []
    roles_doc = read_json(package / "SOURCE_ROLES.json")
    receipt = read_json(package / "one-shot-source-receipt.json")
    if roles_doc.get("schema") != "mizuchi.one-shot-source-roles.v1":
        errors.append("SOURCE_ROLES.json schema mismatch")
    if roles_doc.get("status") != receipt.get("status"):
        errors.append("SOURCE_ROLES.json status mismatch")
    if roles_doc.get("authorityClass") != "byte-authoritative-source":
        errors.append("SOURCE_ROLES.json authorityClass is not byte-authoritative-source")
    if roles_doc.get("accuracyClass") != "byte-exact":
        errors.append("SOURCE_ROLES.json accuracyClass is not byte-exact")
    if roles_doc.get("semanticDecompilation") is not False:
        errors.append("SOURCE_ROLES.json claims semantic decompilation")
    roles = roles_doc.get("roles")
    if not isinstance(roles, list):
        return errors + ["SOURCE_ROLES.json has no roles list"]
    by_path = {item.get("path"): item for item in roles if isinstance(item, dict)}
    expected = {
        "full-binary.S": ("generated-assembler-byte-source", receipt.get("sourceSha256")),
        "full-binary.c": ("generated-c-byte-emitter", receipt.get("cSourceSha256")),
    }
    for rel, (expected_role, expected_sha) in expected.items():
        role = by_path.get(rel)
        if not isinstance(role, dict):
            errors.append(f"SOURCE_ROLES.json missing role: {rel}")
            continue
        if role.get("role") != expected_role:
            errors.append(f"SOURCE_ROLES.json role mismatch: {rel}")
        if role.get("origin") != "generated-from-original-bytes":
            errors.append(f"SOURCE_ROLES.json origin mismatch: {rel}")
        if role.get("accuracyClass") != "byte-exact":
            errors.append(f"SOURCE_ROLES.json accuracyClass mismatch: {rel}")
        if role.get("semanticStatus") != "not-semantic-decompilation":
            errors.append(f"SOURCE_ROLES.json semanticStatus mismatch: {rel}")
        if role.get("semanticDecompilation") is not False:
            errors.append(f"SOURCE_ROLES.json claims semantic decompilation: {rel}")
        if role.get("sha256") != expected_sha:
            errors.append(f"SOURCE_ROLES.json hash mismatch: {rel}")
        if sha256_file(package / rel) != expected_sha:
            errors.append(f"SOURCE_ROLES.json file hash mismatch: {rel}")
        evidence = role.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"SOURCE_ROLES.json role has no evidence: {rel}")
            continue
        for evidence_rel in evidence:
            if not isinstance(evidence_rel, str) or not (package / evidence_rel).exists():
                errors.append(f"SOURCE_ROLES.json evidence missing for {rel}: {evidence_rel}")
    supplied = by_path.get("candidate-source.c") or by_path.get("candidate-source-tree")
    if isinstance(supplied, dict):
        if supplied.get("role") != "supplied-byte-exact-source-candidate":
            errors.append("SOURCE_ROLES.json supplied candidate role mismatch")
        if supplied.get("origin") != "supplied-by-caller":
            errors.append("SOURCE_ROLES.json supplied candidate origin mismatch")
        if supplied.get("accuracyClass") != "byte-exact":
            errors.append("SOURCE_ROLES.json supplied candidate accuracyClass mismatch")
        if supplied.get("semanticDecompilation") is not False:
            errors.append("SOURCE_ROLES.json supplied candidate claims semantic decompilation")
    return errors


def validate_binary_evidence(package: Path) -> list[str]:
    errors: list[str] = []
    evidence = read_json(package / "BINARY_EVIDENCE.json")
    receipt = read_json(package / "one-shot-source-receipt.json")
    if evidence.get("schema") != "mizuchi.one-shot-source-binary-evidence.v1":
        errors.append("BINARY_EVIDENCE.json schema mismatch")
    if evidence.get("status") != "recorded":
        errors.append("BINARY_EVIDENCE.json status is not recorded")
    original = evidence.get("original")
    if not isinstance(original, dict):
        errors.append("BINARY_EVIDENCE.json has no original object")
    else:
        if original.get("sha256") != receipt.get("originalSha256"):
            errors.append("BINARY_EVIDENCE.json original hash mismatch")
        if original.get("size") != receipt.get("originalSize"):
            errors.append("BINARY_EVIDENCE.json original size mismatch")
        if (package / "original.bin").exists() and sha256_file(package / "original.bin") != original.get("sha256"):
            errors.append("BINARY_EVIDENCE.json original.bin hash mismatch")
    hints = evidence.get("functionBoundaryHints")
    if not isinstance(hints, dict):
        errors.append("BINARY_EVIDENCE.json has no functionBoundaryHints object")
    elif hints.get("verifiedAgainstSource") is not False:
        errors.append("BINARY_EVIDENCE.json claims verified source boundaries")
    return errors


def validate_function_boundary_candidates(package: Path) -> list[str]:
    errors: list[str] = []
    candidates_doc = read_json(package / "FUNCTION_BOUNDARY_CANDIDATES.json")
    if candidates_doc.get("schema") != "mizuchi.one-shot-source-function-boundary-candidates.v1":
        errors.append("FUNCTION_BOUNDARY_CANDIDATES.json schema mismatch")
    if candidates_doc.get("status") not in ("hints-present", "absent"):
        errors.append("FUNCTION_BOUNDARY_CANDIDATES.json status mismatch")
    if candidates_doc.get("verifiedAgainstSource") is not False:
        errors.append("FUNCTION_BOUNDARY_CANDIDATES.json claims verified source boundaries")
    if candidates_doc.get("binaryEvidenceSha256") != sha256_file(package / "BINARY_EVIDENCE.json"):
        errors.append("FUNCTION_BOUNDARY_CANDIDATES.json binaryEvidenceSha256 mismatch")
    candidates = candidates_doc.get("candidates")
    if not isinstance(candidates, list):
        return errors + ["FUNCTION_BOUNDARY_CANDIDATES.json has no candidates list"]
    if candidates_doc.get("candidateCount") != len(candidates):
        errors.append("FUNCTION_BOUNDARY_CANDIDATES.json candidateCount mismatch")
    for item in candidates:
        if not isinstance(item, dict):
            errors.append("FUNCTION_BOUNDARY_CANDIDATES.json contains non-object candidate")
        elif item.get("verifiedAgainstSource") is not False:
            errors.append(f"function candidate claims source verification: {item.get('name')}")
    return errors


def validate_function_byte_slices(package: Path) -> list[str]:
    errors: list[str] = []
    slices_doc = read_json(package / "FUNCTION_BYTE_SLICES.json")
    if slices_doc.get("schema") != "mizuchi.one-shot-source-function-byte-slices.v1":
        errors.append("FUNCTION_BYTE_SLICES.json schema mismatch")
    if slices_doc.get("status") not in ("slices-present", "absent"):
        errors.append("FUNCTION_BYTE_SLICES.json status mismatch")
    if slices_doc.get("binaryEvidenceSha256") != sha256_file(package / "BINARY_EVIDENCE.json"):
        errors.append("FUNCTION_BYTE_SLICES.json binaryEvidenceSha256 mismatch")
    if slices_doc.get("functionBoundaryCandidatesSha256") != sha256_file(package / "FUNCTION_BOUNDARY_CANDIDATES.json"):
        errors.append("FUNCTION_BYTE_SLICES.json functionBoundaryCandidatesSha256 mismatch")
    if slices_doc.get("verifiedAgainstSource") is not False:
        errors.append("FUNCTION_BYTE_SLICES.json claims source verification")
    slices = slices_doc.get("slices")
    if not isinstance(slices, list):
        return errors + ["FUNCTION_BYTE_SLICES.json has no slices list"]
    if slices_doc.get("sliceCount") != len(slices):
        errors.append("FUNCTION_BYTE_SLICES.json sliceCount mismatch")
    original = (package / "original.bin").read_bytes() if (package / "original.bin").exists() else b""
    for item in slices:
        if not isinstance(item, dict):
            errors.append("FUNCTION_BYTE_SLICES.json contains non-object slice")
            continue
        if item.get("verifiedAgainstSource") is not False:
            errors.append(f"function byte slice claims source verification: {item.get('name')}")
        offset = item.get("fileOffset")
        size = item.get("size")
        if not isinstance(offset, int) or not isinstance(size, int) or offset < 0 or size <= 0:
            errors.append(f"function byte slice has invalid offset/size: {item.get('name')}")
            continue
        if offset + size > len(original):
            errors.append(f"function byte slice is outside original.bin: {item.get('name')}")
            continue
        if hashlib.sha256(original[offset : offset + size]).hexdigest() != item.get("sha256"):
            errors.append(f"function byte slice hash mismatch: {item.get('name')}")
    return errors


def validate_function_slice_sources(package: Path) -> list[str]:
    errors: list[str] = []
    sources_doc = read_json(package / "FUNCTION_SLICE_SOURCES.json")
    if sources_doc.get("schema") != "mizuchi.one-shot-source-function-slice-sources.v1":
        errors.append("FUNCTION_SLICE_SOURCES.json schema mismatch")
    if sources_doc.get("status") not in ("sources-present", "absent"):
        errors.append("FUNCTION_SLICE_SOURCES.json status mismatch")
    if sources_doc.get("functionByteSlicesSha256") != sha256_file(package / "FUNCTION_BYTE_SLICES.json"):
        errors.append("FUNCTION_SLICE_SOURCES.json functionByteSlicesSha256 mismatch")
    if sources_doc.get("semanticDecompilation") is not False or sources_doc.get("verifiedAgainstSource") is not False:
        errors.append("FUNCTION_SLICE_SOURCES.json claims semantic/source verification")
    sources = sources_doc.get("sources")
    if not isinstance(sources, list):
        return errors + ["FUNCTION_SLICE_SOURCES.json has no sources list"]
    if sources_doc.get("sourceCount") != len(sources):
        errors.append("FUNCTION_SLICE_SOURCES.json sourceCount mismatch")
    for item in sources:
        if not isinstance(item, dict):
            errors.append("FUNCTION_SLICE_SOURCES.json contains non-object source")
            continue
        rel = item.get("path")
        rel_path = Path(str(rel or ""))
        if not isinstance(rel, str) or rel_path.is_absolute() or ".." in rel_path.parts:
            errors.append("FUNCTION_SLICE_SOURCES.json has unsafe source path")
            continue
        path = package / rel_path
        if not path.exists():
            errors.append(f"function slice source missing: {rel}")
            continue
        if sha256_file(path) != item.get("sourceSha256"):
            errors.append(f"function slice source hash mismatch: {rel}")
        if item.get("semanticDecompilation") is not False or item.get("verifiedAgainstSource") is not False:
            errors.append(f"function slice source claims semantic/source verification: {rel}")
    return errors


def validate_function_reconstruction_tasks(package: Path) -> list[str]:
    errors: list[str] = []
    tasks_doc = read_json(package / "FUNCTION_RECONSTRUCTION_TASKS.json")
    if tasks_doc.get("schema") != "mizuchi.one-shot-source-function-reconstruction-tasks.v1":
        errors.append("FUNCTION_RECONSTRUCTION_TASKS.json schema mismatch")
    if tasks_doc.get("status") not in ("tasks-present", "absent"):
        errors.append("FUNCTION_RECONSTRUCTION_TASKS.json status mismatch")
    if tasks_doc.get("functionByteSlicesSha256") != sha256_file(package / "FUNCTION_BYTE_SLICES.json"):
        errors.append("FUNCTION_RECONSTRUCTION_TASKS.json functionByteSlicesSha256 mismatch")
    if tasks_doc.get("functionSliceSourcesSha256") != sha256_file(package / "FUNCTION_SLICE_SOURCES.json"):
        errors.append("FUNCTION_RECONSTRUCTION_TASKS.json functionSliceSourcesSha256 mismatch")
    if tasks_doc.get("semanticDecompilation") is not False or tasks_doc.get("verifiedAgainstSource") is not False:
        errors.append("FUNCTION_RECONSTRUCTION_TASKS.json claims semantic/source verification")
    tasks = tasks_doc.get("tasks")
    if not isinstance(tasks, list):
        return errors + ["FUNCTION_RECONSTRUCTION_TASKS.json has no tasks list"]
    if tasks_doc.get("taskCount") != len(tasks):
        errors.append("FUNCTION_RECONSTRUCTION_TASKS.json taskCount mismatch")
    for item in tasks:
        if not isinstance(item, dict):
            errors.append("FUNCTION_RECONSTRUCTION_TASKS.json contains non-object task")
            continue
        if item.get("semanticDecompilation") is not False or item.get("verifiedAgainstSource") is not False:
            errors.append(f"function reconstruction task claims semantic/source verification: {item.get('name')}")
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
                errors.append(f"FUNCTION_RECONSTRUCTION_TASKS.json has unsafe {key} path")
                continue
            path = package / rel_path
            if not path.exists():
                errors.append(f"function reconstruction task file missing: {rel}")
                continue
            if sha256_file(path) != item.get(hash_key):
                errors.append(f"function reconstruction task hash mismatch: {rel}")
    return errors


def validate_function_reconstruction_candidate_results(package: Path) -> list[str]:
    errors: list[str] = []
    results = read_json(package / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json")
    tasks = read_json(package / "FUNCTION_RECONSTRUCTION_TASKS.json")
    if results.get("schema") != "mizuchi.one-shot-source-function-reconstruction-candidate-replay.v1":
        errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json schema mismatch")
    if results.get("status") not in ("no-candidates", "partial", "matched", "failed"):
        errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json status mismatch")
    if results.get("functionReconstructionTasksSha256") != sha256_file(package / "FUNCTION_RECONSTRUCTION_TASKS.json"):
        errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json functionReconstructionTasksSha256 mismatch")
    if results.get("replayScriptSha256") != sha256_file(package / "REPLAY_RECONSTRUCTION_CANDIDATES.py"):
        errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json replayScriptSha256 mismatch")
    if results.get("semanticDecompilation") is not False:
        errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json claims semantic decompilation")
    rows = results.get("tasks")
    if not isinstance(rows, list):
        return errors + ["FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json has no tasks list"]
    if results.get("taskCount") != tasks.get("taskCount"):
        errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json taskCount mismatch")
    matched = results.get("matchedCount")
    failed = results.get("failedCount")
    skipped = results.get("skippedCount")
    if not all(isinstance(value, int) and value >= 0 for value in (matched, failed, skipped)):
        errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json has invalid counts")
    elif matched + failed + skipped != len(rows):
        errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json count totals mismatch")
    else:
        row_matched = sum(1 for row in rows if isinstance(row, dict) and row.get("status") == "matched")
        row_failed = sum(1 for row in rows if isinstance(row, dict) and row.get("status") == "failed")
        row_skipped = sum(1 for row in rows if isinstance(row, dict) and row.get("status") == "skipped")
        if (matched, failed, skipped) != (row_matched, row_failed, row_skipped):
            errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json count row mismatch")
        status = results.get("status")
        if status == "matched" and not (matched > 0 and failed == 0 and skipped == 0):
            errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json matched status/count mismatch")
        elif status == "partial" and not (matched > 0 and failed == 0 and skipped > 0):
            errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json partial status/count mismatch")
        elif status == "failed" and failed <= 0:
            errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json failed status/count mismatch")
        elif status == "no-candidates" and not (matched == 0 and failed == 0):
            errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json no-candidates status/count mismatch")
    for row in rows:
        if not isinstance(row, dict):
            errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json contains non-object task row")
            continue
        if row.get("status") not in ("matched", "failed", "skipped"):
            errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json task row status mismatch")
        if row.get("status") in ("matched", "failed"):
            candidate_rel = row.get("candidate")
            candidate_path = package / str(candidate_rel or "")
            if not isinstance(candidate_rel, str) or Path(candidate_rel).is_absolute() or ".." in Path(candidate_rel).parts:
                errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json has unsafe candidate path")
            elif not candidate_path.exists():
                errors.append(f"candidate source listed in results is missing: {candidate_rel}")
            elif row.get("candidateSourceSha256") != sha256_file(candidate_path):
                errors.append(f"candidate source hash mismatch: {candidate_rel}")
            build_env_rel = row.get("candidateBuildEnv")
            if build_env_rel is not None:
                build_env_path = package / str(build_env_rel or "")
                if not isinstance(build_env_rel, str) or Path(build_env_rel).is_absolute() or ".." in Path(build_env_rel).parts:
                    errors.append("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json has unsafe candidateBuildEnv path")
                elif not build_env_path.exists():
                    errors.append(f"candidate build env listed in results is missing: {build_env_rel}")
                elif row.get("candidateBuildEnvSha256") != sha256_file(build_env_path):
                    errors.append(f"candidate build env hash mismatch: {build_env_rel}")
            if not isinstance(row.get("candidateOutputSha256"), str):
                errors.append(f"candidate output hash missing: {candidate_rel}")
            if row.get("byteIdentical") is not (row.get("status") == "matched"):
                errors.append(f"candidate byteIdentical flag mismatch: {candidate_rel}")
    return errors


def validate_one_shot_reconstruction_request(package: Path) -> list[str]:
    errors: list[str] = []
    request_path = package / "ONE_SHOT_RECONSTRUCTION_REQUEST.md"
    request_json_path = package / "ONE_SHOT_RECONSTRUCTION_REQUEST.json"
    request_bundle_path = package / "ONE_SHOT_RECONSTRUCTION_BUNDLE.json"
    proof = read_json(package / "PACKAGE_PROOF.json")
    tasks = read_json(package / "FUNCTION_RECONSTRUCTION_TASKS.json")
    request = proof.get("oneShotReconstructionRequest")
    request_json_proof = proof.get("oneShotReconstructionRequestJson")
    request_bundle_proof = proof.get("oneShotReconstructionBundle")
    if not request_path.exists():
        return ["ONE_SHOT_RECONSTRUCTION_REQUEST.md is missing"]
    if not request_json_path.exists():
        errors.append("ONE_SHOT_RECONSTRUCTION_REQUEST.json is missing")
    if not isinstance(request, dict):
        return ["PACKAGE_PROOF.json has no oneShotReconstructionRequest object"]
    if request.get("path") != "ONE_SHOT_RECONSTRUCTION_REQUEST.md":
        errors.append("PACKAGE_PROOF.json oneShotReconstructionRequest path mismatch")
    if request.get("sha256") != sha256_file(request_path):
        errors.append("PACKAGE_PROOF.json oneShotReconstructionRequest hash mismatch")
    if request.get("taskCount") != tasks.get("taskCount"):
        errors.append("PACKAGE_PROOF.json oneShotReconstructionRequest taskCount mismatch")
    if request.get("semanticDecompilation") is not False:
        errors.append("PACKAGE_PROOF.json oneShotReconstructionRequest claims semantic decompilation")
    if not isinstance(request_json_proof, dict):
        errors.append("PACKAGE_PROOF.json has no oneShotReconstructionRequestJson object")
    else:
        request_json = read_json(request_json_path)
        if request_json.get("schema") != "mizuchi.one-shot-source-reconstruction-request.v1":
            errors.append("ONE_SHOT_RECONSTRUCTION_REQUEST.json schema mismatch")
        if request_json.get("taskCount") != tasks.get("taskCount"):
            errors.append("ONE_SHOT_RECONSTRUCTION_REQUEST.json taskCount mismatch")
        if request_json.get("semanticDecompilation") is not False:
            errors.append("ONE_SHOT_RECONSTRUCTION_REQUEST.json claims semantic decompilation")
        if request_json_proof.get("path") != "ONE_SHOT_RECONSTRUCTION_REQUEST.json":
            errors.append("PACKAGE_PROOF.json oneShotReconstructionRequestJson path mismatch")
        if request_json_proof.get("sha256") != sha256_file(request_json_path):
            errors.append("PACKAGE_PROOF.json oneShotReconstructionRequestJson hash mismatch")
        if request_json_proof.get("taskCount") != tasks.get("taskCount"):
            errors.append("PACKAGE_PROOF.json oneShotReconstructionRequestJson taskCount mismatch")
        preferred = request_json.get("preferredResponse")
        if not isinstance(preferred, dict) or preferred.get("schema") != "mizuchi.one-shot-source-reconstruction-response.v1":
            errors.append("ONE_SHOT_RECONSTRUCTION_REQUEST.json preferred response mismatch")
        elif not isinstance(preferred.get("structuredShape"), dict) or preferred["structuredShape"].get("schema") != "mizuchi.one-shot-source-reconstruction-response.v1":
            errors.append("ONE_SHOT_RECONSTRUCTION_REQUEST.json structured preferred response mismatch")
        elif preferred.get("replayReportShapes") != expected_json_replay_report_shapes():
            errors.append("ONE_SHOT_RECONSTRUCTION_REQUEST.json replay report shapes mismatch")
        commands = request_json.get("commands")
        if not isinstance(commands, dict):
            errors.append("ONE_SHOT_RECONSTRUCTION_REQUEST.json has no commands object")
        else:
            expected_commands = {
                "validateJson": "./VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json",
                "validateJsonWithBuildCommand": "./VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json --allow-build-command",
                "importJson": "./IMPORT_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json",
                "importJsonWithBuildCommand": "./IMPORT_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json --allow-build-command",
                "refreshReceipts": "./REFRESH_RECONSTRUCTION_RECEIPTS.py",
            }
            for key, expected_command in expected_commands.items():
                if commands.get(key) != expected_command:
                    errors.append(f"ONE_SHOT_RECONSTRUCTION_REQUEST.json {key} command mismatch")
    if not request_bundle_path.exists():
        errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json is missing")
    elif not isinstance(request_bundle_proof, dict):
        errors.append("PACKAGE_PROOF.json has no oneShotReconstructionBundle object")
    else:
        request_bundle = read_json(request_bundle_path)
        if request_bundle.get("schema") != "mizuchi.one-shot-source-reconstruction-request-bundle.v1":
            errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json schema mismatch")
        if request_bundle.get("status") != "candidate-source-request-bundle":
            errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json status mismatch")
        if request_bundle.get("taskCount") != tasks.get("taskCount"):
            errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json taskCount mismatch")
        if request_bundle.get("semanticDecompilation") is not False:
            errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json claims semantic decompilation")
        if request_bundle_proof.get("path") != "ONE_SHOT_RECONSTRUCTION_BUNDLE.json":
            errors.append("PACKAGE_PROOF.json oneShotReconstructionBundle path mismatch")
        if request_bundle_proof.get("sha256") != sha256_file(request_bundle_path):
            errors.append("PACKAGE_PROOF.json oneShotReconstructionBundle hash mismatch")
        if request_bundle_proof.get("taskCount") != tasks.get("taskCount"):
            errors.append("PACKAGE_PROOF.json oneShotReconstructionBundle taskCount mismatch")
        bundle_request = request_bundle.get("request")
        if not isinstance(bundle_request, dict) or bundle_request.get("sha256") != sha256_file(request_json_path):
            errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json request hash mismatch")
        bundle_template = request_bundle.get("responseTemplate")
        if not isinstance(bundle_template, dict) or bundle_template.get("sha256") != sha256_file(package / "RECONSTRUCTION_RESPONSE_TEMPLATE.json"):
            errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json response template hash mismatch")
        elif bundle_template.get("jsonReplayReportShapes") != expected_json_replay_report_shapes():
            errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json response template replay report shapes mismatch")
        bundle_artifacts = request_bundle.get("sourceArtifacts")
        if not isinstance(bundle_artifacts, dict):
            errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json has no sourceArtifacts object")
        else:
            expected_bundle_artifacts = {
                "functionReconstructionTasks": "FUNCTION_RECONSTRUCTION_TASKS.json",
                "markdownRequest": "ONE_SHOT_RECONSTRUCTION_REQUEST.md",
                "candidateImporter": "IMPORT_RECONSTRUCTION_CANDIDATES.py",
                "byteAccurateResponseExporter": "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py",
                "jsonImporter": "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py",
                "jsonValidator": "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py",
                "receiptRefresher": "REFRESH_RECONSTRUCTION_RECEIPTS.py",
                "candidateReplay": "REPLAY_RECONSTRUCTION_CANDIDATES.py",
                "semanticAuthorityEvaluator": "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py",
            }
            for artifact_name, expected_path in expected_bundle_artifacts.items():
                artifact = bundle_artifacts.get(artifact_name)
                if not isinstance(artifact, dict):
                    errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json missing {artifact_name} artifact")
                    continue
                if artifact.get("path") != expected_path:
                    errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json {artifact_name} path mismatch")
                if artifact.get("sha256") != sha256_file(package / expected_path):
                    errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json {artifact_name} hash mismatch")
        bundle_tasks = request_bundle.get("tasks")
        if not isinstance(bundle_tasks, list):
            errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json has no tasks list")
        else:
            if len(bundle_tasks) != tasks.get("taskCount"):
                errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json tasks length mismatch")
            for row in bundle_tasks:
                if not isinstance(row, dict):
                    errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json contains non-object task")
                    continue
                prompt_rel = row.get("prompt")
                prompt_path = package / str(prompt_rel or "")
                if not isinstance(prompt_rel, str) or Path(prompt_rel).is_absolute() or ".." in Path(prompt_rel).parts:
                    errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json has unsafe prompt path")
                elif not prompt_path.exists():
                    errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json prompt missing: {prompt_rel}")
                else:
                    if row.get("promptSha256") != sha256_file(prompt_path):
                        errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json prompt hash mismatch: {prompt_rel}")
                    if row.get("promptText") != prompt_path.read_text():
                        errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json prompt text mismatch: {prompt_rel}")
                target = row.get("targetBytes")
                if not isinstance(target, dict):
                    errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json task has no targetBytes object")
                else:
                    target_rel = target.get("path")
                    target_path = package / str(target_rel or "")
                    if not isinstance(target_rel, str) or Path(target_rel).is_absolute() or ".." in Path(target_rel).parts:
                        errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json has unsafe target bytes path")
                    elif not target_path.exists():
                        errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json target bytes missing: {target_rel}")
                    else:
                        target_bytes = target_path.read_bytes()
                        if target.get("sha256") != sha256_file(target_path):
                            errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json target bytes hash mismatch: {target_rel}")
                        if target.get("size") != len(target_bytes):
                            errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json target bytes size mismatch: {target_rel}")
                        if target.get("hex") != target_bytes.hex():
                            errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json target bytes hex mismatch: {target_rel}")
                reference = row.get("referenceByteEmitter")
                if not isinstance(reference, dict):
                    errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json task has no referenceByteEmitter object")
                else:
                    reference_rel = reference.get("path")
                    reference_path = package / str(reference_rel or "")
                    if not isinstance(reference_rel, str) or Path(reference_rel).is_absolute() or ".." in Path(reference_rel).parts:
                        errors.append("ONE_SHOT_RECONSTRUCTION_BUNDLE.json has unsafe reference byte-emitter path")
                    elif not reference_path.exists():
                        errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json reference byte-emitter missing: {reference_rel}")
                    else:
                        if reference.get("sha256") != sha256_file(reference_path):
                            errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json reference byte-emitter hash mismatch: {reference_rel}")
                        if reference.get("sourceText") != reference_path.read_text():
                            errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json reference byte-emitter text mismatch: {reference_rel}")
                if row.get("semanticDecompilation") is not False:
                    errors.append(f"ONE_SHOT_RECONSTRUCTION_BUNDLE.json task claims semantic decompilation: {row.get('name')}")
    return errors


def validate_one_shot_candidate_importer(package: Path) -> list[str]:
    errors: list[str] = []
    importer_path = package / "IMPORT_RECONSTRUCTION_CANDIDATES.py"
    proof = read_json(package / "PACKAGE_PROOF.json")
    importer = proof.get("oneShotCandidateImporter")
    if not importer_path.exists():
        return ["IMPORT_RECONSTRUCTION_CANDIDATES.py is missing"]
    if not isinstance(importer, dict):
        return ["PACKAGE_PROOF.json has no oneShotCandidateImporter object"]
    if importer.get("path") != "IMPORT_RECONSTRUCTION_CANDIDATES.py":
        errors.append("PACKAGE_PROOF.json oneShotCandidateImporter path mismatch")
    if importer.get("sha256") != sha256_file(importer_path):
        errors.append("PACKAGE_PROOF.json oneShotCandidateImporter hash mismatch")
    if importer.get("semanticDecompilation") is not False:
        errors.append("PACKAGE_PROOF.json oneShotCandidateImporter claims semantic decompilation")
    return errors


def validate_one_shot_response_json_importer(package: Path) -> list[str]:
    errors: list[str] = []
    importer_path = package / "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py"
    proof = read_json(package / "PACKAGE_PROOF.json")
    importer = proof.get("oneShotResponseJsonImporter")
    if not importer_path.exists():
        return ["IMPORT_RECONSTRUCTION_RESPONSE_JSON.py is missing"]
    if not isinstance(importer, dict):
        return ["PACKAGE_PROOF.json has no oneShotResponseJsonImporter object"]
    if importer.get("path") != "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py":
        errors.append("PACKAGE_PROOF.json oneShotResponseJsonImporter path mismatch")
    if importer.get("sha256") != sha256_file(importer_path):
        errors.append("PACKAGE_PROOF.json oneShotResponseJsonImporter hash mismatch")
    if importer.get("semanticDecompilation") is not False:
        errors.append("PACKAGE_PROOF.json oneShotResponseJsonImporter claims semantic decompilation")
    return errors


def validate_one_shot_response_json_validator(package: Path) -> list[str]:
    errors: list[str] = []
    validator_path = package / "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py"
    proof = read_json(package / "PACKAGE_PROOF.json")
    validator = proof.get("oneShotResponseJsonValidator")
    if not validator_path.exists():
        return ["VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py is missing"]
    if not isinstance(validator, dict):
        return ["PACKAGE_PROOF.json has no oneShotResponseJsonValidator object"]
    if validator.get("path") != "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py":
        errors.append("PACKAGE_PROOF.json oneShotResponseJsonValidator path mismatch")
    if validator.get("sha256") != sha256_file(validator_path):
        errors.append("PACKAGE_PROOF.json oneShotResponseJsonValidator hash mismatch")
    if validator.get("semanticDecompilation") is not False:
        errors.append("PACKAGE_PROOF.json oneShotResponseJsonValidator claims semantic decompilation")
    return errors


def validate_one_shot_receipt_refresher(package: Path) -> list[str]:
    errors: list[str] = []
    refresher_path = package / "REFRESH_RECONSTRUCTION_RECEIPTS.py"
    proof = read_json(package / "PACKAGE_PROOF.json")
    refresher = proof.get("oneShotReceiptRefresher")
    if not refresher_path.exists():
        return ["REFRESH_RECONSTRUCTION_RECEIPTS.py is missing"]
    if not isinstance(refresher, dict):
        return ["PACKAGE_PROOF.json has no oneShotReceiptRefresher object"]
    if refresher.get("path") != "REFRESH_RECONSTRUCTION_RECEIPTS.py":
        errors.append("PACKAGE_PROOF.json oneShotReceiptRefresher path mismatch")
    if refresher.get("sha256") != sha256_file(refresher_path):
        errors.append("PACKAGE_PROOF.json oneShotReceiptRefresher hash mismatch")
    if refresher.get("semanticDecompilation") is not False:
        errors.append("PACKAGE_PROOF.json oneShotReceiptRefresher claims semantic decompilation")
    return errors


def validate_one_shot_response_template(package: Path) -> list[str]:
    errors: list[str] = []
    template_path = package / "RECONSTRUCTION_RESPONSE_TEMPLATE.json"
    exporter_path = package / "EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py"
    byte_exporter_path = package / "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py"
    byte_prover_path = package / "PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py"
    proof = read_json(package / "PACKAGE_PROOF.json")
    tasks = read_json(package / "FUNCTION_RECONSTRUCTION_TASKS.json")
    template = read_json(template_path)
    proof_template = proof.get("oneShotResponseTemplate")
    proof_exporter = proof.get("oneShotResponseTemplateExporter")
    proof_byte_exporter = proof.get("oneShotByteAccurateResponseExporter")
    proof_byte_prover = proof.get("oneShotByteAccurateResponseProver")
    if template.get("schema") != "mizuchi.one-shot-source-reconstruction-response-template.v1":
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json schema mismatch")
    if template.get("status") != "empty-template":
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json status mismatch")
    if template.get("semanticDecompilation") is not False:
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json claims semantic decompilation")
    expected = template.get("expectedCandidates")
    task_rows = tasks.get("tasks")
    if not isinstance(expected, list):
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json has no expectedCandidates list")
    elif template.get("taskCount") != len(expected) or template.get("taskCount") != tasks.get("taskCount"):
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json taskCount mismatch")
    elif isinstance(task_rows, list):
        expected_paths = [f"{task.get('path')}/candidate.c" for task in task_rows if isinstance(task, dict)]
        template_paths = [row.get("path") for row in expected if isinstance(row, dict)]
        if template_paths != expected_paths:
            errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json expected candidate paths mismatch")
    if template.get("oneShotReconstructionRequestSha256") != sha256_file(package / "ONE_SHOT_RECONSTRUCTION_REQUEST.md"):
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json request hash mismatch")
    if template.get("oneShotReconstructionRequestJsonSha256") != sha256_file(package / "ONE_SHOT_RECONSTRUCTION_REQUEST.json"):
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json request JSON hash mismatch")
    if template.get("functionReconstructionTasksSha256") != sha256_file(package / "FUNCTION_RECONSTRUCTION_TASKS.json"):
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json tasks hash mismatch")
    if template.get("importerSha256") != sha256_file(package / "IMPORT_RECONSTRUCTION_CANDIDATES.py"):
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json importer hash mismatch")
    if template.get("jsonImporterSha256") != sha256_file(package / "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py"):
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json JSON importer hash mismatch")
    if template.get("jsonValidatorSha256") != sha256_file(package / "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py"):
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json JSON validator hash mismatch")
    if template.get("receiptRefresherSha256") != sha256_file(package / "REFRESH_RECONSTRUCTION_RECEIPTS.py"):
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json receipt refresher hash mismatch")
    if template.get("exporterSha256") != sha256_file(exporter_path):
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json exporter hash mismatch")
    if template.get("receiptRefreshCommand") != "./REFRESH_RECONSTRUCTION_RECEIPTS.py":
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json receipt refresh command mismatch")
    if template.get("jsonImportCommandWithBuildCommand") != "./IMPORT_RECONSTRUCTION_RESPONSE_JSON.py --response-json <response.json> --allow-build-command":
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json build-command JSON import command mismatch")
    if template.get("jsonValidateCommandWithBuildCommand") != "./VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json <response.json> --allow-build-command":
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json build-command JSON validate command mismatch")
    shape = template.get("jsonResponseShape")
    if not isinstance(shape, dict) or shape.get("schema") != "mizuchi.one-shot-source-reconstruction-response.v1":
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json JSON response shape mismatch")
    elif "candidates" in shape or not isinstance(shape.get("files"), dict):
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json JSON response shape must use files only")
    structured_shape = template.get("jsonStructuredResponseShape")
    if not isinstance(structured_shape, dict) or structured_shape.get("schema") != "mizuchi.one-shot-source-reconstruction-response.v1":
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json structured JSON response shape mismatch")
    else:
        structured_candidates = structured_shape.get("candidates")
        if not isinstance(structured_candidates, list) or not structured_candidates:
            errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json structured JSON response shape has no candidates")
        else:
            build = structured_candidates[0].get("build") if isinstance(structured_candidates[0], dict) else None
            if not isinstance(build, dict) or build.get("command") != "optional custom command that writes $CANDIDATE_OUTPUT; requires --allow-build-command":
                errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json structured JSON response build command mismatch")
    if template.get("jsonReplayReportShapes") != expected_json_replay_report_shapes():
        errors.append("RECONSTRUCTION_RESPONSE_TEMPLATE.json JSON replay report shapes mismatch")
    if proof_template != template:
        errors.append("PACKAGE_PROOF.json oneShotResponseTemplate mismatch")
    if not isinstance(proof_exporter, dict):
        errors.append("PACKAGE_PROOF.json has no oneShotResponseTemplateExporter")
    else:
        if proof_exporter.get("path") != "EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py":
            errors.append("PACKAGE_PROOF.json oneShotResponseTemplateExporter path mismatch")
        if proof_exporter.get("sha256") != sha256_file(exporter_path):
            errors.append("PACKAGE_PROOF.json oneShotResponseTemplateExporter hash mismatch")
        if proof_exporter.get("semanticDecompilation") is not False:
            errors.append("PACKAGE_PROOF.json oneShotResponseTemplateExporter claims semantic decompilation")
    if not isinstance(proof_byte_exporter, dict):
        errors.append("PACKAGE_PROOF.json has no oneShotByteAccurateResponseExporter")
    else:
        if proof_byte_exporter.get("path") != "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py":
            errors.append("PACKAGE_PROOF.json oneShotByteAccurateResponseExporter path mismatch")
        if proof_byte_exporter.get("sha256") != sha256_file(byte_exporter_path):
            errors.append("PACKAGE_PROOF.json oneShotByteAccurateResponseExporter hash mismatch")
        if proof_byte_exporter.get("semanticDecompilation") is not False:
            errors.append("PACKAGE_PROOF.json oneShotByteAccurateResponseExporter claims semantic decompilation")
    if not isinstance(proof_byte_prover, dict):
        errors.append("PACKAGE_PROOF.json has no oneShotByteAccurateResponseProver")
    else:
        if proof_byte_prover.get("path") != "PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py":
            errors.append("PACKAGE_PROOF.json oneShotByteAccurateResponseProver path mismatch")
        if proof_byte_prover.get("sha256") != sha256_file(byte_prover_path):
            errors.append("PACKAGE_PROOF.json oneShotByteAccurateResponseProver hash mismatch")
        if proof_byte_prover.get("semanticDecompilation") is not False:
            errors.append("PACKAGE_PROOF.json oneShotByteAccurateResponseProver claims semantic decompilation")
    return errors


def validate_semantic_readiness(package: Path) -> list[str]:
    errors: list[str] = []
    readiness = read_json(package / "SEMANTIC_READINESS.json")
    claims = read_json(package / "CLAIMS.json")
    roles = read_json(package / "SOURCE_ROLES.json")
    binary_evidence = read_json(package / "BINARY_EVIDENCE.json")
    boundary_candidates = read_json(package / "FUNCTION_BOUNDARY_CANDIDATES.json")
    function_byte_slices = read_json(package / "FUNCTION_BYTE_SLICES.json")
    function_slice_sources = read_json(package / "FUNCTION_SLICE_SOURCES.json")
    reconstruction_tasks = read_json(package / "FUNCTION_RECONSTRUCTION_TASKS.json")
    receipt = read_json(package / "one-shot-source-receipt.json")
    if readiness.get("schema") != "mizuchi.one-shot-source-semantic-readiness.v1":
        errors.append("SEMANTIC_READINESS.json schema mismatch")
    expected_status = "ready" if int(receipt.get("semanticSourceBundlesVerified") or 0) > 0 else "not-ready"
    if readiness.get("status") != expected_status:
        errors.append("SEMANTIC_READINESS.json status mismatch")
    if readiness.get("authorityClass") != claims.get("authorityClass"):
        errors.append("SEMANTIC_READINESS.json authorityClass mismatch")
    if readiness.get("accuracyClass") != claims.get("accuracyClass"):
        errors.append("SEMANTIC_READINESS.json accuracyClass mismatch")
    if readiness.get("currentClaim") != "byte-exact-reproduction":
        errors.append("SEMANTIC_READINESS.json currentClaim mismatch")
    if readiness.get("targetClaim") != "semantic-source-recovery":
        errors.append("SEMANTIC_READINESS.json targetClaim mismatch")
    if readiness.get("semanticDecompilation") is not False:
        errors.append("SEMANTIC_READINESS.json claims semantic decompilation")
    if readiness.get("binaryEvidenceStatus") != binary_evidence.get("status"):
        errors.append("SEMANTIC_READINESS.json binaryEvidenceStatus mismatch")
    if readiness.get("functionBoundaryCandidateStatus") != boundary_candidates.get("status"):
        errors.append("SEMANTIC_READINESS.json functionBoundaryCandidateStatus mismatch")
    if readiness.get("functionBoundaryCandidateCount") != boundary_candidates.get("candidateCount"):
        errors.append("SEMANTIC_READINESS.json functionBoundaryCandidateCount mismatch")
    if readiness.get("functionByteSliceStatus") != function_byte_slices.get("status"):
        errors.append("SEMANTIC_READINESS.json functionByteSliceStatus mismatch")
    if readiness.get("functionByteSliceCount") != function_byte_slices.get("sliceCount"):
        errors.append("SEMANTIC_READINESS.json functionByteSliceCount mismatch")
    if readiness.get("functionSliceSourceStatus") != function_slice_sources.get("status"):
        errors.append("SEMANTIC_READINESS.json functionSliceSourceStatus mismatch")
    if readiness.get("functionSliceSourceCount") != function_slice_sources.get("sourceCount"):
        errors.append("SEMANTIC_READINESS.json functionSliceSourceCount mismatch")
    if readiness.get("functionReconstructionTaskStatus") != reconstruction_tasks.get("status"):
        errors.append("SEMANTIC_READINESS.json functionReconstructionTaskStatus mismatch")
    if readiness.get("functionReconstructionTaskCount") != reconstruction_tasks.get("taskCount"):
        errors.append("SEMANTIC_READINESS.json functionReconstructionTaskCount mismatch")
    if readiness.get("sourceRoles") != roles.get("roles"):
        errors.append("SEMANTIC_READINESS.json sourceRoles mismatch")
    missing = readiness.get("missingForSemanticAuthority")
    if expected_status == "not-ready" and (not isinstance(missing, list) or not missing):
        errors.append("SEMANTIC_READINESS.json has no semantic blockers")
    available = readiness.get("evidenceAvailable")
    if not isinstance(available, list) or not available:
        errors.append("SEMANTIC_READINESS.json has no evidenceAvailable list")
    return errors


def validate_semantic_source_authority_evaluation(package: Path) -> list[str]:
    errors: list[str] = []
    evaluation = read_json(package / "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json")
    readiness = read_json(package / "SEMANTIC_READINESS.json")
    tasks = read_json(package / "FUNCTION_RECONSTRUCTION_TASKS.json")
    results = read_json(package / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json")
    evaluator_path = package / "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py"
    if evaluation.get("schema") != "mizuchi.one-shot-source-semantic-authority-evaluation.v1":
        errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json schema mismatch")
    if evaluation.get("status") not in ("ready", "not-ready"):
        errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json status mismatch")
    if evaluation.get("semanticDecompilation") is not False:
        errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json claims semantic decompilation")
    task_count = int(tasks.get("taskCount") or 0)
    matched_count = int(results.get("matchedCount") or 0)
    failed_count = int(results.get("failedCount") or 0)
    skipped_count = int(results.get("skippedCount") or 0)
    all_matched = (
        results.get("status") == "matched"
        and task_count > 0
        and matched_count == task_count
        and failed_count == 0
        and skipped_count == 0
    )
    expected_status = "ready" if all_matched and readiness.get("status") == "ready" and not readiness.get("missingForSemanticAuthority") else "not-ready"
    if evaluation.get("status") != expected_status:
        errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json readiness decision mismatch")
    if evaluation.get("candidateReplayStatus") != results.get("status"):
        errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json candidate replay status mismatch")
    if evaluation.get("taskCount") != task_count:
        errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json taskCount mismatch")
    if (
        evaluation.get("matchedCount") != matched_count
        or evaluation.get("failedCount") != failed_count
        or evaluation.get("skippedCount") != skipped_count
    ):
        errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json candidate counts mismatch")
    if evaluation.get("allCandidatesMatched") is not all_matched:
        errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json allCandidatesMatched mismatch")
    if evaluation.get("semanticReadinessStatus") != readiness.get("status"):
        errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json semanticReadinessStatus mismatch")
    if evaluation.get("functionReconstructionTasksSha256") != sha256_file(package / "FUNCTION_RECONSTRUCTION_TASKS.json"):
        errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json task hash mismatch")
    if evaluation.get("functionReconstructionCandidateResultsSha256") != sha256_file(package / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json"):
        errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json candidate results hash mismatch")
    if evaluation.get("semanticReadinessSha256") != sha256_file(package / "SEMANTIC_READINESS.json"):
        errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json readiness hash mismatch")
    if evaluation.get("evaluatorScriptSha256") != sha256_file(evaluator_path):
        errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json evaluator hash mismatch")
    blockers = evaluation.get("blockers")
    if expected_status == "not-ready" and (not isinstance(blockers, list) or not blockers):
        errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json has no blockers")
    elif isinstance(blockers, list):
        blocker_ids = {
            item.get("id")
            for item in blockers
            if isinstance(item, dict)
        }
        expected_blocker_ids: set[str] = set()
        if results.get("status") == "no-candidates" or skipped_count:
            expected_blocker_ids.add("missing-reconstruction-candidates")
        if results.get("status") == "failed" or failed_count:
            expected_blocker_ids.add("candidate-replay-failures")
        if not all_matched:
            expected_blocker_ids.add("all-candidates-not-byte-identical")
        if readiness.get("missingForSemanticAuthority"):
            expected_blocker_ids.add("semantic-evidence-incomplete")
        missing_blockers = sorted(expected_blocker_ids - blocker_ids)
        if missing_blockers:
            errors.append(
                "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json missing blockers: "
                + ", ".join(missing_blockers)
            )
        if expected_status == "ready" and blockers:
            errors.append("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json ready status has blockers")
    return errors


def validate_package_proof(package: Path) -> list[str]:
    errors: list[str] = []
    proof = read_json(package / "PACKAGE_PROOF.json")
    claims = read_json(package / "CLAIMS.json")
    content = read_json(package / "CONTENT_MANIFEST.json")
    candidates = read_json(package / "VERIFIED_SOURCE_CANDIDATES.json")
    binary_evidence = read_json(package / "BINARY_EVIDENCE.json")
    boundary_candidates = read_json(package / "FUNCTION_BOUNDARY_CANDIDATES.json")
    function_byte_slices = read_json(package / "FUNCTION_BYTE_SLICES.json")
    function_slice_sources = read_json(package / "FUNCTION_SLICE_SOURCES.json")
    reconstruction_tasks = read_json(package / "FUNCTION_RECONSTRUCTION_TASKS.json")
    reconstruction_results = read_json(package / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json")
    source_roles = read_json(package / "SOURCE_ROLES.json")
    semantic_readiness = read_json(package / "SEMANTIC_READINESS.json")
    semantic_authority_evaluation = read_json(package / "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json")
    gates = read_json(package / "AUTHORITY_GATES.json")
    toolchain = read_json(package / "TOOLCHAIN_PROVENANCE.json")
    receipt = read_json(package / "one-shot-source-receipt.json")
    authority_summary = read_json(package / "AUTHORITY_SUMMARY.json")
    if proof.get("schema") != "mizuchi.one-shot-source-package-proof.v1":
        errors.append("PACKAGE_PROOF.json schema mismatch")
    if proof.get("status") != "authoritative":
        errors.append("PACKAGE_PROOF.json status is not authoritative")
    if proof.get("authorityClass") != "byte-authoritative-source":
        errors.append("PACKAGE_PROOF.json authorityClass is not byte-authoritative-source")
    if proof.get("accuracyClass") != "byte-exact":
        errors.append("PACKAGE_PROOF.json accuracyClass is not byte-exact")
    if proof.get("authoritySummary") != authority_summary:
        errors.append("PACKAGE_PROOF.json authoritySummary mismatch")
    if proof.get("authoritySummarySha256") != sha256_file(package / "AUTHORITY_SUMMARY.json"):
        errors.append("PACKAGE_PROOF.json authoritySummarySha256 mismatch")
    if proof.get("contentIdentity") != content.get("contentIdentity"):
        errors.append("PACKAGE_PROOF.json contentIdentity mismatch")
    original = proof.get("original")
    if not isinstance(original, dict) or original.get("sha256") != receipt.get("originalSha256"):
        errors.append("PACKAGE_PROOF.json original hash mismatch")
    if proof.get("proven") != claims.get("proven"):
        errors.append("PACKAGE_PROOF.json proven block mismatch")
    if proof.get("notProven") != claims.get("notProven"):
        errors.append("PACKAGE_PROOF.json notProven block mismatch")
    if proof.get("binaryEvidence") != binary_evidence:
        errors.append("PACKAGE_PROOF.json binaryEvidence mismatch")
    if proof.get("functionBoundaryCandidates") != boundary_candidates:
        errors.append("PACKAGE_PROOF.json functionBoundaryCandidates mismatch")
    if proof.get("functionByteSlices") != function_byte_slices:
        errors.append("PACKAGE_PROOF.json functionByteSlices mismatch")
    if proof.get("functionSliceSources") != function_slice_sources:
        errors.append("PACKAGE_PROOF.json functionSliceSources mismatch")
    if proof.get("functionReconstructionTasks") != reconstruction_tasks:
        errors.append("PACKAGE_PROOF.json functionReconstructionTasks mismatch")
    if proof.get("functionReconstructionCandidateResults") != reconstruction_results:
        errors.append("PACKAGE_PROOF.json functionReconstructionCandidateResults mismatch")
    if proof.get("sourceCandidates") != candidates.get("candidates"):
        errors.append("PACKAGE_PROOF.json sourceCandidates mismatch")
    if proof.get("sourceRoles") != source_roles.get("roles"):
        errors.append("PACKAGE_PROOF.json sourceRoles mismatch")
    if proof.get("semanticReadiness") != semantic_readiness:
        errors.append("PACKAGE_PROOF.json semanticReadiness mismatch")
    if proof.get("semanticAuthorityEvaluation") != semantic_authority_evaluation:
        errors.append("PACKAGE_PROOF.json semanticAuthorityEvaluation mismatch")
    evaluator = proof.get("semanticAuthorityEvaluator")
    if not isinstance(evaluator, dict):
        errors.append("PACKAGE_PROOF.json has no semanticAuthorityEvaluator")
    else:
        if evaluator.get("path") != "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py":
            errors.append("PACKAGE_PROOF.json semanticAuthorityEvaluator path mismatch")
        if evaluator.get("sha256") != sha256_file(package / "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py"):
            errors.append("PACKAGE_PROOF.json semanticAuthorityEvaluator hash mismatch")
        if evaluator.get("semanticDecompilation") is not False:
            errors.append("PACKAGE_PROOF.json semanticAuthorityEvaluator claims semantic decompilation")
    receipt_refresher = proof.get("oneShotReceiptRefresher")
    if not isinstance(receipt_refresher, dict):
        errors.append("PACKAGE_PROOF.json has no oneShotReceiptRefresher")
    else:
        if receipt_refresher.get("path") != "REFRESH_RECONSTRUCTION_RECEIPTS.py":
            errors.append("PACKAGE_PROOF.json oneShotReceiptRefresher path mismatch")
        if receipt_refresher.get("sha256") != sha256_file(package / "REFRESH_RECONSTRUCTION_RECEIPTS.py"):
            errors.append("PACKAGE_PROOF.json oneShotReceiptRefresher hash mismatch")
        if receipt_refresher.get("semanticDecompilation") is not False:
            errors.append("PACKAGE_PROOF.json oneShotReceiptRefresher claims semantic decompilation")
    if proof.get("authorityGateStatus") != gates.get("status"):
        errors.append("PACKAGE_PROOF.json authorityGateStatus mismatch")
    if proof.get("authorityGates") != gates.get("gates"):
        errors.append("PACKAGE_PROOF.json authorityGates mismatch")
    toolchain_summary = proof.get("toolchainProvenance")
    if not isinstance(toolchain_summary, dict):
        errors.append("PACKAGE_PROOF.json has no toolchainProvenance")
    else:
        if toolchain_summary.get("status") != toolchain.get("status"):
            errors.append("PACKAGE_PROOF.json toolchain status mismatch")
        if toolchain_summary.get("tools") != toolchain.get("tools"):
            errors.append("PACKAGE_PROOF.json toolchain tools mismatch")
        if toolchain_summary.get("replayEnvironment") != toolchain.get("replayEnvironment"):
            errors.append("PACKAGE_PROOF.json toolchain replay environment mismatch")
    entrypoints = proof.get("replayEntrypoints")
    if not isinstance(entrypoints, dict) or not entrypoints.get("fullPackage"):
        errors.append("PACKAGE_PROOF.json has no full-package replay entrypoints")
    return errors


def validate_toolchain_provenance(package: Path) -> list[str]:
    errors: list[str] = []
    provenance = read_json(package / "TOOLCHAIN_PROVENANCE.json")
    if provenance.get("schema") != "mizuchi.toolchain-provenance.v1":
        errors.append("TOOLCHAIN_PROVENANCE.json schema mismatch")
    if provenance.get("status") != "recorded":
        errors.append("TOOLCHAIN_PROVENANCE.json status is not recorded")
    tools = provenance.get("tools")
    if not isinstance(tools, dict):
        return errors + ["TOOLCHAIN_PROVENANCE.json has no tools object"]
    for name in ("gcc", "objcopy"):
        item = tools.get(name)
        if not isinstance(item, dict):
            errors.append(f"TOOLCHAIN_PROVENANCE.json missing tool: {name}")
            continue
        if not item.get("path"):
            errors.append(f"TOOLCHAIN_PROVENANCE.json has no path for {name}")
    entrypoints = provenance.get("proofEntrypoints")
    if not isinstance(entrypoints, dict) or not entrypoints.get("fullPackage"):
        errors.append("TOOLCHAIN_PROVENANCE.json has no full-package proof entrypoints")
    return errors


def validate_verified_source_candidates(package: Path) -> list[str]:
    errors: list[str] = []
    manifest = read_json(package / "VERIFIED_SOURCE_CANDIDATES.json")
    receipt = read_json(package / "one-shot-source-receipt.json")
    roles_doc = read_json(package / "SOURCE_ROLES.json")
    if manifest.get("schema") != "mizuchi.verified-source-candidates.v1":
        errors.append("VERIFIED_SOURCE_CANDIDATES.json schema mismatch")
    if manifest.get("status") != "authoritative":
        errors.append("VERIFIED_SOURCE_CANDIDATES.json status is not authoritative")
    if manifest.get("authorityClass") != "byte-authoritative-source":
        errors.append("VERIFIED_SOURCE_CANDIDATES.json authorityClass is not byte-authoritative-source")
    if manifest.get("accuracyClass") != "byte-exact":
        errors.append("VERIFIED_SOURCE_CANDIDATES.json accuracyClass is not byte-exact")
    if manifest.get("semanticDecompilation") is not False:
        errors.append("VERIFIED_SOURCE_CANDIDATES.json claims semantic decompilation")
    original = manifest.get("original")
    if not isinstance(original, dict) or original.get("sha256") != receipt.get("originalSha256"):
        errors.append("VERIFIED_SOURCE_CANDIDATES.json original hash mismatch")
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list):
        return errors + ["VERIFIED_SOURCE_CANDIDATES.json has no candidates list"]
    by_path = {item.get("path"): item for item in candidates if isinstance(item, dict)}
    roles_by_path = {item.get("path"): item for item in roles_doc.get("roles", []) if isinstance(item, dict)}
    expected = {
        "full-binary.S": receipt.get("sourceSha256"),
        "full-binary.c": receipt.get("cSourceSha256"),
    }
    for rel, expected_sha in expected.items():
        candidate = by_path.get(rel)
        if not isinstance(candidate, dict):
            errors.append(f"VERIFIED_SOURCE_CANDIDATES.json missing candidate: {rel}")
            continue
        if candidate.get("accuracyClass") != "byte-exact":
            errors.append(f"source candidate accuracyClass is not byte-exact: {rel}")
        if candidate.get("byteIdentical") is not True:
            errors.append(f"source candidate is not byteIdentical: {rel}")
        if candidate.get("semanticDecompilation") is not False:
            errors.append(f"source candidate claims semantic decompilation: {rel}")
        role = roles_by_path.get(rel)
        if not isinstance(role, dict):
            errors.append(f"SOURCE_ROLES.json missing role for source candidate: {rel}")
        elif candidate.get("sourceRole") != role.get("role"):
            errors.append(f"VERIFIED_SOURCE_CANDIDATES.json sourceRole mismatch: {rel}")
        if candidate.get("sha256") != expected_sha:
            errors.append(f"source candidate hash mismatch: {rel}")
        if sha256_file(package / rel) != expected_sha:
            errors.append(f"source candidate file hash mismatch: {rel}")
        evidence = candidate.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"source candidate has no evidence: {rel}")
            continue
        for evidence_rel in evidence:
            if not isinstance(evidence_rel, str) or not (package / evidence_rel).exists():
                errors.append(f"source candidate evidence missing for {rel}: {evidence_rel}")
    supplied = by_path.get("candidate-source.c") or by_path.get("candidate-source-tree")
    if isinstance(supplied, dict):
        supplied_role = roles_by_path.get(str(supplied.get("path")))
        if not isinstance(supplied_role, dict):
            errors.append("SOURCE_ROLES.json missing supplied candidate role")
        elif supplied.get("sourceRole") != supplied_role.get("role"):
            errors.append("VERIFIED_SOURCE_CANDIDATES.json supplied sourceRole mismatch")
        report = read_json(package / "candidate-source-roundtrip.json")
        recipe = read_json(package / "CANDIDATE_BUILD_RECIPE.json")
        if not (package / "REPLAY_CANDIDATE.sh").exists():
            errors.append("missing REPLAY_CANDIDATE.sh")
        if recipe.get("schema") != "mizuchi.candidate-build-recipe.v1":
            errors.append("CANDIDATE_BUILD_RECIPE.json schema mismatch")
        if recipe.get("status") != "authoritative":
            errors.append("CANDIDATE_BUILD_RECIPE.json status is not authoritative")
        if recipe.get("candidatePath") != supplied.get("path"):
            errors.append("CANDIDATE_BUILD_RECIPE.json candidate path mismatch")
        if recipe.get("verificationMode") != supplied.get("verificationMode"):
            errors.append("CANDIDATE_BUILD_RECIPE.json verification mode mismatch")
        expected_output = recipe.get("expectedOutput")
        if not isinstance(expected_output, dict) or expected_output.get("sha256") != receipt.get("originalSha256"):
            errors.append("CANDIDATE_BUILD_RECIPE.json expected output mismatch")
        observed_output = recipe.get("observedGenerationOutput")
        if not isinstance(observed_output, dict) or observed_output.get("sha256") != receipt.get("originalSha256"):
            errors.append("CANDIDATE_BUILD_RECIPE.json observed output mismatch")
        if report.get("schema") != "mizuchi.supplied-source-candidate-roundtrip.v1":
            errors.append("candidate-source-roundtrip.json schema mismatch")
        if report.get("status") != "matched":
            errors.append("candidate-source-roundtrip.json status is not matched")
        if report.get("byteIdentical") is not True:
            errors.append("candidate-source-roundtrip.json is not byte-identical")
        source_tree = supplied.get("sourceTree")
        if isinstance(source_tree, list) and recipe.get("sourceTree") != source_tree:
            errors.append("CANDIDATE_BUILD_RECIPE.json source tree mismatch")
        if not isinstance(source_tree, list) and recipe.get("sourceSha256") != supplied.get("sha256"):
            errors.append("CANDIDATE_BUILD_RECIPE.json source hash mismatch")
        if isinstance(source_tree, list):
            for row in source_tree:
                if not isinstance(row, dict):
                    errors.append("candidate source tree contains non-object row")
                    continue
                rel = row.get("path")
                expected_sha = row.get("sha256")
                if not isinstance(rel, str) or not isinstance(expected_sha, str):
                    errors.append("candidate source tree row is incomplete")
                    continue
                target = package / rel
                if not target.exists():
                    errors.append(f"candidate source tree file missing: {rel}")
                elif sha256_file(target) != expected_sha:
                    errors.append(f"candidate source tree hash mismatch: {rel}")
        elif report.get("sourceSha256") != supplied.get("sha256"):
            errors.append("candidate-source-roundtrip.json source hash mismatch")
        if report.get("emittedSha256") != receipt.get("originalSha256"):
            errors.append("candidate-source-roundtrip.json emitted hash mismatch")
        mode = supplied.get("verificationMode")
        if mode not in ("c-stdout-emitter", "command-output-file"):
            errors.append("candidate-source.c has unsupported verification mode")
        if report.get("verificationMode") != mode:
            errors.append("candidate-source-roundtrip.json verification mode mismatch")
        if mode == "command-output-file" and not supplied.get("replayCommand"):
            errors.append("candidate-source.c command-output-file mode has no replay command")
        if recipe.get("replayCommand") != supplied.get("replayCommand"):
            errors.append("CANDIDATE_BUILD_RECIPE.json replay command mismatch")
    return errors


def validate(
    package: Path,
    expect_content_identity: str | None = None,
    require_complete: bool = False,
    check_external_bundle: bool = True,
    expected_deliverable_phase: str = "final-package-index",
) -> dict[str, Any]:
    errors: list[str] = []
    missing = [name for name in REQUIRED_FILES if not (package / name).exists()]
    errors.extend(f"missing required file: {name}" for name in missing)
    complete_receipt_status: dict[str, bool] = {}
    if require_complete:
        complete_receipts = [
            "receipts/one-shot-source-result.json",
            "receipts/archive-verify.json",
            "receipts/proof.json",
            "receipts/proof.md",
            "receipts/byte-accurate-response-proof.json",
            "receipts/deliverable.json",
            "receipts/bundle-verify.json",
        ]
        for rel in complete_receipts:
            present = (package / rel).exists()
            complete_receipt_status[rel] = present
            if not present:
                errors.append(f"missing complete-mode receipt: {rel}")
    if not missing:
        errors.extend(validate_package_manifest(package))
        errors.extend(validate_content_manifest(package))
        errors.extend(validate_claims(package))
        errors.extend(validate_authority_gates(package))
        errors.extend(
            validate_authority_summary(
                package,
                check_external_bundle=check_external_bundle,
                expected_deliverable_phase=expected_deliverable_phase,
            )
        )
        errors.extend(validate_source_index(package))
        errors.extend(validate_binary_evidence(package))
        errors.extend(validate_function_boundary_candidates(package))
        errors.extend(validate_function_byte_slices(package))
        errors.extend(validate_function_slice_sources(package))
        errors.extend(validate_function_reconstruction_tasks(package))
        errors.extend(validate_function_reconstruction_candidate_results(package))
        errors.extend(validate_one_shot_reconstruction_request(package))
        errors.extend(validate_one_shot_candidate_importer(package))
        errors.extend(validate_one_shot_response_json_importer(package))
        errors.extend(validate_one_shot_response_json_validator(package))
        errors.extend(validate_one_shot_receipt_refresher(package))
        errors.extend(validate_one_shot_response_template(package))
        errors.extend(validate_source_roles(package))
        errors.extend(validate_semantic_readiness(package))
        errors.extend(validate_semantic_source_authority_evaluation(package))
        errors.extend(validate_verified_source_candidates(package))
        errors.extend(validate_toolchain_provenance(package))
        errors.extend(validate_package_proof(package))
        errors.extend(validate_sha256sums(package))
    claims = read_json(package / "CLAIMS.json") if (package / "CLAIMS.json").exists() else {}
    content = read_json(package / "CONTENT_MANIFEST.json") if (package / "CONTENT_MANIFEST.json").exists() else {}
    if require_complete and (package / "receipts" / "deliverable.json").exists():
        deliverable = read_json(package / "receipts" / "deliverable.json")
        if not isinstance(deliverable.get("bundle"), dict):
            errors.append("complete-mode deliverable has no bundle object")
        if not isinstance(deliverable.get("bundleVerifier"), dict):
            errors.append("complete-mode deliverable has no bundleVerifier object")
    content_identity = content.get("contentIdentity") or claims.get("contentIdentity")
    authority_summary_path = package / "AUTHORITY_SUMMARY.json"
    authority_summary_sha = sha256_file(authority_summary_path) if authority_summary_path.exists() else None
    content_identity_matches = expect_content_identity is None or content_identity == expect_content_identity
    if not content_identity_matches:
        errors.append("contentIdentity does not match expected value")
    return {
        "schema": "mizuchi.one-shot-source-validate.v1",
        "package": str(package),
        "status": "valid" if not errors else "invalid",
        "ok": not errors,
        "errors": errors,
        "claimStatus": claims.get("status"),
        "requireComplete": require_complete,
        "completeReceiptsPresent": complete_receipt_status,
        "authorityClass": claims.get("authorityClass"),
        "accuracyClass": claims.get("accuracyClass"),
        "authoritySummarySha256": authority_summary_sha,
        "contentIdentity": content_identity,
        "expectedContentIdentity": expect_content_identity,
        "contentIdentityMatches": content_identity_matches,
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


def validate_archive(path: Path, expect_content_identity: str | None = None, require_complete: bool = False) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mizuchi-one-shot-validate-") as tmp:
        tmp_dir = Path(tmp)
        with tarfile.open(path, "r:gz") as archive:
            members, root = safe_members(archive)
            archive.extractall(tmp_dir, members)
        report = validate(
            tmp_dir / root,
            expect_content_identity=expect_content_identity,
            require_complete=False,
            check_external_bundle=False,
            expected_deliverable_phase="pre-bundle-index",
        )
        if require_complete:
            report["requireComplete"] = True
            report["completeReceiptsPresent"] = {}
            report["completeReceiptScope"] = (
                "source archives are created before complete-mode receipts; "
                "use one-shot-source-deliverable-verify --bundle for the complete receipt chain"
            )
        report["archive"] = str(path)
        report["packageRoot"] = root
        report["package"] = root
        return report


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--package", type=Path)
    group.add_argument("--archive", type=Path)
    parser.add_argument("--expect-content-identity", help="Fail unless the package contentIdentity matches this value.")
    parser.add_argument("--require-complete", action="store_true", help="Require complete-mode receipts and bundle metadata.")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--out", type=Path, help="Write the validation JSON report to this path.")
    args = parser.parse_args()

    report = (
        validate(args.package, expect_content_identity=args.expect_content_identity, require_complete=args.require_complete)
        if args.package
        else validate_archive(args.archive, expect_content_identity=args.expect_content_identity, require_complete=args.require_complete)
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.markdown:
        print("# One-Shot Source Package Validation")
        print()
        print(f"Status: `{report['status']}`")
        if report.get("archive"):
            print(f"Archive: `{report['archive']}`")
            print(f"Package root: `{report['packageRoot']}`")
        else:
            print(f"Package: `{report['package']}`")
        print(f"Authority class: `{report.get('authorityClass')}`")
        print(f"Accuracy class: `{report.get('accuracyClass')}`")
        print(f"Authority summary SHA256: `{report.get('authoritySummarySha256')}`")
        print(f"Content identity: `{report.get('contentIdentity')}`")
        if report.get("expectedContentIdentity"):
            print(f"Content identity matches pin: `{str(report.get('contentIdentityMatches')).lower()}`")
        if report.get("requireComplete"):
            print()
            print("## Complete-mode receipts")
            if report.get("completeReceiptScope"):
                print(report.get("completeReceiptScope"))
            complete_receipts = report.get("completeReceiptsPresent") if isinstance(report.get("completeReceiptsPresent"), dict) else {}
            for key, value in sorted(complete_receipts.items()):
                print(f"- `{key}`: `{str(value).lower()}`")
        if report["errors"]:
            print()
            print("## Errors")
            for error in report["errors"]:
                print(f"- {error}")
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
