#!/usr/bin/env python3
"""Recompile and verify app-level Steam source roundtrip manifests."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ELF_AUTO_TOOL = ROOT / "scripts" / "elf-auto-trivial.py"
ELF_SLICE_TOOL = ROOT / "scripts" / "elf-function-slice.py"
PE_AUTO_TOOL = ROOT / "scripts" / "pe-auto-trivial.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


elfauto = load_module("steam_manifest_elf_auto", ELF_AUTO_TOOL)
elfslice = load_module("steam_manifest_elf_slice", ELF_SLICE_TOOL)
peauto = load_module("steam_manifest_pe_auto", PE_AUTO_TOOL)


def resolve_path(value: object, base: Path) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    for candidate in (Path.cwd() / path, ROOT / path, base / path):
        if candidate.exists():
            return candidate
    return base / path


def read_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise SystemExit(f"missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"unexpected JSON root in {path}")
    return data


def compiler_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("CCACHE_DISABLE", "1")
    env.setdefault("CCACHE_DIR", str(ROOT / "target" / ".ccache"))
    return env


def run_command(args: list[str], timeout: int, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False, timeout=timeout, env=compiler_env())


def sha256_bytes(data: bytes) -> str:
    return elfslice.sha256_bytes(data)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_file(src: Path, dst: Path) -> None:
    with src.open("rb") as src_fh, dst.open("wb") as dst_fh:
        shutil.copyfileobj(src_fh, dst_fh, length=1024 * 1024)


def sha256_file_region(path: Path, offset: int, size: int) -> str:
    digest = hashlib.sha256()
    remaining = size
    with path.open("rb") as fh:
        fh.seek(offset)
        while remaining > 0:
            chunk = fh.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def matched_app_file_count(reports: list[dict[str, object]]) -> int:
    by_path: dict[str, list[dict[str, object]]] = {}
    for report in reports:
        key = str(report.get("relativePath") or report.get("binary") or "")
        if key:
            by_path.setdefault(key, []).append(report)

    matched = 0
    for items in by_path.values():
        expected_chunks = max(1, max(int(item.get("chunkCount") or 1) for item in items))
        matched_chunks = {
            int(item.get("chunkIndex") or 0)
            for item in items
            if item.get("byteIdentical") is True
        }
        if len(matched_chunks) == expected_chunks:
            matched += 1
    return matched


def elf_section_ranges(path: Path) -> dict[str, tuple[int, int]]:
    with path.open("rb") as fh:
        ident = fh.read(16)
        if len(ident) != 16 or ident[:4] != b"\x7fELF":
            raise ValueError(f"not an ELF object: {path}")
        bits = ident[4]
        endian = "<" if ident[5] == 1 else ">"
        if bits == 2:
            rest = fh.read(struct.calcsize(endian + "HHIQQQIHHHHHH"))
            fields = struct.unpack(endian + "HHIQQQIHHHHHH", rest)
            e_shoff, e_shentsize, e_shnum, e_shstrndx = fields[5], fields[10], fields[11], fields[12]
            sh_fmt = endian + "IIQQQQIIQQ"
        elif bits == 1:
            rest = fh.read(struct.calcsize(endian + "HHIIIIIHHHHHH"))
            fields = struct.unpack(endian + "HHIIIIIHHHHHH", rest)
            e_shoff, e_shentsize, e_shnum, e_shstrndx = fields[5], fields[10], fields[11], fields[12]
            sh_fmt = endian + "IIIIIIIIII"
        else:
            raise ValueError(f"unsupported ELF class {bits}: {path}")
        sh_size = struct.calcsize(sh_fmt)
        headers: list[tuple[int, int, int]] = []
        for index in range(e_shnum):
            fh.seek(e_shoff + index * e_shentsize)
            raw = fh.read(e_shentsize)
            values = struct.unpack(sh_fmt, raw[:sh_size])
            headers.append((int(values[0]), int(values[4]), int(values[5])))
        if e_shstrndx >= len(headers):
            raise ValueError(f"invalid ELF section string table index: {path}")
        _name, str_offset, str_size = headers[e_shstrndx]
        fh.seek(str_offset)
        strtab = fh.read(str_size)

    out: dict[str, tuple[int, int]] = {}
    for name_offset, section_offset, section_size in headers:
        end = strtab.find(b"\x00", name_offset)
        if end < 0:
            continue
        name = strtab[name_offset:end].decode("utf-8", errors="replace")
        if name:
            out[name] = (section_offset, section_size)
    return out


def verify_elf_bundle(
    bundle: dict[str, object],
    manifest_dir: Path,
    out_dir: Path,
    timeout: int,
) -> dict[str, object]:
    source = resolve_path(bundle.get("source"), manifest_dir)
    binary = resolve_path(bundle.get("binary"), manifest_dir)
    if source is None:
        return {"kind": bundle.get("kind"), "status": "failed", "error": "missing source path"}
    verify_path = source.parent / "verify.json"
    verify_report = read_json(verify_path)
    if binary is None or not binary.exists():
        binary = resolve_path(verify_report.get("binary"), manifest_dir)
    if binary is None or not binary.exists():
        return {"kind": bundle.get("kind"), "source": str(source), "status": "failed", "error": "missing binary path"}
    verified = verify_report.get("verified")
    if not isinstance(verified, list):
        return {"kind": bundle.get("kind"), "source": str(source), "status": "failed", "error": "verify.json has no verified list"}

    out_dir.mkdir(parents=True, exist_ok=True)
    object_path = out_dir / f"{source.stem}.recompiled.o"
    arch_flags = elfauto.compiler_arch_flags(binary)
    try:
        compile_proc = run_command(
            [
                "gcc",
                *arch_flags,
                "-x",
                "assembler-with-cpp",
                "-c",
                str(source),
                "-o",
                str(object_path),
            ],
            timeout,
        )
    except subprocess.TimeoutExpired:
        return {"kind": bundle.get("kind"), "source": str(source), "status": "failed", "error": f"compile timed out after {timeout}s"}

    rows: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    if compile_proc.returncode == 0:
        for item in verified:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "")
            size = int(item.get("size") or 0)
            expected = str(item.get("targetSha256") or "")
            if not symbol or size <= 0 or not expected:
                failures.append({"symbol": symbol, "error": "incomplete verified row"})
                continue
            try:
                candidate_meta, candidate_bytes = elfslice.extract_symbol_bytes(object_path, symbol, length=size)
            except SystemExit as exc:
                failures.append({"symbol": symbol, "error": str(exc)})
                continue
            actual = sha256_bytes(candidate_bytes)
            row = {
                "symbol": symbol,
                "byteIdentical": actual == expected,
                "size": size,
                "targetSha256": expected,
                "candidateSha256": actual,
                "candidate": candidate_meta,
            }
            if row["byteIdentical"]:
                rows.append(row)
            else:
                failures.append(row)

    status = "matched" if compile_proc.returncode == 0 and len(rows) == len(verified) else "failed"
    return {
        "kind": bundle.get("kind"),
        "source": str(source),
        "object": str(object_path),
        "binary": str(binary),
        "status": status,
        "byteIdentical": status == "matched",
        "matchedSymbols": len(rows),
        "expectedSymbols": len(verified),
        "compileReturnCode": compile_proc.returncode,
        "compileStdout": compile_proc.stdout[-4000:],
        "compileStderr": compile_proc.stderr[-4000:],
        "verified": rows,
        "failures": failures,
    }


def verify_pe_bundle(
    bundle: dict[str, object],
    manifest_dir: Path,
    out_dir: Path,
    timeout: int,
) -> dict[str, object]:
    source = resolve_path(bundle.get("source"), manifest_dir)
    if source is None:
        return {"kind": bundle.get("kind"), "status": "failed", "error": "missing source path"}
    verify_path = source.parent / "verify.json"
    verify_report = read_json(verify_path)
    verified = verify_report.get("verified")
    if not isinstance(verified, list):
        return {"kind": bundle.get("kind"), "source": str(source), "status": "failed", "error": "verify.json has no verified list"}
    target = verify_report.get("coffTarget")
    if not isinstance(target, str) or not target:
        return {"kind": bundle.get("kind"), "source": str(source), "status": "failed", "error": "verify.json has no coffTarget"}

    out_dir.mkdir(parents=True, exist_ok=True)
    object_path = out_dir / f"{source.stem}.recompiled.obj"
    try:
        compile_proc = run_command(
            [
                "clang",
                f"--target={target}",
                "-x",
                "assembler-with-cpp",
                "-c",
                str(source),
                "-o",
                str(object_path),
            ],
            timeout,
        )
    except subprocess.TimeoutExpired:
        return {"kind": bundle.get("kind"), "source": str(source), "status": "failed", "error": f"compile timed out after {timeout}s"}

    rows: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    if compile_proc.returncode == 0:
        for item in verified:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "")
            size = int(item.get("size") or 0)
            expected = str(item.get("targetSha256") or "")
            if not symbol or size <= 0 or not expected:
                failures.append({"symbol": symbol, "error": "incomplete verified row"})
                continue
            try:
                candidate_meta, candidate_bytes = peauto.extract_objdump_symbol_bytes(object_path, [symbol], size)
            except SystemExit as exc:
                failures.append({"symbol": symbol, "error": str(exc)})
                continue
            actual = sha256_bytes(candidate_bytes)
            row = {
                "symbol": symbol,
                "byteIdentical": actual == expected,
                "size": size,
                "targetSha256": expected,
                "candidateSha256": actual,
                "candidate": candidate_meta,
            }
            if row["byteIdentical"]:
                rows.append(row)
            else:
                failures.append(row)

    status = "matched" if compile_proc.returncode == 0 and len(rows) == len(verified) else "failed"
    return {
        "kind": bundle.get("kind"),
        "source": str(source),
        "object": str(object_path),
        "binary": bundle.get("binary"),
        "status": status,
        "byteIdentical": status == "matched",
        "matchedSymbols": len(rows),
        "expectedSymbols": len(verified),
        "compileReturnCode": compile_proc.returncode,
        "compileStdout": compile_proc.stdout[-4000:],
        "compileStderr": compile_proc.stderr[-4000:],
        "verified": rows,
        "failures": failures,
    }


def verify_full_binary_roundtrip(
    item: dict[str, object],
    manifest_dir: Path,
    out_dir: Path,
    timeout: int,
) -> dict[str, object]:
    source = resolve_path(item.get("source"), manifest_dir)
    blob = resolve_path(item.get("blob"), manifest_dir)
    expected = str(item.get("originalSha256") or "")
    artifact_mode = str(item.get("artifactMode") or "full")
    if source is None:
        return {"kind": item.get("kind"), "status": "failed", "error": "missing source path"}
    if not source.exists():
        return {"kind": item.get("kind"), "source": str(source), "status": "failed", "error": "source is missing"}
    if blob is not None and not blob.exists():
        return {"kind": item.get("kind"), "source": str(source), "blob": str(blob), "status": "failed", "error": "blob is missing"}
    if not expected and blob is not None:
        expected = sha256_file(blob)
    if not expected:
        return {"kind": item.get("kind"), "source": str(source), "status": "failed", "error": "missing expected originalSha256"}

    out_dir.mkdir(parents=True, exist_ok=True)
    local_blob = out_dir / "original.bin"
    local_source = out_dir / "full-binary.S"
    object_path = out_dir / "full-binary.recompiled.o"
    rebuilt_path = out_dir / "rebuilt.bin"
    local_source.write_text(source.read_text())
    if blob is not None:
        copy_file(blob, local_blob)

    try:
        compile_proc = run_command(
            [
                "gcc",
                "-x",
                "assembler-with-cpp",
                "-c",
                local_source.name,
                "-o",
                object_path.name,
            ],
            timeout,
            cwd=out_dir,
        )
    except subprocess.TimeoutExpired:
        return {"kind": item.get("kind"), "source": str(source), "status": "failed", "error": f"compile timed out after {timeout}s"}

    objcopy_proc = subprocess.CompletedProcess(["objcopy"], 1, "", "compile failed")
    if compile_proc.returncode == 0:
        try:
            objcopy_proc = run_command(
                [
                    "objcopy",
                    "-O",
                    "binary",
                    "-j",
                    ".reconkit_image",
                    object_path.name,
                    rebuilt_path.name,
                ],
                timeout,
                cwd=out_dir,
            )
        except subprocess.TimeoutExpired:
            objcopy_proc = subprocess.CompletedProcess(["objcopy"], 124, "", f"objcopy timed out after {timeout}s")

    actual = sha256_file(rebuilt_path) if rebuilt_path.exists() else ""
    byte_identical = compile_proc.returncode == 0 and objcopy_proc.returncode == 0 and actual == expected
    object_retained = artifact_mode != "lean"
    rebuilt_retained = artifact_mode != "lean"
    if artifact_mode == "lean":
        for path in (object_path, rebuilt_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    return {
        "kind": item.get("kind"),
        "source": str(source),
        "blob": str(blob) if blob is not None else None,
        "object": str(object_path) if object_retained else None,
        "rebuiltBinary": str(rebuilt_path) if rebuilt_retained else None,
        "artifactMode": artifact_mode,
        "objectRetained": object_retained,
        "rebuiltBinaryRetained": rebuilt_retained,
        "status": "matched" if byte_identical else "failed",
        "byteIdentical": byte_identical,
        "matchedSymbols": 1 if byte_identical else 0,
        "expectedSymbols": 1,
        "targetSha256": expected,
        "rebuiltSha256": actual,
        "compileReturnCode": compile_proc.returncode,
        "compileStdout": compile_proc.stdout[-4000:],
        "compileStderr": compile_proc.stderr[-4000:],
        "objcopyReturnCode": objcopy_proc.returncode,
        "objcopyStdout": objcopy_proc.stdout[-4000:],
        "objcopyStderr": objcopy_proc.stderr[-4000:],
        "strategy": item.get("strategy"),
    }


def verify_full_binary_batch(
    items: list[dict[str, object]],
    manifest_dir: Path,
    out_dir: Path,
    timeout: int,
) -> list[dict[str, object]]:
    if not items:
        return []
    first = items[0]
    source = resolve_path(first.get("source"), manifest_dir)
    artifact_mode = str(first.get("artifactMode") or "lean")
    if source is None or not source.exists():
        return [
            {
                "kind": item.get("kind"),
                "source": str(source) if source else None,
                "sectionName": item.get("sectionName"),
                "status": "failed",
                "byteIdentical": False,
                "error": "missing batch source path",
            }
            for item in items
        ]

    out_dir.mkdir(parents=True, exist_ok=True)
    local_source = out_dir / "app-files.S"
    object_path = out_dir / "app-files.recompiled.o"
    dump_dir = out_dir / "rebuilt-sections"
    dump_dir.mkdir(parents=True, exist_ok=True)
    local_source.write_text(source.read_text())
    for item in items:
        blob = resolve_path(item.get("blob"), manifest_dir)
        if blob is None:
            continue
        if not blob.exists():
            return [
                {
                    "kind": failed.get("kind"),
                    "source": str(source),
                    "blob": str(blob),
                    "sectionName": failed.get("sectionName"),
                    "status": "failed",
                    "byteIdentical": False,
                    "error": "batch blob is missing",
                }
                for failed in items
            ]
        try:
            relative_blob = blob.relative_to(source.parent)
        except ValueError:
            continue
        local_blob = out_dir / relative_blob
        local_blob.parent.mkdir(parents=True, exist_ok=True)
        copy_file(blob, local_blob)

    try:
        compile_proc = run_command(["as", "--64", "-o", object_path.name, local_source.name], timeout, cwd=out_dir)
    except subprocess.TimeoutExpired:
        return [
            {
                "kind": item.get("kind"),
                "source": str(source),
                "sectionName": item.get("sectionName"),
                "status": "failed",
                "byteIdentical": False,
                "error": f"batch compile timed out after {timeout}s",
            }
            for item in items
        ]

    reports: list[dict[str, object]] = []
    if compile_proc.returncode != 0:
        return [
            {
                "kind": item.get("kind"),
                "source": str(source),
                "sectionName": item.get("sectionName"),
                "status": "failed",
                "byteIdentical": False,
                "compileReturnCode": compile_proc.returncode,
                "compileStdout": compile_proc.stdout[-4000:],
                "compileStderr": compile_proc.stderr[-4000:],
            }
            for item in items
        ]

    try:
        section_ranges = elf_section_ranges(object_path)
    except ValueError as exc:
        section_ranges = {}
        section_error = str(exc)
    else:
        section_error = ""

    for item in items:
        expected = str(item.get("originalSha256") or "")
        section = str(item.get("sectionName") or "")
        section_range = section_ranges.get(section)
        if section_range is None:
            actual = ""
            rebuilt_size = 0
            byte_identical = False
        else:
            offset, rebuilt_size = section_range
            actual = sha256_file_region(object_path, offset, rebuilt_size)
            byte_identical = actual == expected
        reports.append(
            {
                "kind": item.get("kind"),
                "binary": item.get("binary"),
                "relativePath": item.get("relativePath"),
                "source": str(source),
                "blob": item.get("blob"),
                "object": str(object_path) if artifact_mode != "lean" else None,
                "rebuiltBinary": None,
                "sectionName": item.get("sectionName"),
                "chunkIndex": item.get("chunkIndex"),
                "chunkCount": item.get("chunkCount"),
                "chunkOffset": item.get("chunkOffset"),
                "chunkSize": item.get("chunkSize"),
                "artifactMode": artifact_mode,
                "objectRetained": artifact_mode != "lean",
                "rebuiltBinaryRetained": False,
                "status": "matched" if byte_identical else "failed",
                "byteIdentical": byte_identical,
                "matchedSymbols": 1 if byte_identical else 0,
                "expectedSymbols": 1,
                "targetSha256": expected,
                "rebuiltSha256": actual,
                "compileReturnCode": compile_proc.returncode,
                "compileStdout": compile_proc.stdout[-4000:],
                "compileStderr": compile_proc.stderr[-4000:],
                "sectionExtractReturnCode": 0 if section_range is not None else 1,
                "sectionExtractStderr": section_error,
                "strategy": item.get("strategy"),
                "sourceType": item.get("sourceType"),
                "sourceAuthority": item.get("sourceAuthority"),
                "semanticDecompilation": item.get("semanticDecompilation"),
            }
        )

    if artifact_mode == "lean":
        try:
            object_path.unlink()
        except FileNotFoundError:
            pass
        try:
            dump_dir.rmdir()
        except OSError:
            pass
    return reports


def verify_manifest(manifest_path: Path, out_dir: Path, timeout: int) -> dict[str, object]:
    manifest = read_json(manifest_path)
    manifest_dir = manifest_path.parent
    bundles = manifest.get("sourceBundles")
    if not isinstance(bundles, list):
        raise SystemExit(f"manifest has no sourceBundles list: {manifest_path}")

    bundle_reports: list[dict[str, object]] = []
    for index, bundle in enumerate(bundles):
        if not isinstance(bundle, dict):
            continue
        kind = str(bundle.get("kind") or "")
        bundle_out = out_dir / f"bundle-{index:03d}-{kind or 'unknown'}"
        if kind == "elf-functions":
            bundle_reports.append(verify_elf_bundle(bundle, manifest_dir, bundle_out, timeout))
        elif kind == "pe-exports":
            bundle_reports.append(verify_pe_bundle(bundle, manifest_dir, bundle_out, timeout))
        else:
            bundle_reports.append({"kind": kind, "status": "skipped", "reason": "unsupported bundle kind"})

    full_binary_reports: list[dict[str, object]] = []
    full_binary_items = manifest.get("fullBinaryRoundtrips")
    if isinstance(full_binary_items, list):
        batch_groups: dict[str, list[dict[str, object]]] = {}
        for index, item in enumerate(full_binary_items):
            if not isinstance(item, dict):
                continue
            if item.get("sectionName"):
                batch_groups.setdefault(str(item.get("source") or ""), []).append(item)
            else:
                full_binary_reports.append(
                    verify_full_binary_roundtrip(
                        item,
                        manifest_dir,
                        out_dir / f"full-binary-{index:03d}",
                        timeout,
                    )
                )
        for index, group in enumerate(batch_groups.values()):
            full_binary_reports.extend(
                verify_full_binary_batch(group, manifest_dir, out_dir / f"full-binary-batch-{index:03d}", timeout)
            )

    matched = sum(int(report.get("matchedSymbols") or 0) for report in bundle_reports)
    expected = sum(int(report.get("expectedSymbols") or 0) for report in bundle_reports)
    app_file_total = int(manifest.get("appFileRoundtripTotal") or 0)
    app_file_expected = int(manifest.get("appFileRoundtripExpected") or matched_app_file_count(full_binary_reports))
    binary_matched = matched_app_file_count(full_binary_reports)
    binary_expected = app_file_expected
    app_file_skipped = manifest.get("appFileRoundtripSkipped")
    skipped_count = len(app_file_skipped) if isinstance(app_file_skipped, list) else 0
    full_app_byte_identical = (
        app_file_total > 0
        and app_file_expected == app_file_total
        and skipped_count == 0
        and binary_matched == app_file_total
    )
    failures = [report for report in bundle_reports if report.get("status") not in {"matched", "skipped"}]
    failures.extend(report for report in full_binary_reports if report.get("status") != "matched")
    report = {
        "schema": "reconkit.app-source-roundtrip-verify.v1",
        "manifest": str(manifest_path),
        "app": manifest.get("app"),
        "appid": manifest.get("appid"),
        "status": "matched" if not failures and matched == expected else "failed",
        "byteIdentical": not failures and matched == expected,
        "matchedSymbols": matched,
        "expectedSymbols": expected,
        "fullBinaryRoundtrips": binary_expected,
        "fullBinaryByteIdentical": binary_matched,
        "appFileRoundtripExpected": app_file_expected,
        "appFileRoundtripTotal": app_file_total,
        "appFileRoundtripMatched": binary_matched,
        "appFileRoundtripSkipped": skipped_count,
        "fullAppByteIdentical": full_app_byte_identical,
        "bundleCount": len(bundle_reports),
        "bundles": bundle_reports,
        "fullBinaries": full_binary_reports,
        "scopeNote": "Recompiled app manifest source bundles and verified symbol/export slice bytes. fullAppByteIdentical is true only when the manifest covered every regular app file with byte-source artifacts that rebuilt byte-for-byte; semantic decompilation coverage remains separate.",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verify-manifest.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def update_manifest_from_verification(manifest_path: Path, report: dict[str, object]) -> None:
    manifest = read_json(manifest_path)
    full_binaries = manifest.get("fullBinaryRoundtrips")
    verified = report.get("fullBinaries")
    if isinstance(full_binaries, list) and isinstance(verified, list):
        verified_by_key: dict[tuple[str, str], dict[str, object]] = {}
        verified_by_section: dict[str, dict[str, object]] = {}
        for item in verified:
            if not isinstance(item, dict):
                continue
            key = (str(item.get("source") or ""), str(item.get("sectionName") or ""))
            verified_by_key[key] = item
            section = str(item.get("sectionName") or "")
            if section:
                verified_by_section[section] = item
        for item in full_binaries:
            if not isinstance(item, dict):
                continue
            key = (str(item.get("source") or ""), str(item.get("sectionName") or ""))
            verified_item = verified_by_key.get(key) or verified_by_section.get(str(item.get("sectionName") or ""))
            if not verified_item:
                continue
            for field in (
                "status",
                "byteIdentical",
                "rebuiltSha256",
                "rebuiltSize",
                "compileReturnCode",
                "sectionExtractReturnCode",
                "sectionExtractStderr",
            ):
                if field in verified_item:
                    item[field] = verified_item[field]

    matched = int(report.get("appFileRoundtripMatched") or 0)
    total = int(manifest.get("appFileRoundtripTotal") or 0)
    expected = int(manifest.get("appFileRoundtripExpected") or 0)
    skipped = manifest.get("appFileRoundtripSkipped")
    skipped_count = len(skipped) if isinstance(skipped, list) else 0
    full_app = total > 0 and expected == total and skipped_count == 0 and matched == total
    manifest["appFileRoundtripMatched"] = matched
    manifest["appFilesByteIdentical"] = full_app
    manifest["fullAppByteIdentical"] = full_app
    if isinstance(full_binaries, list) and full_binaries:
        manifest["primaryBinaryByteIdentical"] = (
            isinstance(full_binaries[0], dict) and full_binaries[0].get("byteIdentical") is True
        )
    else:
        manifest["primaryBinaryByteIdentical"] = manifest.get("primaryBinaryByteIdentical", False)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument(
        "--update-manifest",
        action="store_true",
        help="Write verified full-binary status fields back to the source-roundtrip manifest.",
    )
    args = parser.parse_args()

    report = verify_manifest(args.manifest, args.out, args.timeout)
    if args.update_manifest and report["byteIdentical"]:
        update_manifest_from_verification(args.manifest, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["byteIdentical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
