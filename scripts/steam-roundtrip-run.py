#!/usr/bin/env python3
"""Run available roundtrip matchers across Steam app workspaces."""

from __future__ import annotations

import argparse
import datetime as _datetime
import importlib.util
import json
import os
import subprocess
import hashlib
import shutil
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_TOOL = ROOT / "scripts" / "steam-roundtrip-inventory.py"
AUTO_TRIVIAL_TOOL = ROOT / "scripts" / "elf-auto-trivial.py"
PE_AUTO_TRIVIAL_TOOL = ROOT / "scripts" / "pe-auto-trivial.py"
BINARY_SOURCE_TOOL = ROOT / "scripts" / "binary-source-roundtrip.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inventory_mod = load_module("steam_roundtrip_inventory", INVENTORY_TOOL)


def compiler_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("CCACHE_DISABLE", "1")
    env.setdefault("CCACHE_DIR", str(ROOT / "target" / ".ccache"))
    return env


def run(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=False, timeout=timeout, env=compiler_env())


def run_cwd(args: list[str], timeout: int, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False, timeout=timeout, env=compiler_env())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
            name_offset = int(values[0])
            section_offset = int(values[4])
            section_size = int(values[5])
            headers.append((name_offset, section_offset, section_size))

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


def asm_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def primary_target(app: dict[str, object]) -> dict[str, object] | None:
    evidence = app.get("roundtrip_evidence")
    if not isinstance(evidence, dict):
        return None
    target = evidence.get("primaryTarget")
    return target if isinstance(target, dict) else None


def is_supported_symbolic_elf(target: dict[str, object] | None) -> tuple[bool, str]:
    if not target:
        return False, "no primary native target"
    if target.get("class") != "elf":
        return False, f"unsupported binary class: {target.get('class')}"
    file_text = str(target.get("file", ""))
    supported_arch = ("ELF 64-bit" in file_text and "x86-64" in file_text) or (
        "ELF 32-bit" in file_text and ("Intel 80386" in file_text or "Intel i386" in file_text)
    )
    if not supported_arch:
        return False, f"unsupported ELF architecture: {file_text}"
    if "not stripped" not in file_text:
        return False, "ELF target is stripped or symbol status unknown"
    return True, "supported ELF with symbols"


def is_supported_pe_export_binary(target: dict[str, object] | None) -> tuple[bool, str]:
    if not target:
        return False, "no target"
    if target.get("class") != "pe":
        return False, f"unsupported binary class: {target.get('class')}"
    rel = str(target.get("path", "")).lower()
    if not rel.endswith(".dll"):
        return False, "PE export matcher currently scans DLL exports only"
    file_text = str(target.get("file", ""))
    supported_arch = ("PE32 executable" in file_text and ("Intel 80386" in file_text or "Intel i386" in file_text)) or (
        "PE32+ executable" in file_text and "x86-64" in file_text
    )
    if not supported_arch:
        return False, f"unsupported PE architecture: {file_text}"
    return True, "supported PE DLL exports"


def app_workspace(root: Path, app: dict[str, object]) -> Path:
    appid = str(app.get("appid") or "unknown")
    name = str(app.get("name") or app.get("installdir") or "app")
    return root / f"{appid}-{inventory_mod.slugify(name)}"


def read_existing_manifest(app_dir: Path) -> dict[str, object] | None:
    path = app_dir / "source-roundtrip-manifest.json"
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def resolve_manifest_path(value: object, manifest_dir: Path) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return manifest_dir / path


def existing_full_app_artifacts_current(manifest: dict[str, object], manifest_dir: Path) -> bool:
    roundtrips = manifest.get("fullBinaryRoundtrips")
    if not isinstance(roundtrips, list) or not roundtrips:
        return False
    expected = int(manifest.get("appFileRoundtripExpected") or 0)
    total = int(manifest.get("appFileRoundtripTotal") or 0)
    if len(roundtrips) != expected or len(roundtrips) != total:
        return False

    for item in roundtrips:
        if not isinstance(item, dict):
            return False
        if item.get("kind") != "whole-binary-byte-source" or item.get("byteIdentical") is not True:
            return False
        source = resolve_manifest_path(item.get("source"), manifest_dir)
        if source is None or not source.is_file():
            return False
        blob = resolve_manifest_path(item.get("blob"), manifest_dir)
        if item.get("blob") is not None and (blob is None or not blob.is_file()):
            return False
        binary = resolve_manifest_path(item.get("binary"), manifest_dir)
        if binary is None or not binary.is_file():
            return False
        expected_sha = str(item.get("originalSha256") or "")
        if not expected_sha or sha256_file(binary) != expected_sha:
            return False
    return True


def existing_full_app_match(app_dir: Path, artifact_mode: str) -> dict[str, object] | None:
    manifest = read_existing_manifest(app_dir)
    if not manifest:
        return None
    if manifest.get("fullAppByteIdentical") is not True:
        return None
    if manifest.get("fullBinarySourceMode") != "all-files":
        return None
    if manifest.get("fullBinaryArtifactMode") != artifact_mode:
        return None
    total = int(manifest.get("appFileRoundtripTotal") or 0)
    matched = int(manifest.get("appFileRoundtripMatched") or 0)
    expected = int(manifest.get("appFileRoundtripExpected") or 0)
    skipped = manifest.get("appFileRoundtripSkipped")
    if total <= 0 or matched != total or expected != total or (isinstance(skipped, list) and skipped):
        return None
    if not existing_full_app_artifacts_current(manifest, app_dir):
        return None
    return manifest


