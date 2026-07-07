#!/usr/bin/env python3
"""Replay verification for a one-shot authoritative source package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def compiler_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("CCACHE_DISABLE", "1")
    env.setdefault("CCACHE_DIR", str(ROOT / "target" / ".ccache"))
    return env


def run(args: list[str], timeout: int, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False, timeout=timeout, env=compiler_env())


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


def resolve_package_path(value: object, package_dir: Path) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return package_dir / path


def verify_package_manifest(package_dir: Path) -> dict[str, Any]:
    manifest_path = package_dir / "package-manifest.json"
    try:
        manifest = read_json(manifest_path)
    except SystemExit as exc:
        return {
            "manifest": str(manifest_path),
            "status": "missing",
            "matched": False,
            "error": str(exc),
            "files": [],
        }
    files = manifest.get("files")
    if not isinstance(files, list):
        return {
            "manifest": str(manifest_path),
            "status": "failed",
            "matched": False,
            "error": "package-manifest.json has no files list",
            "files": [],
        }
    rows: list[dict[str, Any]] = []
    matched = True
    for item in files:
        if not isinstance(item, dict):
            continue
        rel = str(item.get("path") or "")
        expected_sha = str(item.get("sha256") or "")
        path = package_dir / rel
        if not rel or not expected_sha or not path.exists():
            rows.append({"path": rel, "matched": False, "error": "missing path or expected hash"})
            matched = False
            continue
        actual_sha = sha256_file(path)
        ok = actual_sha == expected_sha
        rows.append(
            {
                "path": rel,
                "matched": ok,
                "expectedSha256": expected_sha,
                "actualSha256": actual_sha,
                "expectedSize": item.get("size"),
                "actualSize": path.stat().st_size,
            }
        )
        if not ok:
            matched = False
    return {
        "manifest": str(manifest_path),
        "status": "matched" if matched else "failed",
        "matched": matched,
        "files": rows,
    }


def verify_c_source(package_dir: Path, receipt: dict[str, Any], timeout: int) -> dict[str, Any]:
    source = resolve_package_path(receipt.get("cSource"), package_dir)
    expected_sha = str(receipt.get("originalSha256") or "")
    expected_source_sha = str(receipt.get("cSourceSha256") or "")
    if source is None or not source.exists():
        return {"status": "skipped", "byteIdentical": False, "reason": "no packaged C source"}
    source_sha = sha256_file(source)
    emitter = package_dir / "verify-full-binary-c-emitter"
    emitted = package_dir / "verify-full-binary-c-output.bin"
    try:
        compile_proc = run(["gcc", "-O2", source.name, "-o", emitter.name], timeout, package_dir)
    except subprocess.TimeoutExpired:
        return {
            "source": str(source),
            "status": "failed",
            "byteIdentical": False,
            "error": f"C emitter compile timed out after {timeout}s",
        }
    run_proc = subprocess.CompletedProcess([str(emitter)], 1, "", "compile failed")
    if compile_proc.returncode == 0:
        try:
            with emitted.open("wb") as fh:
                run_proc = subprocess.run(
                    [str(emitter)],
                    cwd=package_dir,
                    stdout=fh,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=timeout,
                )
        except subprocess.TimeoutExpired:
            run_proc = subprocess.CompletedProcess([str(emitter)], 124, b"", b"C emitter run timed out")
    emitted_sha = sha256_file(emitted) if emitted.exists() else ""
    source_matches = not expected_source_sha or source_sha == expected_source_sha
    byte_identical = compile_proc.returncode == 0 and run_proc.returncode == 0 and emitted_sha == expected_sha and source_matches
    return {
        "source": str(source),
        "sourceSha256": source_sha,
        "expectedSourceSha256": expected_source_sha,
        "sourceMatchesReceipt": source_matches,
        "emitter": str(emitter),
        "emittedBinary": str(emitted),
        "status": "matched" if byte_identical else "failed",
        "byteIdentical": byte_identical,
        "expectedSha256": expected_sha,
        "emittedSha256": emitted_sha,
        "compileReturnCode": compile_proc.returncode,
        "compileStdout": compile_proc.stdout[-4000:],
        "compileStderr": compile_proc.stderr[-4000:],
        "runReturnCode": run_proc.returncode,
        "runStderr": (run_proc.stderr or b"")[-4000:].decode("utf-8", errors="replace")
        if isinstance(run_proc.stderr, bytes)
        else str(run_proc.stderr or "")[-4000:],
    }


def run_package_local_verifier(package_dir: Path, timeout: int) -> dict[str, Any]:
    verifier = package_dir / "VERIFY.py"
    if not verifier.exists():
        return {
            "status": "missing",
            "ok": False,
            "error": "package has no VERIFY.py",
        }
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
            "status": "failed",
            "ok": False,
            "error": f"VERIFY.py timed out after {timeout}s",
        }
    ok = proc.returncode == 0 and "ONE_SHOT_SOURCE_PACKAGE_OK" in proc.stdout
    return {
        "status": "matched" if ok else "failed",
        "ok": ok,
        "returnCode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def read_optional_json(path: Path) -> dict[str, Any]:
    try:
        return read_json(path)
    except SystemExit:
        return {}


def verify_package(package_dir: Path, timeout: int, expect_content_identity: str | None = None) -> dict[str, Any]:
    receipt_path = package_dir / "one-shot-source-receipt.json"
    receipt = read_json(receipt_path)
    source = resolve_package_path(receipt.get("source"), package_dir)
    expected_sha = str(receipt.get("originalSha256") or "")
    if source is None:
        return {
            "schema": "reconkit.one-shot-source-verify.v1",
            "package": str(package_dir),
            "status": "failed",
            "byteIdentical": False,
            "error": "receipt has no source path",
        }
    if not source.exists():
        return {
            "schema": "reconkit.one-shot-source-verify.v1",
            "package": str(package_dir),
            "source": str(source),
            "status": "failed",
            "byteIdentical": False,
            "error": "source is missing",
        }
    if not expected_sha:
        return {
            "schema": "reconkit.one-shot-source-verify.v1",
            "package": str(package_dir),
            "source": str(source),
            "status": "failed",
            "byteIdentical": False,
            "error": "receipt has no originalSha256",
        }

    object_path = package_dir / "verify-full-binary.o"
    rebuilt_path = package_dir / "verify-rebuilt.bin"
    try:
        compile_proc = run(
            ["gcc", "-x", "assembler-with-cpp", "-c", source.name, "-o", object_path.name],
            timeout,
            package_dir,
        )
    except subprocess.TimeoutExpired:
        return {
            "schema": "reconkit.one-shot-source-verify.v1",
            "package": str(package_dir),
            "source": str(source),
            "status": "failed",
            "byteIdentical": False,
            "error": f"assembler compile timed out after {timeout}s",
        }

    objcopy_proc = subprocess.CompletedProcess(["objcopy"], 1, "", "compile failed")
        if compile_proc.returncode == 0:
        try:
            objcopy_proc = run(
                ["objcopy", "-O", "binary", "-j", ".reconkit_image", object_path.name, rebuilt_path.name],
                timeout,
                package_dir,
            )
        except subprocess.TimeoutExpired:
            objcopy_proc = subprocess.CompletedProcess(["objcopy"], 124, "", f"objcopy timed out after {timeout}s")

    actual_sha = sha256_file(rebuilt_path) if rebuilt_path.exists() else ""
    source_sha = sha256_file(source)
    expected_source_sha = str(receipt.get("sourceSha256") or "")
    source_matches_receipt = not expected_source_sha or source_sha == expected_source_sha
    rebuilt_size = rebuilt_path.stat().st_size if rebuilt_path.exists() else 0
    package_manifest = verify_package_manifest(package_dir)
    c_source_report = verify_c_source(package_dir, receipt, timeout)
    package_local_verifier = run_package_local_verifier(package_dir, timeout)
    claims = read_optional_json(package_dir / "CLAIMS.json")
    content = read_optional_json(package_dir / "CONTENT_MANIFEST.json")
    source_index = read_optional_json(package_dir / "SOURCE_INDEX.json")
    source_roles = read_optional_json(package_dir / "SOURCE_ROLES.json")
    content_identity = content.get("contentIdentity") or claims.get("contentIdentity")
    content_identity_matches = expect_content_identity is None or content_identity == expect_content_identity
    byte_identical = (
        compile_proc.returncode == 0
        and objcopy_proc.returncode == 0
        and actual_sha == expected_sha
        and source_matches_receipt
        and package_manifest.get("matched") is True
        and c_source_report.get("byteIdentical") is True
        and package_local_verifier.get("ok") is True
        and content_identity_matches
    )
    report = {
        "schema": "reconkit.one-shot-source-verify.v1",
        "package": str(package_dir),
        "receipt": str(receipt_path),
        "source": str(source),
        "object": str(object_path),
        "rebuiltBinary": str(rebuilt_path),
        "artifactMode": receipt.get("artifactMode"),
        "packageSelfContained": receipt.get("packageSelfContained"),
        "blob": receipt.get("blob"),
        "status": "authoritative" if byte_identical else "failed",
        "byteIdentical": byte_identical,
        "sourceSha256": source_sha,
        "expectedSourceSha256": expected_source_sha,
        "sourceMatchesReceipt": source_matches_receipt,
        "packageManifest": package_manifest,
        "packageLocalVerifier": package_local_verifier,
        "claimStatus": claims.get("status"),
        "proven": claims.get("proven"),
        "notProven": claims.get("notProven"),
        "contentIdentity": content_identity,
        "expectedContentIdentity": expect_content_identity,
        "contentIdentityMatches": content_identity_matches,
        "contentIdentityScope": content.get("identityScope") or claims.get("contentIdentityScope"),
        "sourceIndex": {
            "status": source_index.get("status"),
            "sources": [
                {
                    "path": item.get("path"),
                    "language": item.get("language"),
                    "authority": item.get("authority"),
                    "sourceRole": item.get("sourceRole"),
                    "semanticDecompilation": item.get("semanticDecompilation"),
                    "sha256": item.get("sha256"),
                }
                for item in source_index.get("sources", [])
                if isinstance(item, dict)
            ],
        }
        if source_index
        else None,
        "sourceRoles": {
            "status": source_roles.get("status"),
            "roles": [
                {
                    "path": item.get("path"),
                    "role": item.get("role"),
                    "origin": item.get("origin"),
                    "sourceKind": item.get("sourceKind"),
                    "accuracyClass": item.get("accuracyClass"),
                    "semanticStatus": item.get("semanticStatus"),
                    "semanticDecompilation": item.get("semanticDecompilation"),
                }
                for item in source_roles.get("roles", [])
                if isinstance(item, dict)
            ],
        }
        if source_roles
        else None,
        "cSource": c_source_report,
        "expectedSha256": expected_sha,
        "rebuiltSha256": actual_sha,
        "expectedSize": receipt.get("originalSize"),
        "rebuiltSize": rebuilt_size,
        "compileReturnCode": compile_proc.returncode,
        "compileStdout": compile_proc.stdout[-4000:],
        "compileStderr": compile_proc.stderr[-4000:],
        "objcopyReturnCode": objcopy_proc.returncode,
        "objcopyStdout": objcopy_proc.stdout[-4000:],
        "objcopyStderr": objcopy_proc.stderr[-4000:],
        "sourceType": receipt.get("sourceType"),
        "sourceAuthority": receipt.get("sourceAuthority"),
        "semanticDecompilation": receipt.get("semanticDecompilation"),
        "claimBoundary": receipt.get("claimBoundary"),
    }
    (package_dir / "one-shot-source-verify.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--expect-content-identity", help="Fail unless the package contentIdentity matches this value.")
    parser.add_argument("--out", type=Path, help="Write the verification JSON report to this path.")
    parser.add_argument("--markdown", action="store_true", help="Print a concise Markdown report instead of JSON.")
    args = parser.parse_args()

    report = verify_package(args.package, args.timeout, expect_content_identity=args.expect_content_identity)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.markdown:
        print("# One-Shot Source Package Verification")
        print()
        print(f"Status: `{report['status']}`")
        print(f"Package: `{report['package']}`")
        print(f"Content identity: `{report.get('contentIdentity')}`")
        if report.get("expectedContentIdentity"):
            print(f"Content identity matches pin: `{str(report.get('contentIdentityMatches')).lower()}`")
        print(f"Claim status: `{report.get('claimStatus')}`")
        local = report.get("packageLocalVerifier") if isinstance(report.get("packageLocalVerifier"), dict) else {}
        print(f"Package-local verifier: `{local.get('status')}`")
        print()
        print("## Sources")
        source_index = report.get("sourceIndex") if isinstance(report.get("sourceIndex"), dict) else {}
        for item in source_index.get("sources", []) if isinstance(source_index.get("sources"), list) else []:
            print(
                f"- `{item.get('path')}`: `{item.get('language')}`, `{item.get('authority')}`, "
                f"role=`{item.get('sourceRole')}`, semanticDecompilation=`{str(item.get('semanticDecompilation')).lower()}`"
            )
        print()
        print("## Proven")
        proven = report.get("proven") if isinstance(report.get("proven"), dict) else {}
        for key, value in sorted(proven.items()):
            print(f"- `{key}`: `{str(value).lower()}`")
        print()
        print("## Not proven")
        not_proven = report.get("notProven") if isinstance(report.get("notProven"), list) else []
        for item in not_proven:
            print(f"- {item}")
        print()
        print(f"Verifier output: `{str(local.get('stdout') or '').strip()}`")
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "authoritative" else 1


if __name__ == "__main__":
    raise SystemExit(main())
