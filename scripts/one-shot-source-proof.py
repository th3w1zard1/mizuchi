#!/usr/bin/env python3
"""Aggregate one-shot source package proof surfaces."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
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


claims_mod = load_module("one_shot_claims", ROOT / "scripts" / "one-shot-source-claims.py")
validate_mod = load_module("one_shot_validate", ROOT / "scripts" / "one-shot-source-validate.py")
verify_mod = load_module("one_shot_verify", ROOT / "scripts" / "one-shot-source-verify.py")
archive_verify_mod = load_module("one_shot_archive_verify", ROOT / "scripts" / "one-shot-source-archive-verify.py")


def prove_package(package: Path, timeout: int, expect_content_identity: str | None) -> dict[str, Any]:
    validation = validate_mod.validate(package, expect_content_identity=expect_content_identity)
    claims = claims_mod.summarize_package(package, str(package))
    verification = verify_mod.verify_package(package, timeout, expect_content_identity=expect_content_identity)
    ok = validation.get("ok") is True and verification.get("status") == "authoritative"
    return {
        "schema": "reconkit.one-shot-source-proof.v1",
        "artifact": str(package),
        "artifactType": "package",
        "status": "authoritative" if ok else "failed",
        "ok": ok,
        "contentIdentity": verification.get("contentIdentity") or claims.get("contentIdentity"),
        "validation": validation,
        "claims": claims,
        "verification": verification,
    }


def prove_archive(
    archive: Path,
    timeout: int,
    expect_archive_sha256: str | None,
    expect_content_identity: str | None,
) -> dict[str, Any]:
    validation = validate_mod.validate_archive(archive, expect_content_identity=expect_content_identity)
    claims = claims_mod.summarize_archive(archive)
    verification = archive_verify_mod.verify_archive(
        archive,
        timeout,
        expect_archive_sha256=expect_archive_sha256,
        expect_content_identity=expect_content_identity,
    )
    ok = validation.get("ok") is True and verification.get("ok") is True
    return {
        "schema": "reconkit.one-shot-source-proof.v1",
        "artifact": str(archive),
        "artifactType": "archive",
        "status": "authoritative" if ok else "failed",
        "ok": ok,
        "archiveSha256": verification.get("archiveSha256"),
        "contentIdentity": verification.get("contentIdentity") or claims.get("contentIdentity"),
        "validation": validation,
        "claims": claims,
        "verification": verification,
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# One-Shot Source Proof",
        "",
        f"Status: `{report['status']}`",
        f"Artifact: `{report['artifact']}`",
        f"Artifact type: `{report['artifactType']}`",
    ]
    if report.get("archiveSha256"):
        lines.append(f"Archive SHA256: `{report['archiveSha256']}`")
    lines.extend(
        [
            f"Content identity: `{report.get('contentIdentity')}`",
            f"Authority class: `{report.get('claims', {}).get('authorityClass')}`",
            f"Accuracy class: `{report.get('claims', {}).get('accuracyClass')}`",
            f"Authority summary SHA256: `{report.get('claims', {}).get('authoritySummarySha256')}`",
            f"Validation: `{report['validation'].get('status')}`",
            f"Verification: `{report['verification'].get('status')}`",
            "",
            "## Proven",
        ]
    )
    proven = report.get("claims", {}).get("proven")
    if isinstance(proven, dict):
        for key, value in sorted(proven.items()):
            if value is None:
                continue
            lines.append(f"- `{key}`: `{str(value).lower()}`")
    lines.extend(["", "## Verified source candidates"])
    candidates = report.get("claims", {}).get("sourceCandidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            if isinstance(candidate, dict):
                lines.append(
                    f"- `{candidate.get('path')}`: `{candidate.get('language')}`, "
                    f"accuracyClass=`{candidate.get('accuracyClass')}`, "
                    f"mode=`{candidate.get('verificationMode')}`, "
                    f"byteIdentical=`{str(candidate.get('byteIdentical')).lower()}`"
                )
    recipe = report.get("claims", {}).get("candidateBuildRecipe")
    if isinstance(recipe, dict):
        lines.extend(
            [
                "",
                "## Candidate build recipe",
                f"- status: `{recipe.get('status')}`",
                f"- candidate: `{recipe.get('candidatePath')}`",
                f"- mode: `{recipe.get('verificationMode')}`",
            ]
        )
    lines.extend(["", "## Authority gates"])
    gates = report.get("claims", {}).get("authorityGates")
    if isinstance(gates, list):
        for gate in gates:
            if isinstance(gate, dict):
                lines.append(f"- `{gate.get('id')}`: `{gate.get('status')}`")
    proof_commands = report.get("verification", {}).get("proofCommands")
    entrypoints = proof_commands.get("entrypoints") if isinstance(proof_commands, dict) else None
    if isinstance(entrypoints, dict):
        lines.extend(["", "## Response replay entrypoints"])
        for key in (
            "responseJsonPreflight",
            "responseJsonImport",
            "responseJsonPreflightWithBuildCommand",
            "responseJsonImportWithBuildCommand",
        ):
            commands = entrypoints.get(key)
            if isinstance(commands, list) and commands:
                lines.append(f"- `{key}`: " + "; ".join(f"`{command}`" for command in commands))
        lines.append(
            "- `build.command` overrides require the `--allow-build-command` entrypoints and must write `$CANDIDATE_OUTPUT`."
        )
    lines.extend(["", "## Not proven"])
    not_proven = report.get("claims", {}).get("notProven")
    if isinstance(not_proven, list):
        for item in not_proven:
            lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--package", type=Path)
    group.add_argument("--archive", type=Path)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--expect-content-identity")
    parser.add_argument("--expect-archive-sha256")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args()

    report = (
        prove_package(args.package, args.timeout, args.expect_content_identity)
        if args.package
        else prove_archive(args.archive, args.timeout, args.expect_archive_sha256, args.expect_content_identity)
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    markdown = markdown_report(report)
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(markdown)
    if args.markdown:
        print(markdown, end="")
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