def run_auto_trivial(
    binary: Path,
    out_dir: Path,
    max_size: int,
    timeout: int,
) -> tuple[dict[str, object] | None, str | None]:
    try:
        proc = run(
            [
                str(AUTO_TRIVIAL_TOOL),
                "--binary",
                str(binary),
                "--out",
                str(out_dir),
                "--max-size",
                str(max_size),
            ],
            timeout,
        )
    except subprocess.TimeoutExpired:
        return None, f"ELF auto matcher timed out after {timeout}s"
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout).strip()
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"invalid auto matcher JSON: {exc}"


def run_pe_auto_trivial(
    binary: Path,
    out_dir: Path,
    max_size: int,
    limit: int = 0,
    pe_rebuild_mode: str = "always",
    timeout: int = 180,
) -> tuple[dict[str, object] | None, str | None]:
    command = [
        str(PE_AUTO_TRIVIAL_TOOL),
        "--binary",
        str(binary),
        "--out",
        str(out_dir),
        "--max-size",
        str(max_size),
        "--pe-rebuild-mode",
        pe_rebuild_mode,
    ]
    if limit:
        command.extend(["--limit", str(limit)])
    try:
        proc = run(command, timeout)
    except subprocess.TimeoutExpired:
        return None, f"PE auto matcher timed out after {timeout}s"
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout).strip()
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"invalid PE auto matcher JSON: {exc}"


def run_binary_source_roundtrip(
    binary: Path,
    out_dir: Path,
    timeout: int,
    artifact_mode: str,
) -> tuple[dict[str, object] | None, str | None]:
    try:
        proc = run(
            [
                str(BINARY_SOURCE_TOOL),
                "--binary",
                str(binary),
                "--out",
                str(out_dir),
                "--timeout",
                str(timeout),
                "--artifact-mode",
                artifact_mode,
            ],
            timeout,
        )
    except subprocess.TimeoutExpired:
        return None, f"binary source roundtrip timed out after {timeout}s"
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout).strip()
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"invalid binary source roundtrip JSON: {exc}"


def iter_app_files(app_path: Path) -> list[Path]:
    files: list[Path] = []
    for path in app_path.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                files.append(path)
        except OSError:
            continue
    return sorted(files, key=lambda p: p.relative_to(app_path).as_posix().lower())


def full_binary_out_dir(app_dir: Path, app_path: Path, binary: Path) -> Path:
    rel = binary.relative_to(app_path).as_posix()
    digest = hashlib.sha256(rel.encode("utf-8")).hexdigest()[:12]
    return app_dir / "full-binary-source" / f"{inventory_mod.slugify(rel)}-{digest}"


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


def app_file_matched(reports: list[dict[str, object]], relative_path: str) -> bool:
    items = [item for item in reports if item.get("relativePath") == relative_path]
    return bool(items) and matched_app_file_count(items) == 1


