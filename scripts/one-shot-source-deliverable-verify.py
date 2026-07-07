#!/usr/bin/env python3
"""Verify a one-shot source deliverable index."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


archive_verify_mod = load_module(
    "mizuchi_one_shot_source_archive_verify",
    ROOT / "scripts" / "one-shot-source-archive-verify.py",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    if not members:
        raise SystemExit("bundle is empty")
    for member in members:
        member_path = Path(member.name)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise SystemExit(f"unsafe bundle member path: {member.name}")
        if member.issym() or member.islnk():
            raise SystemExit(f"bundle links are not allowed: {member.name}")
    return members


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


def resolve_path(value: object, base: Path) -> Path | None:
    if isinstance(value, dict):
        rel = value.get("relativePath")
        if isinstance(rel, str) and rel.strip():
            return base / rel
        value = value.get("path")
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    return path if path.is_absolute() else base / path


def resolve_existing_deliverable_archive(value: object, base: Path) -> Path | None:
    path = resolve_path(value, base)
    if path is None or path.exists():
        return path
    # Portable bundles place the source archive beside the extracted package
    # root, while the deliverable's external relative path may point outside
    # that layout. Only relocate by basename; SHA/content pins still gate trust.
    relocated = base.parent / path.name
    if relocated.exists():
        return relocated
    return path


def verify_deliverable(path: Path, timeout: int) -> dict[str, Any]:
    deliverable = read_json(path)
    base = path.resolve().parent
    errors: list[str] = []
    if deliverable.get("schema") != "mizuchi.one-shot-source-deliverable.v1":
        errors.append("deliverable schema mismatch")
    archive = deliverable.get("archive")
    if not isinstance(archive, dict):
        errors.append("deliverable has no archive object")
        archive_path = None
        expected_archive_sha = None
    else:
        archive_path = resolve_existing_deliverable_archive(archive, base)
        expected_archive_sha = archive.get("sha256") if isinstance(archive.get("sha256"), str) else None
    expected_content_identity = deliverable.get("contentIdentity") if isinstance(deliverable.get("contentIdentity"), str) else None
    authority_summary = deliverable.get("authoritySummary") if isinstance(deliverable.get("authoritySummary"), dict) else {}
    authority_summary_sha = deliverable.get("authoritySummarySha256")
    if not isinstance(authority_summary_sha, str) or not authority_summary_sha:
        errors.append("deliverable has no authoritySummarySha256")
    if not authority_summary:
        errors.append("deliverable has no authoritySummary object")
    replay = None
    if archive_path is None:
        errors.append("deliverable archive path is missing")
    elif not archive_path.exists():
        errors.append(f"deliverable archive is missing: {archive_path}")
    else:
        replay = archive_verify_mod.verify_archive(
            archive_path,
            timeout,
            expect_archive_sha256=expected_archive_sha,
            expect_content_identity=expected_content_identity,
        )
        if replay.get("ok") is not True:
            errors.append("archive replay failed")
        if replay.get("archiveSha256") != expected_archive_sha:
            errors.append("archive SHA256 does not match deliverable pin")
        if replay.get("contentIdentity") != expected_content_identity:
            errors.append("content identity does not match deliverable pin")
        if replay.get("sourceCandidateStatus") != "authoritative":
            errors.append("source candidate status is not authoritative")
        if replay.get("authorityGateStatus") != "passed":
            errors.append("authority gates are not passed")
        if replay.get("packageProofStatus") != "authoritative":
            errors.append("package proof is not authoritative")
        if replay.get("binaryEvidenceStatus") != "recorded":
            errors.append("binary evidence is not recorded")
        if deliverable.get("binaryEvidence") != replay.get("binaryEvidence"):
            errors.append("deliverable binaryEvidence does not match archive replay")
        if deliverable.get("functionBoundaryCandidates") != replay.get("functionBoundaryCandidates"):
            errors.append("deliverable functionBoundaryCandidates do not match archive replay")
        if deliverable.get("functionByteSlices") != replay.get("functionByteSlices"):
            errors.append("deliverable functionByteSlices do not match archive replay")
        if deliverable.get("functionSliceSources") != replay.get("functionSliceSources"):
            errors.append("deliverable functionSliceSources do not match archive replay")
        if deliverable.get("functionReconstructionTasks") != replay.get("functionReconstructionTasks"):
            errors.append("deliverable functionReconstructionTasks do not match archive replay")
        if deliverable.get("functionReconstructionCandidateResults") != replay.get("functionReconstructionCandidateResults"):
            errors.append("deliverable functionReconstructionCandidateResults do not match archive replay")
        if deliverable.get("oneShotReconstructionRequest") != replay.get("oneShotReconstructionRequest"):
            errors.append("deliverable oneShotReconstructionRequest does not match archive replay")
        if deliverable.get("oneShotReconstructionRequestJson") != replay.get("oneShotReconstructionRequestJson"):
            errors.append("deliverable oneShotReconstructionRequestJson does not match archive replay")
        if deliverable.get("oneShotReconstructionBundle") != replay.get("oneShotReconstructionBundle"):
            errors.append("deliverable oneShotReconstructionBundle does not match archive replay")
        if deliverable.get("oneShotCandidateImporter") != replay.get("oneShotCandidateImporter"):
            errors.append("deliverable oneShotCandidateImporter does not match archive replay")
        if deliverable.get("oneShotResponseJsonImporter") != replay.get("oneShotResponseJsonImporter"):
            errors.append("deliverable oneShotResponseJsonImporter does not match archive replay")
        if deliverable.get("oneShotResponseJsonValidator") != replay.get("oneShotResponseJsonValidator"):
            errors.append("deliverable oneShotResponseJsonValidator does not match archive replay")
        if deliverable.get("oneShotReceiptRefresher") != replay.get("oneShotReceiptRefresher"):
            errors.append("deliverable oneShotReceiptRefresher does not match archive replay")
        if deliverable.get("oneShotResponseTemplate") != replay.get("oneShotResponseTemplate"):
            errors.append("deliverable oneShotResponseTemplate does not match archive replay")
        if deliverable.get("oneShotResponseTemplateExporter") != replay.get("oneShotResponseTemplateExporter"):
            errors.append("deliverable oneShotResponseTemplateExporter does not match archive replay")
        if deliverable.get("oneShotByteAccurateResponseExporter") != replay.get("oneShotByteAccurateResponseExporter"):
            errors.append("deliverable oneShotByteAccurateResponseExporter does not match archive replay")
        if deliverable.get("oneShotByteAccurateResponseProver") != replay.get("oneShotByteAccurateResponseProver"):
            errors.append("deliverable oneShotByteAccurateResponseProver does not match archive replay")
        if replay.get("sourceRolesStatus") != "recorded":
            errors.append("source roles are not recorded")
        if deliverable.get("sourceRoles") != replay.get("sourceRoles"):
            errors.append("deliverable sourceRoles do not match archive replay")
        if replay.get("semanticReadinessStatus") not in ("ready", "not-ready"):
            errors.append("semantic readiness is not recorded")
        if deliverable.get("semanticReadiness") != replay.get("semanticReadiness"):
            errors.append("deliverable semanticReadiness does not match archive replay")
        if deliverable.get("semanticAuthorityEvaluation") != replay.get("semanticAuthorityEvaluation"):
            errors.append("deliverable semanticAuthorityEvaluation does not match archive replay")
        if deliverable.get("semanticAuthorityEvaluator") != replay.get("semanticAuthorityEvaluator"):
            errors.append("deliverable semanticAuthorityEvaluator does not match archive replay")
        if replay.get("authorityContractStatus") != "passed":
            errors.append("archive authority contract is not passed")
        expected_summary = {
            "schema": "mizuchi.one-shot-source-authority-summary.v1",
            "status": "authoritative",
            "authorityClass": "byte-authoritative-source",
            "accuracyClass": "byte-exact",
            "authorityContractStatus": "passed",
            "authorityGateStatus": "passed",
            "sourceCandidateStatus": "authoritative",
            "packageProofStatus": "authoritative",
            "contentIdentity": expected_content_identity,
            "semanticDecompilation": False,
        }
        for key, expected in expected_summary.items():
            if authority_summary.get(key) != expected:
                errors.append(f"deliverable authoritySummary mismatch for {key}")
        if authority_summary.get("authorityClass") != replay.get("authorityClass"):
            errors.append("deliverable authoritySummary authorityClass does not match archive replay")
        if authority_summary.get("accuracyClass") != replay.get("accuracyClass"):
            errors.append("deliverable authoritySummary accuracyClass does not match archive replay")
        if authority_summary.get("authorityContractStatus") != replay.get("authorityContractStatus"):
            errors.append("deliverable authoritySummary contract status does not match archive replay")
        replay_summary = replay.get("authoritySummary")
        if not isinstance(replay_summary, dict):
            errors.append("archive replay has no authoritySummary object")
        elif authority_summary != replay_summary:
            errors.append("deliverable authoritySummary does not exactly match archive replay authoritySummary")
        if replay.get("authoritySummarySha256") != authority_summary_sha:
            errors.append("deliverable authoritySummarySha256 does not match archive replay")
    receipts = deliverable.get("receipts")
    receipt_status: dict[str, bool] = {}
    byte_accurate_response_proof: dict[str, Any] | None = None
    byte_accurate_response_proof_status = "missing"
    if isinstance(receipts, dict):
        for key, value in receipts.items():
            receipt_path = resolve_path(value, base)
            receipt_status[key] = bool(receipt_path and receipt_path.exists())
        response_proof_ref = receipts.get("byteAccurateResponseProof")
        response_proof_path = resolve_path(response_proof_ref, base)
        if response_proof_path is None:
            errors.append("deliverable has no byteAccurateResponseProof receipt path")
        elif not response_proof_path.exists():
            errors.append("deliverable byteAccurateResponseProof receipt file is missing")
        else:
            try:
                byte_accurate_response_proof = read_json(response_proof_path)
            except SystemExit as exc:
                errors.append(f"deliverable byteAccurateResponseProof receipt is unreadable: {exc}")
            else:
                byte_accurate_response_proof_status = str(
                    byte_accurate_response_proof.get("status") or "unknown"
                )
                if (
                    byte_accurate_response_proof.get("schema")
                    != "mizuchi.one-shot-source-byte-accurate-response-proof.v1"
                ):
                    errors.append("deliverable byteAccurateResponseProof receipt schema mismatch")
                if byte_accurate_response_proof.get("status") != "matched":
                    errors.append("deliverable byteAccurateResponseProof receipt is not matched")
                if byte_accurate_response_proof.get("ok") is not True:
                    errors.append("deliverable byteAccurateResponseProof receipt is not ok")
                if byte_accurate_response_proof.get("semanticDecompilation") is not False:
                    errors.append("deliverable byteAccurateResponseProof receipt claims semantic decompilation")
                if byte_accurate_response_proof.get("matchedCount") != byte_accurate_response_proof.get("taskCount"):
                    errors.append("deliverable byteAccurateResponseProof matchedCount does not equal taskCount")
                if byte_accurate_response_proof.get("failedCount") != 0:
                    errors.append("deliverable byteAccurateResponseProof has failed candidates")
                if byte_accurate_response_proof.get("skippedCount") != 0:
                    errors.append("deliverable byteAccurateResponseProof has skipped candidates")
                if deliverable.get("oneShotByteAccurateResponseProof") != byte_accurate_response_proof:
                    errors.append("deliverable embedded oneShotByteAccurateResponseProof does not match receipt")
    else:
        errors.append("deliverable receipts must be an object")
    ok = not errors
    return {
        "schema": "mizuchi.one-shot-source-deliverable-verify.v1",
        "deliverable": str(path),
        "status": "matched" if ok else "failed",
        "ok": ok,
        "errors": errors,
        "archive": str(archive_path) if archive_path else None,
        "expectedArchiveSha256": expected_archive_sha,
        "expectedContentIdentity": expected_content_identity,
        "receiptFilesPresent": receipt_status,
        "deliverableStatus": deliverable.get("status"),
        "deliverablePhase": deliverable.get("deliverablePhase"),
        "packageProofStatus": deliverable.get("packageProofStatus"),
        "authorityGateStatus": deliverable.get("authorityGateStatus"),
        "authorityContractStatus": replay.get("authorityContractStatus") if isinstance(replay, dict) else None,
        "authorityClass": replay.get("authorityClass") if isinstance(replay, dict) else None,
        "accuracyClass": replay.get("accuracyClass") if isinstance(replay, dict) else None,
        "deliverableAuthoritySummary": authority_summary,
        "deliverableAuthoritySummarySha256": authority_summary_sha,
        "archiveAuthoritySummary": replay.get("authoritySummary") if isinstance(replay, dict) else None,
        "archiveAuthoritySummarySha256": replay.get("authoritySummarySha256") if isinstance(replay, dict) else None,
        "toolchainProvenanceStatus": (deliverable.get("toolchainProvenance") or {}).get("status")
        if isinstance(deliverable.get("toolchainProvenance"), dict)
        else None,
        "proofCommandsStatus": replay.get("proofCommandsStatus") if isinstance(replay, dict) else None,
        "proofCommands": replay.get("proofCommands") if isinstance(replay, dict) else None,
        "binaryEvidenceStatus": replay.get("binaryEvidenceStatus") if isinstance(replay, dict) else None,
        "binaryEvidence": replay.get("binaryEvidence") if isinstance(replay, dict) else None,
        "functionBoundaryCandidateStatus": replay.get("functionBoundaryCandidateStatus") if isinstance(replay, dict) else None,
        "functionBoundaryCandidateCount": replay.get("functionBoundaryCandidateCount") if isinstance(replay, dict) else None,
        "functionBoundaryCandidates": replay.get("functionBoundaryCandidates") if isinstance(replay, dict) else None,
        "functionByteSliceStatus": replay.get("functionByteSliceStatus") if isinstance(replay, dict) else None,
        "functionByteSliceCount": replay.get("functionByteSliceCount") if isinstance(replay, dict) else None,
        "functionByteSlices": replay.get("functionByteSlices") if isinstance(replay, dict) else None,
        "functionSliceSourceStatus": replay.get("functionSliceSourceStatus") if isinstance(replay, dict) else None,
        "functionSliceSourceCount": replay.get("functionSliceSourceCount") if isinstance(replay, dict) else None,
        "functionSliceSources": replay.get("functionSliceSources") if isinstance(replay, dict) else None,
        "functionReconstructionTaskStatus": replay.get("functionReconstructionTaskStatus") if isinstance(replay, dict) else None,
        "functionReconstructionTaskCount": replay.get("functionReconstructionTaskCount") if isinstance(replay, dict) else None,
        "functionReconstructionTasks": replay.get("functionReconstructionTasks") if isinstance(replay, dict) else None,
        "functionReconstructionCandidateResultStatus": replay.get("functionReconstructionCandidateResultStatus") if isinstance(replay, dict) else None,
        "functionReconstructionCandidateResults": replay.get("functionReconstructionCandidateResults") if isinstance(replay, dict) else None,
        "oneShotReconstructionRequest": replay.get("oneShotReconstructionRequest") if isinstance(replay, dict) else None,
        "oneShotReconstructionRequestJson": replay.get("oneShotReconstructionRequestJson") if isinstance(replay, dict) else None,
        "oneShotReconstructionBundle": replay.get("oneShotReconstructionBundle") if isinstance(replay, dict) else None,
        "oneShotCandidateImporter": replay.get("oneShotCandidateImporter") if isinstance(replay, dict) else None,
        "oneShotResponseJsonImporter": replay.get("oneShotResponseJsonImporter") if isinstance(replay, dict) else None,
        "oneShotResponseJsonValidator": replay.get("oneShotResponseJsonValidator") if isinstance(replay, dict) else None,
        "oneShotReceiptRefresher": replay.get("oneShotReceiptRefresher") if isinstance(replay, dict) else None,
        "oneShotResponseTemplate": replay.get("oneShotResponseTemplate") if isinstance(replay, dict) else None,
        "oneShotResponseTemplateExporter": replay.get("oneShotResponseTemplateExporter") if isinstance(replay, dict) else None,
        "oneShotByteAccurateResponseExporter": replay.get("oneShotByteAccurateResponseExporter") if isinstance(replay, dict) else None,
        "oneShotByteAccurateResponseProver": replay.get("oneShotByteAccurateResponseProver") if isinstance(replay, dict) else None,
        "oneShotByteAccurateResponseProofStatus": byte_accurate_response_proof_status,
        "oneShotByteAccurateResponseProof": byte_accurate_response_proof,
        "embeddedOneShotByteAccurateResponseProof": deliverable.get("oneShotByteAccurateResponseProof"),
        "sourceRolesStatus": replay.get("sourceRolesStatus") if isinstance(replay, dict) else None,
        "sourceRoleCount": len(replay.get("sourceRoles") or []) if isinstance(replay, dict) else 0,
        "sourceRoles": replay.get("sourceRoles") if isinstance(replay, dict) else None,
        "semanticReadinessStatus": replay.get("semanticReadinessStatus") if isinstance(replay, dict) else None,
        "semanticReadiness": replay.get("semanticReadiness") if isinstance(replay, dict) else None,
        "semanticAuthorityEvaluationStatus": replay.get("semanticAuthorityEvaluationStatus") if isinstance(replay, dict) else None,
        "semanticAuthorityEvaluation": replay.get("semanticAuthorityEvaluation") if isinstance(replay, dict) else None,
        "semanticAuthorityEvaluator": replay.get("semanticAuthorityEvaluator") if isinstance(replay, dict) else None,
        "sourceCandidateCount": len(deliverable.get("sourceCandidates") or [])
        if isinstance(deliverable.get("sourceCandidates"), list)
        else 0,
        "archiveReplay": replay,
    }


def verify_bundle(path: Path, timeout: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mizuchi-one-shot-deliverable-bundle-") as tmp:
        tmp_dir = Path(tmp)
        with tarfile.open(path, "r:gz") as archive:
            members = safe_members(archive)
            archive.extractall(tmp_dir, members)
        errors: list[str] = []
        bundle_manifest_errors: list[str] = []
        bundle_manifest_status = "matched"
        bundle_manifest_path = tmp_dir / "BUNDLE_MANIFEST.json"
        bundle_manifest: dict[str, Any] | None = None
        bundle_manifest_sha256 = sha256_file(bundle_manifest_path) if bundle_manifest_path.exists() else None
        try:
            bundle_manifest = read_json(bundle_manifest_path)
        except SystemExit as exc:
            bundle_manifest_errors.append(str(exc))
        if bundle_manifest is not None:
            if bundle_manifest.get("schema") != "mizuchi.one-shot-source-deliverable-bundle-manifest.v1":
                bundle_manifest_errors.append("bundle manifest schema mismatch")
            manifest_authority_summary_sha = bundle_manifest.get("authoritySummarySha256")
            if not isinstance(manifest_authority_summary_sha, str) or not manifest_authority_summary_sha:
                bundle_manifest_errors.append("bundle manifest has no authoritySummarySha256")
            manifest_members = bundle_manifest.get("members")
            if not isinstance(manifest_members, list):
                bundle_manifest_errors.append("bundle manifest members must be a list")
            else:
                for row in manifest_members:
                    if not isinstance(row, dict):
                        bundle_manifest_errors.append("bundle manifest member row must be an object")
                        continue
                    member_path_value = row.get("path")
                    if not isinstance(member_path_value, str) or not member_path_value.strip():
                        bundle_manifest_errors.append("bundle manifest member has no path")
                        continue
                    member_rel = Path(member_path_value)
                    if member_rel.is_absolute() or ".." in member_rel.parts:
                        bundle_manifest_errors.append(f"unsafe bundle manifest member path: {member_path_value}")
                        continue
                    member_file = tmp_dir / member_rel
                    if not member_file.is_file():
                        bundle_manifest_errors.append(f"bundle manifest member is missing: {member_path_value}")
                        continue
                    if row.get("size") != member_file.stat().st_size:
                        bundle_manifest_errors.append(f"bundle manifest size mismatch: {member_path_value}")
                    expected_sha = row.get("sha256")
                    if not isinstance(expected_sha, str) or sha256_file(member_file) != expected_sha:
                        bundle_manifest_errors.append(f"bundle manifest SHA256 mismatch: {member_path_value}")
        if bundle_manifest_errors:
            bundle_manifest_status = "failed"
            errors.extend(bundle_manifest_errors)
        deliverables = sorted(tmp_dir.glob("*/receipts/deliverable.json"))
        if len(deliverables) != 1:
            return {
                "schema": "mizuchi.one-shot-source-deliverable-verify.v1",
                "bundle": str(path),
                "status": "failed",
                "ok": False,
                "errors": errors + [f"bundle must contain exactly one */receipts/deliverable.json, found {len(deliverables)}"],
                "bundleManifestStatus": bundle_manifest_status,
            }
        report = verify_deliverable(deliverables[0], timeout)
        if bundle_manifest is not None:
            if bundle_manifest.get("authoritySummarySha256") != report.get("deliverableAuthoritySummarySha256"):
                bundle_manifest_errors.append("bundle manifest authoritySummarySha256 does not match deliverable verification")
            if bundle_manifest.get("contentIdentity") != report.get("expectedContentIdentity"):
                bundle_manifest_errors.append("bundle manifest contentIdentity does not match deliverable verification")
        if bundle_manifest_errors:
            bundle_manifest_status = "failed"
            for error in bundle_manifest_errors:
                if error not in errors:
                    errors.append(error)
        if errors:
            report["errors"] = errors + list(report.get("errors") or [])
            report["ok"] = False
            report["status"] = "failed"
        report["bundle"] = str(path)
        report["bundleDeliverable"] = str(deliverables[0])
        report["bundleManifest"] = str(bundle_manifest_path)
        report["bundleManifestStatus"] = bundle_manifest_status
        report["bundleManifestSha256"] = bundle_manifest_sha256
        report["bundleManifestAuthoritySummarySha256"] = bundle_manifest.get("authoritySummarySha256") if isinstance(bundle_manifest, dict) else None
        report["bundleManifestContentIdentity"] = bundle_manifest.get("contentIdentity") if isinstance(bundle_manifest, dict) else None
        return report


def print_markdown(report: dict[str, Any]) -> None:
    print("# One-Shot Source Deliverable Verification")
    print()
    print(f"Status: `{report['status']}`")
    print(f"Deliverable: `{report.get('deliverable')}`")
    if report.get("bundle"):
        print(f"Bundle: `{report.get('bundle')}`")
        print(f"Bundle manifest: `{report.get('bundleManifestStatus')}`")
        print(f"Bundle manifest SHA256: `{report.get('bundleManifestSha256')}`")
        print(f"Bundle manifest authority summary SHA256: `{report.get('bundleManifestAuthoritySummarySha256')}`")
        print(f"Bundle manifest content identity: `{report.get('bundleManifestContentIdentity')}`")
    print(f"Archive: `{report.get('archive')}`")
    print(f"Expected archive SHA256: `{report.get('expectedArchiveSha256')}`")
    print(f"Expected content identity: `{report.get('expectedContentIdentity')}`")
    print(f"Deliverable phase: `{report.get('deliverablePhase')}`")
    print(f"Package proof: `{report.get('packageProofStatus')}`")
    print(f"Authority gates: `{report.get('authorityGateStatus')}`")
    print(f"Authority contract: `{report.get('authorityContractStatus')}`")
    print(f"Authority class: `{report.get('authorityClass')}`")
    print(f"Accuracy class: `{report.get('accuracyClass')}`")
    summary = report.get("deliverableAuthoritySummary") if isinstance(report.get("deliverableAuthoritySummary"), dict) else {}
    if summary:
        print(f"Deliverable summary authority: `{summary.get('authorityClass')}`")
        print(f"Deliverable summary accuracy: `{summary.get('accuracyClass')}`")
    print(f"Toolchain provenance: `{report.get('toolchainProvenanceStatus')}`")
    print(f"Proof commands: `{report.get('proofCommandsStatus')}`")
    print(f"Binary evidence: `{report.get('binaryEvidenceStatus')}`")
    print(f"Function boundary candidates: `{report.get('functionBoundaryCandidateStatus')}`")
    print(f"Function boundary candidate count: `{report.get('functionBoundaryCandidateCount')}`")
    print(f"Function byte slices: `{report.get('functionByteSliceStatus')}`")
    print(f"Function byte slice count: `{report.get('functionByteSliceCount')}`")
    print(f"Function slice sources: `{report.get('functionSliceSourceStatus')}`")
    print(f"Function slice source count: `{report.get('functionSliceSourceCount')}`")
    print(f"Function reconstruction tasks: `{report.get('functionReconstructionTaskStatus')}`")
    print(f"Function reconstruction task count: `{report.get('functionReconstructionTaskCount')}`")
    print(f"Function reconstruction candidate results: `{report.get('functionReconstructionCandidateResultStatus')}`")
    print(f"Source roles: `{report.get('sourceRolesStatus')}`")
    print(f"Source role count: `{report.get('sourceRoleCount')}`")
    print(f"Semantic readiness: `{report.get('semanticReadinessStatus')}`")
    print(f"Semantic authority evaluation: `{report.get('semanticAuthorityEvaluationStatus')}`")
    print(f"Source candidate count: `{report.get('sourceCandidateCount')}`")
    if report.get("errors"):
        print()
        print("## Errors")
        for error in report["errors"]:
            print(f"- {error}")
    print()
    print("## Receipt files")
    receipts = report.get("receiptFilesPresent") if isinstance(report.get("receiptFilesPresent"), dict) else {}
    for key, value in sorted(receipts.items()):
        print(f"- `{key}`: `{str(value).lower()}`")


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--deliverable", type=Path)
    group.add_argument("--bundle", type=Path)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args()

    report = verify_deliverable(args.deliverable, args.timeout) if args.deliverable else verify_bundle(args.bundle, args.timeout)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.markdown:
        print_markdown(report)
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
