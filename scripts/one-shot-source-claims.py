#!/usr/bin/env python3
"""Summarize one-shot source package claims without replaying verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import tempfile
from pathlib import Path
from typing import Any


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def summarize_package(package_dir: Path, artifact: str) -> dict[str, Any]:
    claims = read_json(package_dir / "CLAIMS.json")
    content = read_json(package_dir / "CONTENT_MANIFEST.json")
    source_index = read_json(package_dir / "SOURCE_INDEX.json")
    authority_gates = read_json(package_dir / "AUTHORITY_GATES.json")
    source_candidates = read_json(package_dir / "VERIFIED_SOURCE_CANDIDATES.json")
    candidate_recipe = read_json(package_dir / "CANDIDATE_BUILD_RECIPE.json") if (package_dir / "CANDIDATE_BUILD_RECIPE.json").exists() else {}
    package_proof = read_json(package_dir / "PACKAGE_PROOF.json")
    toolchain = read_json(package_dir / "TOOLCHAIN_PROVENANCE.json")
    authority_summary_path = package_dir / "AUTHORITY_SUMMARY.json"
    return {
        "schema": "reconkit.one-shot-source-claims-summary.v1",
        "artifact": artifact,
        "package": str(package_dir),
        "claimStatus": claims.get("status"),
        "authorityClass": claims.get("authorityClass"),
        "accuracyClass": claims.get("accuracyClass"),
        "authoritySummarySha256": sha256_file(authority_summary_path) if authority_summary_path.exists() else None,
        "authorityGateStatus": authority_gates.get("status"),
        "authorityGates": authority_gates.get("gates"),
        "sourceCandidateStatus": source_candidates.get("status"),
        "sourceCandidates": source_candidates.get("candidates"),
        "packageProofStatus": package_proof.get("status"),
        "packageProofReplayEntrypoints": package_proof.get("replayEntrypoints"),
        "toolchainProvenanceStatus": toolchain.get("status"),
        "toolchainTools": toolchain.get("tools"),
        "candidateBuildRecipe": {
            "status": candidate_recipe.get("status"),
            "candidatePath": candidate_recipe.get("candidatePath"),
            "verificationMode": candidate_recipe.get("verificationMode"),
        }
        if candidate_recipe
        else None,
        "contentIdentity": content.get("contentIdentity") or claims.get("contentIdentity"),
        "contentIdentityScope": content.get("identityScope") or claims.get("contentIdentityScope"),
        "proven": claims.get("proven"),
        "notProven": claims.get("notProven"),
        "sources": [
            {
                "path": item.get("path"),
                "language": item.get("language"),
                "authority": item.get("authority"),
                "semanticDecompilation": item.get("semanticDecompilation"),
                "sha256": item.get("sha256"),
            }
            for item in source_index.get("sources", [])
            if isinstance(item, dict)
        ],
    }


def summarize_archive(path: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="reconkit-one-shot-claims-") as tmp:
        tmp_dir = Path(tmp)
        with tarfile.open(path, "r:gz") as archive:
            members, root = safe_members(archive)
            wanted = {
                f"{root}/AUTHORITY_GATES.json",
                f"{root}/AUTHORITY_SUMMARY.json",
                f"{root}/CANDIDATE_BUILD_RECIPE.json",
                f"{root}/CLAIMS.json",
                f"{root}/CONTENT_MANIFEST.json",
                f"{root}/PACKAGE_PROOF.json",
                f"{root}/SOURCE_INDEX.json",
                f"{root}/TOOLCHAIN_PROVENANCE.json",
                f"{root}/VERIFIED_SOURCE_CANDIDATES.json",
            }
            archive.extractall(tmp_dir, [member for member in members if member.name in wanted])
        return summarize_package(tmp_dir / root, str(path))


def print_markdown(summary: dict[str, Any]) -> None:
    print("# One-Shot Source Claims")
    print()
    print(f"Artifact: `{summary['artifact']}`")
    print(f"Claim status: `{summary.get('claimStatus')}`")
    print(f"Authority class: `{summary.get('authorityClass')}`")
    print(f"Accuracy class: `{summary.get('accuracyClass')}`")
    print(f"Content identity: `{summary.get('contentIdentity')}`")
    print(f"Package proof: `{summary.get('packageProofStatus')}`")
    print(f"Toolchain provenance: `{summary.get('toolchainProvenanceStatus')}`")
    print()
    print("## Sources")
    for item in summary.get("sources", []):
        print(
            f"- `{item.get('path')}`: `{item.get('language')}`, `{item.get('authority')}`, "
            f"semanticDecompilation=`{str(item.get('semanticDecompilation')).lower()}`"
        )
    print()
    print("## Verified source candidates")
    candidates = summary.get("sourceCandidates") if isinstance(summary.get("sourceCandidates"), list) else []
    for item in candidates:
        if isinstance(item, dict):
            print(
                f"- `{item.get('path')}`: `{item.get('language')}`, "
                f"accuracyClass=`{item.get('accuracyClass')}`, "
                f"mode=`{item.get('verificationMode')}`, "
                f"byteIdentical=`{str(item.get('byteIdentical')).lower()}`"
            )
    recipe = summary.get("candidateBuildRecipe")
    if isinstance(recipe, dict):
        print()
        print("## Candidate build recipe")
        print(f"- status: `{recipe.get('status')}`")
        print(f"- candidate: `{recipe.get('candidatePath')}`")
        print(f"- mode: `{recipe.get('verificationMode')}`")
    print()
    print("## Proven")
    proven = summary.get("proven") if isinstance(summary.get("proven"), dict) else {}
    for key, value in sorted(proven.items()):
        if value is None:
            continue
        print(f"- `{key}`: `{str(value).lower()}`")
    print()
    print("## Authority gates")
    gates = summary.get("authorityGates") if isinstance(summary.get("authorityGates"), list) else []
    for gate in gates:
        if isinstance(gate, dict):
            print(f"- `{gate.get('id')}`: `{gate.get('status')}`")
    print()
    print("## Not proven")
    not_proven = summary.get("notProven") if isinstance(summary.get("notProven"), list) else []
    for item in not_proven:
        print(f"- {item}")


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--package", type=Path)
    group.add_argument("--archive", type=Path)
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--out", type=Path, help="Write the claims summary JSON to this path.")
    args = parser.parse_args()

    summary = summarize_package(args.package, str(args.package)) if args.package else summarize_archive(args.archive)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    if args.markdown:
        print_markdown(summary)
    else:
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