def run_app_batch_source_roundtrip(
    app_path: Path,
    binaries: list[Path],
    out_dir: Path,
    timeout: int,
    artifact_mode: str,
    batch_size: int = 200,
    batch_max_bytes: int = 0,
) -> tuple[list[dict[str, object]], str | None]:
    out_dir.mkdir(parents=True, exist_ok=True)
    dump_dir = out_dir / "rebuilt-sections"
    dump_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, object]] = []
    batch_size = max(1, batch_size)
    batch_max_bytes = max(0, batch_max_bytes)
    batch_error: str | None = None
    chunk_limit = batch_max_bytes if batch_max_bytes > 0 else 1024 * 1024 * 1024
    units: list[dict[str, object]] = []
    for file_index, binary in enumerate(binaries):
        binary_size = binary.stat().st_size
        chunk_count = max(1, (binary_size + chunk_limit - 1) // chunk_limit)
        for chunk_index in range(chunk_count):
            chunk_offset = chunk_index * chunk_limit
            chunk_size = min(chunk_limit, binary_size - chunk_offset)
            units.append(
                {
                    "fileIndex": file_index,
                    "binary": binary,
                    "binarySize": binary_size,
                    "chunkIndex": chunk_index,
                    "chunkCount": chunk_count,
                    "chunkOffset": chunk_offset,
                    "chunkSize": chunk_size,
                }
            )

    batches: list[tuple[int, list[dict[str, object]], int]] = []
    batch: list[dict[str, object]] = []
    batch_start = 0
    batch_bytes = 0
    for index, unit in enumerate(units):
        chunk_size = int(unit["chunkSize"])
        would_exceed_count = len(batch) >= batch_size
        would_exceed_bytes = batch_max_bytes > 0 and batch and batch_bytes + chunk_size > batch_max_bytes
        if would_exceed_count or would_exceed_bytes:
            batches.append((batch_start, batch, batch_bytes))
            batch = []
            batch_start = index
            batch_bytes = 0
        batch.append(unit)
        batch_bytes += chunk_size
    if batch:
        batches.append((batch_start, batch, batch_bytes))

    file_sha_cache: dict[Path, str] = {}
    for batch_index, (start, batch, batch_bytes) in enumerate(batches):
        source_path = out_dir / f"app-files-{batch_index:06d}.S"
        object_path = out_dir / f"app-files-{batch_index:06d}.o"
        lines = [
            "/* Generated by steam-roundtrip-run.py. */",
            "/* Batched byte-source fallback: preserves exact app file bytes, not semantic decompilation. */",
            f"/* Artifact mode: {artifact_mode}. */",
            f"/* Batch {batch_index}: files {start}..{start + len(batch) - 1}. */",
            f"/* Batch bytes: {batch_bytes}. */",
        ]
        batch_entries: list[dict[str, object]] = []
        for unit in batch:
            binary = unit["binary"]
            if not isinstance(binary, Path):
                continue
            index = int(unit["fileIndex"])
            chunk_index = int(unit["chunkIndex"])
            chunk_count = int(unit["chunkCount"])
            chunk_offset = int(unit["chunkOffset"])
            chunk_size = int(unit["chunkSize"])
            section = (
                f".mizuchi_file_{index:06d}"
                if chunk_count == 1
                else f".mizuchi_file_{index:06d}_chunk_{chunk_index:06d}"
            )
            rel = binary.relative_to(app_path).as_posix()
            blob_ref = str(binary.resolve())
            blob_path: Path | None = None
            if artifact_mode == "full":
                blob_path = out_dir / "blobs" / f"{index:06d}.bin"
                blob_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(binary, blob_path)
                blob_ref = str(blob_path.relative_to(out_dir))
            file_sha = None
            if chunk_count == 1:
                file_sha = file_sha_cache.get(binary)
                if file_sha is None:
                    file_sha = sha256_file(binary)
                    file_sha_cache[binary] = file_sha
            original_sha = sha256_file_region(binary, chunk_offset, chunk_size)
            incbin_args = f"{asm_string(blob_ref)}, {chunk_offset}, {chunk_size}"
            lines.extend(
                [
                    f'.section {asm_string(section)},"a"',
                    f".global mizuchi_file_{index:06d}_chunk_{chunk_index:06d}_start",
                    f".global mizuchi_file_{index:06d}_chunk_{chunk_index:06d}_end",
                    f"mizuchi_file_{index:06d}_chunk_{chunk_index:06d}_start:",
                    f"  .incbin {incbin_args}",
                    f"mizuchi_file_{index:06d}_chunk_{chunk_index:06d}_end:",
                ]
            )
            batch_entries.append(
                {
                    "schema": "mizuchi.binary-source-roundtrip.v1",
                    "binary": str(binary),
                    "relativePath": rel,
                    "source": str(source_path),
                    "blob": str(blob_path) if blob_path is not None else None,
                    "sectionName": section,
                    "batchIndex": batch_index,
                    "batchBytes": batch_bytes,
                    "chunkIndex": chunk_index,
                    "chunkCount": chunk_count,
                    "chunkOffset": chunk_offset,
                    "chunkSize": chunk_size,
                    "fileSha256": file_sha,
                    "artifactMode": artifact_mode,
                    "objectRetained": artifact_mode == "full",
                    "rebuiltBinaryRetained": False,
                    "originalSha256": original_sha,
                    "originalSize": int(unit["binarySize"]),
                    "strategy": "byte-source-incbin-batch-sharded",
                    "sourceType": "byte-source",
                    "sourceAuthority": "original-bytes",
                    "semanticDecompilation": False,
                    "scopeNote": "Whole-file bytes are reproduced from generated assembler source shards; oversized files may be split across multiple byte-source sections and counted once when every chunk matches.",
                }
            )
        source_path.write_text("\n".join(lines) + "\n")

        try:
            compile_proc = run_cwd(
                ["as", "--64", "-o", object_path.name, source_path.name],
                timeout,
                out_dir,
            )
        except subprocess.TimeoutExpired:
            batch_error = "batched assembler compile timed out"
            for entry in batch_entries:
                entry.update({"status": "failed", "byteIdentical": False, "error": f"batched assembler compile timed out after {timeout}s"})
            entries.extend(batch_entries)
            continue

        if compile_proc.returncode != 0:
            batch_error = "batched assembler compile failed"
            for entry in batch_entries:
                entry.update(
                    {
                        "status": "failed",
                        "byteIdentical": False,
                        "object": str(object_path) if artifact_mode == "full" else None,
                        "compileReturnCode": compile_proc.returncode,
                        "compileStdout": compile_proc.stdout[-4000:],
                        "compileStderr": compile_proc.stderr[-4000:],
                    }
                )
            entries.extend(batch_entries)
            continue

        try:
            section_ranges = elf_section_ranges(object_path)
        except ValueError as exc:
            section_ranges = {}
            section_error = str(exc)
        else:
            section_error = ""

        for entry in batch_entries:
            section = str(entry["sectionName"])
            section_range = section_ranges.get(section)
            if section_range is None:
                actual = None
                rebuilt_size = 0
                byte_identical = False
            else:
                offset, rebuilt_size = section_range
                actual = sha256_file_region(object_path, offset, rebuilt_size)
                byte_identical = actual == entry["originalSha256"]
            entry.update(
                {
                    "status": "matched" if byte_identical else "failed",
                    "byteIdentical": byte_identical,
                    "object": str(object_path) if artifact_mode == "full" else None,
                    "rebuiltBinary": None,
                    "rebuiltSha256": actual,
                    "rebuiltSize": rebuilt_size,
                    "compileReturnCode": compile_proc.returncode,
                    "compileStdout": compile_proc.stdout[-4000:],
                    "compileStderr": compile_proc.stderr[-4000:],
                    "sectionExtractReturnCode": 0 if section_range is not None else 1,
                    "sectionExtractStderr": section_error,
                }
            )
        entries.extend(batch_entries)
        if artifact_mode == "lean":
            try:
                object_path.unlink()
            except FileNotFoundError:
                pass

    if artifact_mode == "lean":
        try:
            dump_dir.rmdir()
        except OSError:
            pass
    (out_dir / "binary-source-roundtrip.json").write_text(json.dumps({"files": entries}, indent=2, sort_keys=True) + "\n")
    return entries, batch_error


def select_full_binary_targets(
    app_path: Path,
    target: dict[str, object] | None,
    mode: str,
    max_files: int,
    max_bytes: int,
) -> tuple[list[Path], list[dict[str, object]], int, int]:
    if mode == "never":
        return [], [], 0, 0

    if mode == "primary":
        if not target:
            return [], [], 0, 0
        path = app_path / str(target["path"])
        return ([path] if path.is_file() else []), [], 1, path.stat().st_size if path.is_file() else 0

    files = iter_app_files(app_path)
    total_files = len(files)
    total_bytes = 0
    selected: list[Path] = []
    skipped: list[dict[str, object]] = []
    selected_bytes = 0
    for path in files:
        try:
            size = path.stat().st_size
        except OSError as exc:
            skipped.append({"path": path.relative_to(app_path).as_posix(), "reason": str(exc)})
            continue
        total_bytes += size
        if max_files and len(selected) >= max_files:
            skipped.append({"path": path.relative_to(app_path).as_posix(), "size": size, "reason": "max files reached"})
            continue
        if max_bytes and selected_bytes + size > max_bytes:
            skipped.append({"path": path.relative_to(app_path).as_posix(), "size": size, "reason": "max bytes reached"})
            continue
        selected.append(path)
        selected_bytes += size
    return selected, skipped, total_files, total_bytes


def pe_export_targets(app: dict[str, object]) -> list[dict[str, object]]:
    executables = app.get("executables")
    if not isinstance(executables, list):
        return []
    targets: list[dict[str, object]] = []
    for item in executables:
        if not isinstance(item, dict):
            continue
        ok, _reason = is_supported_pe_export_binary(item)
        if ok:
            targets.append(item)
    def priority(item: dict[str, object]) -> tuple[int, str]:
        rel = str(item.get("path", "")).lower()
        name = Path(rel).name
        score = 100
        if any(token in name for token in ("steam_api", "bink", "gameplay", "xgame", "jagame", "jk2game")):
            score -= 80
        if any(token in name for token in ("galaxy", "gameassembly", "commonlibs", "mss")):
            score -= 55
        if any(token in rel for token in ("system/", "gamedata/", "bin/win32/")):
            score -= 10
        if any(token in rel for token in ("api-ms-win", "directx/", "support/", "register/", "/lang.dll")):
            score += 80
        return score, rel

    targets.sort(key=priority)
    return targets


def write_app_source_manifest(
    app_dir: Path,
    app: dict[str, object],
    app_report: dict[str, object],
    pe_reports: list[dict[str, object]],
    full_binary_reports: list[dict[str, object]],
    full_binary_expected_files: int,
    full_binary_total_files: int,
    full_binary_total_bytes: int,
    full_binary_skipped: list[dict[str, object]],
    full_binary_mode: str,
    full_binary_artifact_mode: str,
) -> dict[str, object]:
    source_bundles: list[dict[str, object]] = []
    rebuilt_binaries: list[dict[str, object]] = []
    full_binary_roundtrips: list[dict[str, object]] = []

    elf_roundtrip = app_report.get("aggregateElfSourceRoundtrip")
    if isinstance(elf_roundtrip, dict):
        source_bundles.append(
            {
                "kind": "elf-functions",
                "binary": str((app_report.get("primaryTarget") or {}).get("path", "")),
                "source": elf_roundtrip.get("source"),
                "object": elf_roundtrip.get("object"),
                "byteIdentical": elf_roundtrip.get("byteIdentical") is True,
                "matchedSymbols": elf_roundtrip.get("matchedSymbols", 0),
            }
        )

    for report in pe_reports:
        aggregate = report.get("aggregateSourceRoundtrip")
        if not isinstance(aggregate, dict):
            continue
        source_bundles.append(
            {
                "kind": "pe-exports",
                "binary": report.get("binary"),
                "source": aggregate.get("source"),
                "object": aggregate.get("object"),
                "byteIdentical": aggregate.get("byteIdentical") is True,
                "matchedSymbols": aggregate.get("matchedSymbols", 0),
            }
        )
        rebuilt = aggregate.get("rebuiltDllRoundtrip")
        if isinstance(rebuilt, dict):
            aggregate_matched = aggregate.get("matchedSymbols", 0)
            rebuilt_binaries.append(
                {
                    "kind": "pe-export-dll",
                    "binary": report.get("binary"),
                    "rebuiltDll": rebuilt.get("rebuiltDll"),
                    "byteIdenticalExports": rebuilt.get("byteIdenticalExports") is True,
                    "matchedSymbols": rebuilt.get("matchedSymbols", aggregate_matched),
                    "expectedSymbols": rebuilt.get("expectedSymbols", aggregate_matched),
                    "status": rebuilt.get("status"),
                    "scopeNote": rebuilt.get("scopeNote"),
                }
            )

    for report in full_binary_reports:
        full_binary_roundtrips.append(
            {
                "kind": "whole-binary-byte-source",
                "binary": report.get("binary"),
                "relativePath": report.get("relativePath"),
                "source": report.get("source"),
                "blob": report.get("blob"),
                "object": report.get("object"),
                "rebuiltBinary": report.get("rebuiltBinary"),
                "sectionName": report.get("sectionName"),
                "chunkIndex": report.get("chunkIndex"),
                "chunkCount": report.get("chunkCount"),
                "chunkOffset": report.get("chunkOffset"),
                "chunkSize": report.get("chunkSize"),
                "artifactMode": report.get("artifactMode"),
                "objectRetained": report.get("objectRetained"),
                "rebuiltBinaryRetained": report.get("rebuiltBinaryRetained"),
                "byteIdentical": report.get("byteIdentical") is True,
                "originalSha256": report.get("originalSha256"),
                "fileSha256": report.get("fileSha256"),
                "rebuiltSha256": report.get("rebuiltSha256"),
                "originalSize": report.get("originalSize"),
                "strategy": report.get("strategy"),
                "sourceType": report.get("sourceType"),
                "sourceAuthority": report.get("sourceAuthority"),
                "semanticDecompilation": report.get("semanticDecompilation"),
                "scopeNote": report.get("scopeNote"),
            }
        )

    full_binary_matched = matched_app_file_count(full_binary_roundtrips)
    app_files_byte_identical = (
        full_binary_mode == "all-files"
        and full_binary_total_files > 0
        and full_binary_expected_files == full_binary_total_files
        and not full_binary_skipped
        and full_binary_matched == full_binary_total_files
    )
    primary_binary_byte_identical = bool(app_report.get("primaryBinaryByteIdentical"))
    if not primary_binary_byte_identical and full_binary_mode == "primary":
        primary_binary_byte_identical = bool(full_binary_roundtrips) and all(
            item.get("byteIdentical") is True for item in full_binary_roundtrips
        )

    manifest = {
        "schema": "mizuchi.app-source-roundtrip-manifest.v1",
        "app": app.get("name"),
        "appid": app.get("appid"),
        "workspace": str(app_dir),
        "sourceBundles": source_bundles,
        "rebuiltBinaries": rebuilt_binaries,
        "fullBinaryRoundtrips": full_binary_roundtrips,
        "fullBinarySourceMode": full_binary_mode,
        "fullBinaryArtifactMode": full_binary_artifact_mode,
        "fullBinaryRunner": app_report.get("fullBinaryRunner"),
        "appFileRoundtripExpected": full_binary_expected_files,
        "appFileRoundtripTotal": full_binary_total_files,
        "appFileRoundtripBytes": full_binary_total_bytes,
        "appFileRoundtripMatched": full_binary_matched,
        "appFileRoundtripSkipped": full_binary_skipped,
        "appFilesByteIdentical": app_files_byte_identical,
        "matchedElfFunctions": app_report.get("matchedElfFunctions", 0),
        "matchedPeExportFunctions": app_report.get("matchedPeExportFunctions", 0),
        "matchedFunctions": app_report.get("matchedFunctions", 0),
        "primaryBinaryByteIdentical": primary_binary_byte_identical,
        "fullAppByteIdentical": app_files_byte_identical,
        "scopeNote": "Manifest collects semantic source/object slice artifacts plus byte-source whole-file fallback artifacts. fullAppByteIdentical means every regular file in the selected app folder was recompiled from generated byte-source and matched byte-for-byte; semantic decompilation coverage is tracked separately.",
        "sourceSemantics": {
            "semanticSourceBundles": "Function/export bundles are byte-identical source slices for matched code units only.",
            "byteSourceRoundtrips": "Whole-file artifacts are generated byte-source that preserve original bytes; they are authoritative for byte identity but not semantic decompilation.",
        },
    }
    (app_dir / "source-roundtrip-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--steamapps", type=Path, default=inventory_mod.DEFAULT_STEAMAPPS)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--app", help="Substring filter over app id, name, or install dir")
    parser.add_argument("--max-apps", type=int, default=0, help="Maximum apps to process after filtering, 0 for no limit")
    parser.add_argument(
        "--skip-existing-full-app",
        action="store_true",
        help="Skip app workspaces whose existing manifest already proves all-files fullAppByteIdentical for the selected artifact mode.",
    )
    parser.add_argument("--max-size", type=int, default=24)
    parser.add_argument("--max-pe-binaries", type=int, default=0, help="Maximum PE DLLs to scan, 0 for no limit")
    parser.add_argument("--max-pe-binaries-per-app", type=int, default=0, help="Maximum PE DLLs to scan per app, 0 for no limit")
    parser.add_argument("--pe-match-limit", type=int, default=0, help="Maximum PE template attempts per DLL, 0 for no limit")
    parser.add_argument("--matcher-timeout", type=int, default=180, help="Per-binary matcher timeout in seconds")
    parser.add_argument(
        "--full-binary-source-mode",
        choices=["primary", "all-files", "never"],
        default="primary",
        help="Generate byte-source whole-file roundtrip artifacts for primary binaries or every regular app file.",
    )
    parser.add_argument(
        "--full-binary-max-files",
        type=int,
        default=0,
        help="Maximum app files to byte-source roundtrip in all-files mode, 0 for no limit.",
    )
    parser.add_argument(
        "--full-binary-max-bytes",
        type=int,
        default=0,
        help="Maximum cumulative app bytes to byte-source roundtrip in all-files mode, 0 for no limit.",
    )
    parser.add_argument(
        "--full-binary-artifact-mode",
        choices=["full", "lean"],
        default="full",
        help="full retains copied blobs/object/rebuilt outputs; lean verifies every file but keeps compact source/report artifacts only.",
    )
    parser.add_argument(
        "--full-binary-runner",
        choices=["per-file", "app-batch"],
        default="per-file",
        help="per-file compiles one source per file; app-batch compiles sharded app-level sources with one section per selected file.",
    )
    parser.add_argument(
        "--full-binary-batch-size",
        type=int,
        default=200,
        help="Maximum files per generated app-batch assembler source shard.",
    )
    parser.add_argument(
        "--full-binary-batch-max-bytes",
        type=int,
        default=1024 * 1024 * 1024,
        help="Maximum cumulative input bytes per app-batch assembler source shard, 0 for no byte cap. Single files larger than the cap are kept as one shard.",
    )
    parser.add_argument(
        "--pe-rebuild-mode",
        choices=["always", "never"],
        default="always",
        help="Whether PE export matcher should emit and verify rebuilt exports.dll artifacts.",
    )
    parser.add_argument(
        "--semantic-match-mode",
        choices=["auto", "never"],
        default="auto",
        help="auto runs supported ELF/PE semantic slice matchers; never only performs whole-file byte-source roundtrips.",
    )
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    workspaces = args.out / "apps"
    inventory = inventory_mod.build_inventory(args.steamapps, app_filter=args.app)
    inventory_mod.emit_app_workspaces(inventory, workspaces)
    (args.out / "inventory.json").write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n")

    apps: list[dict[str, object]] = []
    total_matches = 0
    total_elf_matches = 0
    total_pe_matches = 0
    full_binary_roundtrips = 0
    primary_binary_byte_identical = 0
    full_app_byte_identical = 0
    app_file_roundtrip_expected = 0
    app_file_roundtrip_matched = 0
    app_file_roundtrip_total = 0
    eligible = 0
    ran = 0
    pe_eligible = 0
    pe_ran = 0
    selected_apps = inventory["apps"][: args.max_apps] if args.max_apps else inventory["apps"]
    skipped_existing_full_apps = 0
    for app in selected_apps:
        target = primary_target(app)
        ok, reason = is_supported_symbolic_elf(target)
        app_dir = app_workspace(workspaces, app)
        if args.skip_existing_full_app:
            existing = existing_full_app_match(app_dir, args.full_binary_artifact_mode)
            if existing:
                skipped_existing_full_apps += 1
                existing_report = {
                    "appid": app.get("appid"),
                    "name": app.get("name"),
                    "workspace": str(app_dir),
                    "status": "skipped-existing-full-app",
                    "reason": "existing source-roundtrip-manifest.json already proves fullAppByteIdentical",
                    "matchedFunctions": existing.get("matchedFunctions", 0),
                    "matchedElfFunctions": existing.get("matchedElfFunctions", 0),
                    "matchedPeExportFunctions": existing.get("matchedPeExportFunctions", 0),
                    "sourceRoundtripManifest": str(app_dir / "source-roundtrip-manifest.json"),
                    "sourceRoundtripBundles": len(existing.get("sourceBundles", [])) if isinstance(existing.get("sourceBundles"), list) else 0,
                    "rebuiltRoundtripBinaries": len(existing.get("rebuiltBinaries", [])) if isinstance(existing.get("rebuiltBinaries"), list) else 0,
                    "fullBinaryRoundtripArtifacts": len(existing.get("fullBinaryRoundtrips", [])) if isinstance(existing.get("fullBinaryRoundtrips"), list) else 0,
                    "fullBinarySourceMode": existing.get("fullBinarySourceMode"),
                    "fullBinaryArtifactMode": existing.get("fullBinaryArtifactMode"),
                    "appFileRoundtripExpected": existing.get("appFileRoundtripExpected", 0),
                    "appFileRoundtripMatched": existing.get("appFileRoundtripMatched", 0),
                    "appFileRoundtripTotal": existing.get("appFileRoundtripTotal", 0),
                    "appFilesByteIdentical": True,
                    "fullAppByteIdentical": True,
                    "primaryBinaryByteIdentical": existing.get("primaryBinaryByteIdentical", False),
                }
                apps.append(existing_report)
                total_matches += int(existing_report["matchedFunctions"] or 0)
                total_elf_matches += int(existing_report["matchedElfFunctions"] or 0)
                total_pe_matches += int(existing_report["matchedPeExportFunctions"] or 0)
                app_file_roundtrip_expected += int(existing_report["appFileRoundtripExpected"] or 0)
                app_file_roundtrip_matched += int(existing_report["appFileRoundtripMatched"] or 0)
                app_file_roundtrip_total += int(existing_report["appFileRoundtripTotal"] or 0)
                full_binary_roundtrips += int(existing_report["fullBinaryRoundtripArtifacts"] or 0)
                full_app_byte_identical += 1
                if existing_report["primaryBinaryByteIdentical"]:
                    primary_binary_byte_identical += 1
                continue
        app_report: dict[str, object] = {
            "appid": app.get("appid"),
            "name": app.get("name"),
            "workspace": str(app_dir),
            "primaryTarget": target,
            "eligible": ok,
            "status": "skipped",
            "reason": reason,
            "matchedFunctions": 0,
            "matchedElfFunctions": 0,
            "matchedPeExportFunctions": 0,
            "peExportBinaries": [],
            "fullBinaryRoundtrips": [],
            "fullBinarySourceMode": args.full_binary_source_mode,
            "fullBinaryArtifactMode": args.full_binary_artifact_mode,
            "fullBinaryRunner": args.full_binary_runner,
        }
        app_full_binary_reports: list[dict[str, object]] = []
        app_path = Path(str(app["path"]))
        selected_full_binaries, skipped_full_binaries, total_app_files, total_app_bytes = select_full_binary_targets(
            app_path,
            target,
            args.full_binary_source_mode,
            args.full_binary_max_files,
            args.full_binary_max_bytes,
        )
        app_file_roundtrip_total += total_app_files
        app_report["appFileRoundtripTotal"] = total_app_files
        app_report["appFileRoundtripBytes"] = total_app_bytes
        app_report["appFileRoundtripExpected"] = len(selected_full_binaries)
        app_report["appFileRoundtripSkipped"] = skipped_full_binaries
        if selected_full_binaries and args.full_binary_runner == "app-batch":
            batch_reports, batch_error = run_app_batch_source_roundtrip(
                app_path,
                selected_full_binaries,
                app_dir / "full-binary-source-batch",
                args.matcher_timeout,
                args.full_binary_artifact_mode,
                args.full_binary_batch_size,
                args.full_binary_batch_max_bytes,
            )
            for full_report in batch_reports:
                full_binary_roundtrips += 1
                app_file_roundtrip_expected += 1
                if batch_error and full_report.get("status") != "matched":
                    full_report["reason"] = batch_error
                app_full_binary_reports.append(full_report)
                if full_report.get("byteIdentical") is True:
                    app_file_roundtrip_matched += 1
        else:
            for binary in selected_full_binaries:
                matcher_out = full_binary_out_dir(app_dir, app_path, binary)
                full_report, error = run_binary_source_roundtrip(
                    binary,
                    matcher_out,
                    args.matcher_timeout,
                    args.full_binary_artifact_mode,
                )
                full_binary_roundtrips += 1
                app_file_roundtrip_expected += 1
                relative_path = binary.relative_to(app_path).as_posix()
                if full_report is None:
                    app_full_binary_reports.append(
                        {
                            "binary": str(binary),
                            "relativePath": relative_path,
                            "status": "failed",
                            "reason": error or "binary source roundtrip failed",
                            "byteIdentical": False,
                        }
                    )
                else:
                    full_report["relativePath"] = relative_path
                    app_full_binary_reports.append(full_report)
                    if full_report.get("byteIdentical") is True:
                        app_file_roundtrip_matched += 1
        if app_full_binary_reports:
            app_report["fullBinaryRoundtrips"] = app_full_binary_reports
            app_report["appFileRoundtripMatched"] = matched_app_file_count(app_full_binary_reports)
            if args.full_binary_source_mode == "primary":
                primary_ok = bool(app_full_binary_reports) and app_full_binary_reports[0].get("byteIdentical") is True
                app_report["primaryBinaryByteIdentical"] = primary_ok
                if primary_ok:
                    primary_binary_byte_identical += 1
            elif args.full_binary_source_mode == "all-files":
                primary_rel = str(target.get("path")) if target else ""
                primary_ok = app_file_matched(app_full_binary_reports, primary_rel)
                app_report["primaryBinaryByteIdentical"] = primary_ok
                if primary_ok:
                    primary_binary_byte_identical += 1
                app_full = (
                    total_app_files > 0
                    and len(selected_full_binaries) == total_app_files
                    and not skipped_full_binaries
                    and int(app_report["appFileRoundtripMatched"]) == total_app_files
                )
                app_report["appFilesByteIdentical"] = app_full
                app_report["fullAppByteIdentical"] = app_full
                if app_full:
                    full_app_byte_identical += 1
        if args.semantic_match_mode == "auto" and ok and target:
            eligible += 1
            binary = Path(str(app["path"])) / str(target["path"])
            matcher_out = app_dir / "functions-auto-trivial"
            match_report, error = run_auto_trivial(binary, matcher_out, args.max_size, args.matcher_timeout)
            ran += 1
            if match_report is None:
                app_report.update({"status": "failed", "reason": error or "auto matcher failed"})
            else:
                count = int(match_report.get("matchedCount", 0))
                total_matches += count
                total_elf_matches += count
                app_report.update(
                    {
                        "status": "matched-functions" if count else "no-matches",
                        "reason": "auto trivial matcher completed",
                        "autoTrivialReport": str(matcher_out / "auto-trivial-report.json"),
                        "matchedFunctions": count,
                        "symbolCount": match_report.get("symbolCount", 0),
                        "aggregateElfSourceRoundtrip": match_report.get("aggregateSourceRoundtrip"),
                    }
                )
                matched_functions = {
                    "schema": "mizuchi.app-function-matches.v1",
                    "app": app.get("name"),
                    "binary": str(binary),
                    "matchedFunctions": match_report.get("matches", []),
                    "aggregateSourceRoundtrip": match_report.get("aggregateSourceRoundtrip"),
                }
                (app_dir / "matched-functions.json").write_text(
                    json.dumps(matched_functions, indent=2, sort_keys=True) + "\n"
                )

        pe_reports: list[dict[str, object]] = []
        pe_match_count = 0
        app_pe_ran = 0
        pe_targets = pe_export_targets(app) if args.semantic_match_mode == "auto" else []
        for pe_target in pe_targets:
            if args.max_pe_binaries and pe_ran >= args.max_pe_binaries:
                break
            if args.max_pe_binaries_per_app and app_pe_ran >= args.max_pe_binaries_per_app:
                break
            pe_eligible += 1
            binary = Path(str(app["path"])) / str(pe_target["path"])
            matcher_out = app_dir / "pe-export-auto-trivial" / inventory_mod.slugify(str(pe_target["path"]))
            pe_report, error = run_pe_auto_trivial(
                binary,
                matcher_out,
                args.max_size,
                args.pe_match_limit,
                args.pe_rebuild_mode,
                args.matcher_timeout,
            )
            pe_ran += 1
            app_pe_ran += 1
            if pe_report is None:
                pe_reports.append(
                    {
                        "binary": str(binary),
                        "status": "failed",
                        "reason": error or "PE auto matcher failed",
                        "matchedFunctions": 0,
                    }
                )
                continue
            count = int(pe_report.get("matchedCount", 0))
            matches = pe_report.get("matches", [])
            if not isinstance(matches, list):
                matches = []
            pe_match_count += count
            total_matches += count
            total_pe_matches += count
            pe_reports.append(
                {
                    "binary": str(binary),
                    "status": "matched-functions" if count else "no-matches",
                    "reason": "PE export auto trivial matcher completed",
                    "autoTrivialReport": str(matcher_out / "auto-trivial-report.json"),
                    "matchedFunctions": count,
                    "matches": matches,
                    "aggregateSourceRoundtrip": pe_report.get("aggregateSourceRoundtrip"),
                    "exportCount": pe_report.get("exportCount", 0),
                    "scopeNote": pe_report.get("scopeNote"),
                }
            )
        if pe_reports:
            current_status = str(app_report["status"])
            if pe_match_count and current_status in {"skipped", "no-matches"}:
                app_report["status"] = "matched-functions"
                app_report["reason"] = "PE export byte-slice matcher completed"
            app_report["matchedPeExportFunctions"] = pe_match_count
            app_report["matchedFunctions"] = int(app_report["matchedFunctions"]) + pe_match_count
            app_report["peExportBinaries"] = pe_reports
            (app_dir / "matched-pe-export-functions.json").write_text(
                json.dumps(
                    {
                        "schema": "mizuchi.app-pe-export-function-matches.v1",
                        "app": app.get("name"),
                        "matchedFunctions": pe_match_count,
                        "binaries": pe_reports,
                        "scopeNote": "PE export byte slices compared against locally compiled object functions; not full PE/COFF relinks.",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
        app_report["matchedElfFunctions"] = int(app_report["matchedFunctions"]) - int(
            app_report["matchedPeExportFunctions"]
        )
        source_manifest = write_app_source_manifest(
            app_dir,
            app,
            app_report,
            pe_reports,
            app_full_binary_reports,
            len(selected_full_binaries),
            total_app_files,
            total_app_bytes,
            skipped_full_binaries,
            args.full_binary_source_mode,
            args.full_binary_artifact_mode,
        )
        app_report["sourceRoundtripManifest"] = str(app_dir / "source-roundtrip-manifest.json")
        app_report["sourceRoundtripBundles"] = len(source_manifest["sourceBundles"])
        app_report["rebuiltRoundtripBinaries"] = len(source_manifest["rebuiltBinaries"])
        app_report["fullBinaryRoundtripArtifacts"] = len(source_manifest["fullBinaryRoundtrips"])
        apps.append(app_report)

    report = {
        "schema": "mizuchi.steam-roundtrip-run.v1",
        "generatedAt": _datetime.datetime.now(_datetime.UTC).isoformat(),
        "steamapps": str(args.steamapps),
        "workspaceRoot": str(workspaces),
        "appCount": len(apps),
        "inventoryAppCount": len(inventory["apps"]),
        "maxApps": args.max_apps,
        "skippedExistingFullApps": skipped_existing_full_apps,
        "eligibleApps": eligible,
        "peExportEligibleBinaries": pe_eligible,
        "matcherRuns": ran,
        "peExportMatcherRuns": pe_ran,
        "matchedFunctions": total_matches,
        "matchedElfFunctions": total_elf_matches,
        "matchedPeExportFunctions": total_pe_matches,
        "peRebuildMode": args.pe_rebuild_mode,
        "semanticMatchMode": args.semantic_match_mode,
        "matcherTimeoutSeconds": args.matcher_timeout,
        "fullBinarySourceMode": args.full_binary_source_mode,
        "fullBinaryArtifactMode": args.full_binary_artifact_mode,
        "fullBinaryRunner": args.full_binary_runner,
        "fullBinaryMaxFiles": args.full_binary_max_files,
        "fullBinaryMaxBytes": args.full_binary_max_bytes,
        "fullBinaryRoundtrips": full_binary_roundtrips,
        "primaryBinaryByteIdentical": primary_binary_byte_identical,
        "appFileRoundtripExpected": app_file_roundtrip_expected,
        "appFileRoundtripMatched": app_file_roundtrip_matched,
        "appFileRoundtripTotal": app_file_roundtrip_total,
        "fullAppByteIdentical": full_app_byte_identical,
        "scopeNote": "matchedFunctions are byte-identical function/export slices. fullAppByteIdentical counts apps where every regular installed file was recompiled from generated byte-source and matched byte-for-byte; semantic decompilation coverage remains separate.",
        "apps": apps,
    }
    (args.out / "roundtrip-run.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
