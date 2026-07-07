#!/usr/bin/env python3
"""Auto-match tiny PE exports with conservative C templates.

It extracts exported function bytes from the PE image with rabin2, compiles tiny
C candidates locally to Windows COFF objects when Clang supports the target, and
compares the resulting function bytes exactly. It is still not a full PE relink.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import re
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ELF_SLICE_TOOL = ROOT / "scripts" / "elf-function-slice.py"
ELF_AUTO_TOOL = ROOT / "scripts" / "elf-auto-trivial.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


elfslice = load_module("elf_function_slice_for_pe", ELF_SLICE_TOOL)
elfauto = load_module("elf_auto_trivial_for_pe", ELF_AUTO_TOOL)


def run(args: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def align(value: int, boundary: int) -> int:
    return (value + boundary - 1) // boundary * boundary


def rabin_json(binary: Path, flag: str) -> dict[str, object]:
    proc = run(["rabin2", flag, "-j", str(binary)], timeout=60)
    if proc.returncode != 0:
        raise SystemExit((proc.stderr or proc.stdout).strip() or f"rabin2 {flag} failed")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid rabin2 JSON for {binary}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"unexpected rabin2 JSON root for {binary}")
    return data


def file_text(binary: Path) -> str:
    proc = run(["file", "-b", str(binary)], timeout=10)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def compiler_arch_flags(binary: Path) -> list[str]:
    text = file_text(binary)
    if "PE32 executable" in text and ("Intel 80386" in text or "Intel i386" in text):
        return ["-m32"]
    return []


def clang_target(binary: Path) -> str | None:
    arch = pe_arch(binary)
    if arch == "i386":
        return "i686-w64-windows-gnu"
    if arch == "x86_64":
        return "x86_64-w64-windows-gnu"
    return None


def pe_arch(binary: Path) -> str:
    text = file_text(binary)
    if "PE32+ executable" in text and "x86-64" in text:
        return "x86_64"
    if "PE32 executable" in text and ("Intel 80386" in text or "Intel i386" in text):
        return "i386"
    return "unknown"


def coff_symbol_name(binary: Path) -> str:
    return "_candidate" if pe_arch(binary) == "i386" else "candidate"


def parse_exports(binary: Path) -> list[dict[str, object]]:
    exports = rabin_json(binary, "-E").get("exports", [])
    if not isinstance(exports, list):
        return []
    out: list[dict[str, object]] = []
    seen: set[tuple[str, int]] = set()
    size = binary.stat().st_size
    for item in exports:
        if not isinstance(item, dict) or item.get("type") != "FUNC":
            continue
        name = item.get("name")
        paddr = item.get("paddr")
        vaddr = item.get("vaddr")
        if not isinstance(name, str) or not name.strip() or not isinstance(paddr, int):
            continue
        if paddr <= 0 or paddr >= size:
            continue
        key = (name, paddr)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "name": name,
                "paddr": paddr,
                "vaddr": vaddr if isinstance(vaddr, int) else None,
                "ordinal": item.get("ordinal"),
            }
        )
    return out


def read_export_prefix(binary: Path, paddr: int, max_size: int) -> bytes:
    with binary.open("rb") as fh:
        fh.seek(paddr)
        return fh.read(max_size)


def find_templates(symbol: str, data: bytes, max_size: int):
    for length in range(1, min(max_size, len(data)) + 1):
        generated = elfauto.templates(symbol, data[:length])
        if generated:
            return length, generated
    return 0, []


def disassemble_export(binary: Path, vaddr: int | None, size: int) -> str:
    if not isinstance(vaddr, int):
        return ""
    proc = run(
        [
            "objdump",
            "-d",
            "--demangle",
            f"--start-address=0x{vaddr:x}",
            f"--stop-address=0x{vaddr + size:x}",
            str(binary),
        ],
        timeout=20,
    )
    return proc.stdout if proc.returncode == 0 else proc.stderr


def parse_objdump_sections(obj: Path) -> dict[int, dict[str, int | str]]:
    proc = run(["objdump", "-h", str(obj)], timeout=20)
    if proc.returncode != 0:
        raise SystemExit((proc.stderr or proc.stdout).strip() or f"objdump -h failed for {obj}")
    sections: dict[int, dict[str, int | str]] = {}
    for line in proc.stdout.splitlines():
        match = re.match(
            r"\s*(\d+)\s+(\S+)\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+"
            r"[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+",
            line,
        )
        if not match:
            continue
        index, name, size, vma, file_off = match.groups()
        objdump_index = int(index)
        sections[objdump_index + 1] = {
            "index": objdump_index,
            "symbolSection": objdump_index + 1,
            "name": name,
            "address": int(vma, 16),
            "offset": int(file_off, 16),
            "size": int(size, 16),
        }
    return sections


def parse_objdump_symbols(obj: Path) -> list[dict[str, int | str]]:
    proc = run(["objdump", "-t", str(obj)], timeout=20)
    if proc.returncode != 0:
        raise SystemExit((proc.stderr or proc.stdout).strip() or f"objdump -t failed for {obj}")
    symbols: list[dict[str, int | str]] = []
    for line in proc.stdout.splitlines():
        match = re.match(
            r"\[\s*\d+\]\(sec\s+(-?\d+)\).*?\)\s+0x([0-9a-fA-F]+)\s+(.+)$",
            line,
        )
        if not match:
            continue
        section, value, name = match.groups()
        symbols.append(
            {
                "name": name.strip(),
                "sectionIndex": int(section),
                "address": int(value, 16),
            }
        )
    return symbols


def extract_objdump_symbol_bytes(
    obj: Path,
    symbol_names: list[str],
    length: int,
) -> tuple[dict[str, int | str], bytes]:
    sections = parse_objdump_sections(obj)
    symbols = parse_objdump_symbols(obj)
    symbol = next((sym for sym in symbols if str(sym["name"]) in symbol_names), None)
    if symbol is None:
        raise SystemExit(f"symbol not found in {obj}: {' or '.join(symbol_names)}")
    section = sections.get(int(symbol["sectionIndex"]))
    if section is None:
        raise SystemExit(f"section {symbol['sectionIndex']} not found in {obj}")
    offset = int(section["offset"]) + int(symbol["address"]) - int(section["address"])
    if offset < int(section["offset"]) or offset + length > int(section["offset"]) + int(section["size"]):
        raise SystemExit(f"symbol slice exceeds section bounds: {symbol['name']}")
    with obj.open("rb") as fh:
        fh.seek(offset)
        data = fh.read(length)
    if len(data) != length:
        raise SystemExit(f"short read for {symbol['name']}: expected {length}, got {len(data)}")
    meta = dict(symbol)
    meta.update({"section": section["name"], "fileOffset": offset, "extractedSize": length})
    return meta, data


def compile_candidate_coff(source: str, out_object: Path, target: str | None) -> subprocess.CompletedProcess[str]:
    if target is None or shutil.which("clang") is None:
        return subprocess.CompletedProcess(["clang"], 1, "", "clang or PE target unavailable")
    src = out_object.with_suffix(".c")
    src.write_text(source)
    return run(
        [
            "clang",
            f"--target={target}",
            "-x",
            "c",
            "-std=c99",
            "-O2",
            "-g0",
            "-fno-asynchronous-unwind-tables",
            "-fno-stack-protector",
            "-fno-ident",
            "-c",
            str(src),
            "-o",
            str(out_object),
        ],
        timeout=30,
    )


def asm_symbol_name(symbol: str) -> str:
    return json.dumps(symbol)


def asm_bytes(data: bytes) -> str:
    return ", ".join(f"0x{value:02x}" for value in data)


def target_bytes_for_match(match: dict[str, object]) -> bytes:
    return (Path(str(match["functionDir"])) / "target.bin").read_bytes()


def build_aggregate_source(matches: list[dict[str, object]]) -> str:
    lines = [
        "/* Generated by pe-auto-trivial.py. */",
        "/* Byte-identical PE export slices with original COFF symbol names. */",
        ".text",
    ]
    for match in matches:
        symbol = str(match["symbol"])
        target_bytes = target_bytes_for_match(match)
        quoted = asm_symbol_name(symbol)
        lines.extend(
            [
                "",
                f".globl {quoted}",
                f".def {quoted}; .scl 2; .type 32; .endef",
                f"{quoted}:",
                f"  .byte {asm_bytes(target_bytes)}",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def compile_aggregate_coff(source_path: Path, out_object: Path, target: str | None) -> subprocess.CompletedProcess[str]:
    if target is None or shutil.which("clang") is None:
        return subprocess.CompletedProcess(["clang"], 1, "", "clang or PE target unavailable")
    return run(
        [
            "clang",
            f"--target={target}",
            "-x",
            "assembler-with-cpp",
            "-c",
            str(source_path),
            "-o",
            str(out_object),
        ],
        timeout=30,
    )


def write_minimal_pe32_export_dll(path: Path, dll_name: str, matches: list[dict[str, object]]) -> dict[str, object]:
    section_alignment = 0x1000
    file_alignment = 0x200
    text_rva = 0x1000
    headers_size = 0x200

    function_rows: list[dict[str, object]] = []
    text = bytearray()
    for index, match in enumerate(matches):
        text_offset = align(len(text), 16)
        if text_offset > len(text):
            text.extend(b"\x90" * (text_offset - len(text)))
        data = target_bytes_for_match(match)
        text.extend(data)
        function_rows.append(
            {
                "symbol": str(match["symbol"]),
                "rva": text_rva + text_offset,
                "ordinalIndex": index,
                "size": len(data),
                "targetSha256": sha256_bytes(data),
            }
        )

    text_virtual_size = len(text)
    edata_rva = align(text_rva + max(1, text_virtual_size), section_alignment)
    text_raw_size = align(max(1, len(text)), file_alignment)
    text.extend(b"\x00" * (text_raw_size - len(text)))

    sorted_rows = sorted(function_rows, key=lambda row: str(row["symbol"]))
    n = len(function_rows)
    export_dir_size = 40
    address_table_off = export_dir_size
    name_ptr_table_off = address_table_off + n * 4
    ordinal_table_off = name_ptr_table_off + n * 4
    cursor = ordinal_table_off + n * 2

    dll_name_bytes = dll_name.encode("ascii", "replace") + b"\x00"
    dll_name_off = cursor
    cursor += len(dll_name_bytes)

    name_offsets: dict[str, int] = {}
    name_blob = bytearray()
    for row in sorted_rows:
        symbol = str(row["symbol"])
        name_offsets[symbol] = cursor + len(name_blob)
        name_blob.extend(symbol.encode("ascii", "replace") + b"\x00")
    cursor += len(name_blob)

    edata = bytearray(cursor)
    struct.pack_into(
        "<IIHHIIIIIII",
        edata,
        0,
        0,
        0,
        0,
        0,
        edata_rva + dll_name_off,
        1,
        n,
        n,
        edata_rva + address_table_off,
        edata_rva + name_ptr_table_off,
        edata_rva + ordinal_table_off,
    )
    for row in function_rows:
        struct.pack_into("<I", edata, address_table_off + int(row["ordinalIndex"]) * 4, int(row["rva"]))
    for index, row in enumerate(sorted_rows):
        symbol = str(row["symbol"])
        struct.pack_into("<I", edata, name_ptr_table_off + index * 4, edata_rva + name_offsets[symbol])
        struct.pack_into("<H", edata, ordinal_table_off + index * 2, int(row["ordinalIndex"]))
    edata[dll_name_off : dll_name_off + len(dll_name_bytes)] = dll_name_bytes
    edata[cursor - len(name_blob) : cursor] = name_blob

    edata_virtual_size = len(edata)
    edata_raw_size = align(max(1, len(edata)), file_alignment)
    edata.extend(b"\x00" * (edata_raw_size - len(edata)))

    size_of_image = align(edata_rva + edata_virtual_size, section_alignment)
    image_base = 0x10000000
    dos = bytearray(0x80)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x80)

    coff = struct.pack(
        "<4sHHIIIHH",
        b"PE\0\0",
        0x14C,
        2,
        0,
        0,
        0,
        224,
        0x2102,
    )
    optional = bytearray()
    optional.extend(
        struct.pack(
            "<HBBIIIIIIIIIHHHHHHIIIIHHIIIIII",
            0x10B,
            14,
            0,
            text_raw_size,
            edata_raw_size,
            0,
            0,
            text_rva,
            edata_rva,
            image_base,
            section_alignment,
            file_alignment,
            5,
            0,
            0,
            0,
            5,
            0,
            0,
            size_of_image,
            headers_size,
            0,
            2,
            0,
            0x100000,
            0x1000,
            0x100000,
            0x1000,
            0,
            16,
        )
    )
    data_dirs = [(edata_rva, edata_virtual_size)] + [(0, 0)] * 15
    for rva, size in data_dirs:
        optional.extend(struct.pack("<II", rva, size))
    if len(optional) != 224:
        raise AssertionError(f"unexpected PE32 optional header size: {len(optional)}")

    text_raw_ptr = headers_size
    edata_raw_ptr = text_raw_ptr + text_raw_size
    section_headers = bytearray()
    section_headers.extend(
        struct.pack(
            "<8sIIIIIIHHI",
            b".text\0\0\0",
            text_virtual_size,
            text_rva,
            text_raw_size,
            text_raw_ptr,
            0,
            0,
            0,
            0,
            0x60000020,
        )
    )
    section_headers.extend(
        struct.pack(
            "<8sIIIIIIHHI",
            b".edata\0\0",
            edata_virtual_size,
            edata_rva,
            edata_raw_size,
            edata_raw_ptr,
            0,
            0,
            0,
            0,
            0x40000040,
        )
    )

    headers = dos + coff + optional + section_headers
    headers.extend(b"\x00" * (headers_size - len(headers)))
    path.write_bytes(headers + text + edata)
    return {
        "path": str(path),
        "format": "pe32-dll",
        "exportedSymbols": n,
        "textBytes": text_virtual_size,
        "edataBytes": edata_virtual_size,
    }


def write_minimal_pe64_export_dll(path: Path, dll_name: str, matches: list[dict[str, object]]) -> dict[str, object]:
    section_alignment = 0x1000
    file_alignment = 0x200
    text_rva = 0x1000
    headers_size = 0x200

    function_rows: list[dict[str, object]] = []
    text = bytearray()
    for index, match in enumerate(matches):
        text_offset = align(len(text), 16)
        if text_offset > len(text):
            text.extend(b"\x90" * (text_offset - len(text)))
        data = target_bytes_for_match(match)
        text.extend(data)
        function_rows.append(
            {
                "symbol": str(match["symbol"]),
                "rva": text_rva + text_offset,
                "ordinalIndex": index,
                "size": len(data),
                "targetSha256": sha256_bytes(data),
            }
        )

    text_virtual_size = len(text)
    edata_rva = align(text_rva + max(1, text_virtual_size), section_alignment)
    text_raw_size = align(max(1, len(text)), file_alignment)
    text.extend(b"\x00" * (text_raw_size - len(text)))

    sorted_rows = sorted(function_rows, key=lambda row: str(row["symbol"]))
    n = len(function_rows)
    export_dir_size = 40
    address_table_off = export_dir_size
    name_ptr_table_off = address_table_off + n * 4
    ordinal_table_off = name_ptr_table_off + n * 4
    cursor = ordinal_table_off + n * 2

    dll_name_bytes = dll_name.encode("ascii", "replace") + b"\x00"
    dll_name_off = cursor
    cursor += len(dll_name_bytes)

    name_offsets: dict[str, int] = {}
    name_blob = bytearray()
    for row in sorted_rows:
        symbol = str(row["symbol"])
        name_offsets[symbol] = cursor + len(name_blob)
        name_blob.extend(symbol.encode("ascii", "replace") + b"\x00")
    cursor += len(name_blob)

    edata = bytearray(cursor)
    struct.pack_into(
        "<IIHHIIIIIII",
        edata,
        0,
        0,
        0,
        0,
        0,
        edata_rva + dll_name_off,
        1,
        n,
        n,
        edata_rva + address_table_off,
        edata_rva + name_ptr_table_off,
        edata_rva + ordinal_table_off,
    )
    for row in function_rows:
        struct.pack_into("<I", edata, address_table_off + int(row["ordinalIndex"]) * 4, int(row["rva"]))
    for index, row in enumerate(sorted_rows):
        symbol = str(row["symbol"])
        struct.pack_into("<I", edata, name_ptr_table_off + index * 4, edata_rva + name_offsets[symbol])
        struct.pack_into("<H", edata, ordinal_table_off + index * 2, int(row["ordinalIndex"]))
    edata[dll_name_off : dll_name_off + len(dll_name_bytes)] = dll_name_bytes
    edata[cursor - len(name_blob) : cursor] = name_blob

    edata_virtual_size = len(edata)
    edata_raw_size = align(max(1, len(edata)), file_alignment)
    edata.extend(b"\x00" * (edata_raw_size - len(edata)))

    size_of_image = align(edata_rva + edata_virtual_size, section_alignment)
    image_base = 0x180000000
    dos = bytearray(0x80)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x80)

    coff = struct.pack(
        "<4sHHIIIHH",
        b"PE\0\0",
        0x8664,
        2,
        0,
        0,
        0,
        240,
        0x2022,
    )
    optional = bytearray()
    optional.extend(
        struct.pack(
            "<HBBIIIIIQIIHHHHHHIIIIHHQQQQII",
            0x20B,
            14,
            0,
            text_raw_size,
            edata_raw_size,
            0,
            0,
            text_rva,
            image_base,
            section_alignment,
            file_alignment,
            6,
            0,
            0,
            0,
            6,
            0,
            0,
            size_of_image,
            headers_size,
            0,
            2,
            0,
            0x100000,
            0x1000,
            0x100000,
            0x1000,
            0,
            16,
        )
    )
    data_dirs = [(edata_rva, edata_virtual_size)] + [(0, 0)] * 15
    for rva, size in data_dirs:
        optional.extend(struct.pack("<II", rva, size))
    if len(optional) != 240:
        raise AssertionError(f"unexpected PE32+ optional header size: {len(optional)}")

    text_raw_ptr = headers_size
    edata_raw_ptr = text_raw_ptr + text_raw_size
    section_headers = bytearray()
    section_headers.extend(
        struct.pack(
            "<8sIIIIIIHHI",
            b".text\0\0\0",
            text_virtual_size,
            text_rva,
            text_raw_size,
            text_raw_ptr,
            0,
            0,
            0,
            0,
            0x60000020,
        )
    )
    section_headers.extend(
        struct.pack(
            "<8sIIIIIIHHI",
            b".edata\0\0",
            edata_virtual_size,
            edata_rva,
            edata_raw_size,
            edata_raw_ptr,
            0,
            0,
            0,
            0,
            0x40000040,
        )
    )

    headers = dos + coff + optional + section_headers
    headers.extend(b"\x00" * (headers_size - len(headers)))
    path.write_bytes(headers + text + edata)
    return {
        "path": str(path),
        "format": "pe32plus-dll",
        "exportedSymbols": n,
        "textBytes": text_virtual_size,
        "edataBytes": edata_virtual_size,
    }


def build_rebuilt_pe_roundtrip(
    binary: Path,
    roundtrip_dir: Path,
    matches: list[dict[str, object]],
    arch: str,
) -> dict[str, object] | None:
    if arch not in {"i386", "x86_64"} or not matches:
        return None
    rebuilt_path = roundtrip_dir / "exports.dll"
    if arch == "x86_64":
        dll_meta = write_minimal_pe64_export_dll(rebuilt_path, "mizuchi_exports.dll", matches)
    else:
        dll_meta = write_minimal_pe32_export_dll(rebuilt_path, "mizuchi_exports.dll", matches)
    rebuilt_exports = {str(export["name"]): export for export in parse_exports(rebuilt_path)}
    verified: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for match in matches:
        symbol = str(match["symbol"])
        export = rebuilt_exports.get(symbol)
        target_bytes = target_bytes_for_match(match)
        if export is None:
            failures.append({"symbol": symbol, "error": "rebuilt DLL did not export symbol"})
            continue
        rebuilt_bytes = read_export_prefix(rebuilt_path, int(export["paddr"]), len(target_bytes))
        byte_identical = rebuilt_bytes == target_bytes
        row = {
            "symbol": symbol,
            "byteIdentical": byte_identical,
            "size": len(target_bytes),
            "rebuiltPaddr": export["paddr"],
            "rebuiltVaddr": export.get("vaddr"),
            "targetSha256": sha256_bytes(target_bytes),
            "rebuiltSha256": sha256_bytes(rebuilt_bytes),
        }
        if byte_identical:
            verified.append(row)
        else:
            failures.append(row)

    report = {
        "schema": "mizuchi.pe-rebuilt-dll-export-roundtrip.v1",
        "binary": str(binary),
        "status": "matched" if len(verified) == len(matches) else "failed",
        "rebuiltDll": str(rebuilt_path),
        "rebuiltDllSha256": sha256_bytes(rebuilt_path.read_bytes()),
        "dll": dll_meta,
        "matchedSymbols": len(verified),
        "expectedSymbols": len(matches),
        "byteIdenticalExports": len(verified) == len(matches),
        "verified": verified,
        "failures": failures,
        "scopeNote": "Rebuilt PE DLL exports matched slices only; whole-file DLL bytes are not expected to match the original.",
    }
    (roundtrip_dir / "rebuilt-dll-verify.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def build_aggregate_roundtrip(
    binary: Path,
    out_root: Path,
    matches: list[dict[str, object]],
    target: str | None,
    rebuild_mode: str,
) -> dict[str, object] | None:
    coff_matches = [match for match in matches if match.get("candidateObjectFormat") == "coff"]
    if not coff_matches:
        return None

    roundtrip_dir = out_root / "source-roundtrip"
    roundtrip_dir.mkdir(parents=True, exist_ok=True)
    source_path = roundtrip_dir / "exports.S"
    object_path = roundtrip_dir / "exports.obj"
    source_path.write_text(build_aggregate_source(coff_matches))

    compile_proc = compile_aggregate_coff(source_path, object_path, target)
    verified: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    if compile_proc.returncode == 0:
        for match in coff_matches:
            symbol = str(match["symbol"])
            target_bytes = (Path(str(match["functionDir"])) / "target.bin").read_bytes()
            try:
                candidate_meta, candidate_bytes = extract_objdump_symbol_bytes(
                    object_path,
                    [symbol],
                    len(target_bytes),
                )
            except SystemExit as exc:
                failures.append({"symbol": symbol, "error": str(exc)})
                continue
            byte_identical = candidate_bytes == target_bytes
            row = {
                "symbol": symbol,
                "byteIdentical": byte_identical,
                "size": len(target_bytes),
                "targetSha256": sha256_bytes(target_bytes),
                "candidateSha256": sha256_bytes(candidate_bytes),
                "candidate": candidate_meta,
            }
            if byte_identical:
                verified.append(row)
            else:
                failures.append(row)

    report = {
        "schema": "mizuchi.pe-aggregate-source-roundtrip.v1",
        "binary": str(binary),
        "status": "matched" if compile_proc.returncode == 0 and len(verified) == len(coff_matches) else "failed",
        "source": str(source_path),
        "object": str(object_path),
        "objectFormat": "coff",
        "coffTarget": target,
        "matchedSymbols": len(verified),
        "expectedSymbols": len(coff_matches),
        "byteIdentical": compile_proc.returncode == 0 and len(verified) == len(coff_matches),
        "compileReturnCode": compile_proc.returncode,
        "compileStdout": compile_proc.stdout[-4000:],
        "compileStderr": compile_proc.stderr[-4000:],
        "verified": verified,
        "failures": failures,
        "scopeNote": "Aggregate COFF object contains matched export slices only; it is not a full DLL relink.",
    }
    if rebuild_mode == "always":
        rebuilt_dll_roundtrip = build_rebuilt_pe_roundtrip(binary, roundtrip_dir, coff_matches, pe_arch(binary))
        if rebuilt_dll_roundtrip is not None:
            report["rebuiltDllRoundtrip"] = rebuilt_dll_roundtrip
    else:
        report["rebuiltDllRoundtrip"] = {
            "status": "skipped",
            "reason": "PE rebuilt DLL verification disabled by --pe-rebuild-mode=never.",
        }
    (roundtrip_dir / "verify.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def verify_candidate(
    binary: Path,
    export: dict[str, object],
    target_bytes: bytes,
    candidate_object: Path,
    report_path: Path,
    candidate_format: str,
) -> dict[str, object] | None:
    try:
        if candidate_format == "coff":
            candidate_meta, candidate_bytes = extract_objdump_symbol_bytes(
                candidate_object,
                ["candidate", "_candidate"],
                len(target_bytes),
            )
        else:
            candidate_meta, candidate_bytes = elfslice.extract_symbol_bytes(
                candidate_object,
                "candidate",
                length=len(target_bytes),
            )
    except SystemExit:
        return None
    matched = target_bytes == candidate_bytes
    report = {
        "schema": "mizuchi.pe-export-slice-verify.v1",
        "status": "matched" if matched else "mismatched",
        "byteIdentical": matched,
        "binary": str(binary),
        "export": dict(export),
        "candidateObject": str(candidate_object),
        "candidateObjectFormat": candidate_format,
        "candidateSymbol": "candidate",
        "target": {
            "name": export["name"],
            "paddr": export["paddr"],
            "vaddr": export.get("vaddr"),
            "extractedSize": len(target_bytes),
        },
        "candidate": candidate_meta,
        "targetSha256": sha256_bytes(target_bytes),
        "candidateSha256": sha256_bytes(candidate_bytes),
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report if matched else None


def auto_match(args: argparse.Namespace) -> int:
    binary = args.binary
    out_root = args.out
    out_root.mkdir(parents=True, exist_ok=True)
    exports = parse_exports(binary)
    arch_flags = compiler_arch_flags(binary)
    target = clang_target(binary)
    matches: list[dict[str, object]] = []
    attempts = 0
    coff_attempts = 0
    coff_matches = 0
    elf_fallback_attempts = 0
    candidates_considered = 0
    skipped = 0

    with tempfile.TemporaryDirectory(prefix="mizuchi-pe-auto-trivial-") as tmp:
        tmp_dir = Path(tmp)
        for export in exports:
            if args.limit and attempts >= args.limit:
                break
            symbol_name = str(export["name"])
            prefix = read_export_prefix(binary, int(export["paddr"]), args.max_size)
            size, generated = find_templates(symbol_name, prefix, args.max_size)
            if not generated:
                skipped += 1
                continue
            target_bytes = prefix[:size]
            candidates_considered += len(generated)
            for index, candidate in enumerate(generated):
                attempts += 1
                obj = tmp_dir / f"candidate_{attempts}.obj"
                candidate_format = "coff"
                coff_attempts += 1
                compile_proc = compile_candidate_coff(candidate.source, obj, target)
                if compile_proc.returncode != 0:
                    candidate_format = "elf"
                    obj = tmp_dir / f"candidate_{attempts}.o"
                    elf_fallback_attempts += 1
                    compile_proc = elfauto.compile_candidate(candidate.source, obj, arch_flags)
                    if compile_proc.returncode != 0:
                        continue
                report = verify_candidate(
                    binary,
                    export,
                    target_bytes,
                    obj,
                    tmp_dir / f"verify_{attempts}.json",
                    candidate_format,
                )
                if report is None:
                    continue
                if candidate_format == "coff":
                    coff_matches += 1

                fn_dir = out_root / elfauto.safe_slug(symbol_name)
                fn_dir.mkdir(parents=True, exist_ok=True)
                (fn_dir / "candidate.c").write_text(candidate.source)
                output_name = "candidate.obj" if candidate_format == "coff" else "candidate.o"
                shutil.copy2(obj, fn_dir / output_name)
                (fn_dir / "verify.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
                (fn_dir / "target.bin").write_bytes(target_bytes)
                (fn_dir / "target.asm").write_text(
                    disassemble_export(binary, export.get("vaddr"), len(target_bytes))
                )
                summary = {
                    "symbol": symbol_name,
                    "pattern": candidate.pattern,
                    "status": "matched",
                    "byteIdentical": True,
                    "targetSha256": report["targetSha256"],
                    "candidateSha256": report["candidateSha256"],
                    "size": len(target_bytes),
                    "functionDir": str(fn_dir),
                    "candidateObject": str(fn_dir / output_name),
                    "candidateObjectFormat": candidate_format,
                    "templateIndex": index,
                    "paddr": export["paddr"],
                    "vaddr": export.get("vaddr"),
                }
                (fn_dir / "match-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
                matches.append(summary)
                break

    aggregate_roundtrip = build_aggregate_roundtrip(binary, out_root, matches, target, args.pe_rebuild_mode)

    report = {
        "schema": "mizuchi.pe-auto-trivial.v1",
        "binary": str(binary),
        "status": "completed",
        "architecture": pe_arch(binary),
        "exportCount": len(exports),
        "coffTarget": target,
        "compilerArchFlags": arch_flags,
        "attempts": attempts,
        "coffAttempts": coff_attempts,
        "coffMatches": coff_matches,
        "elfFallbackAttempts": elf_fallback_attempts,
        "candidateTemplates": candidates_considered,
        "skippedNoTemplate": skipped,
        "matchedCount": len(matches),
        "matches": matches,
        "aggregateSourceRoundtrip": aggregate_roundtrip,
        "peRebuildMode": args.pe_rebuild_mode,
        "scopeNote": "PE export byte slices compared against locally compiled COFF object functions when possible; not a full PE relink.",
    }
    (out_root / "auto-trivial-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-size", type=int, default=24)
    parser.add_argument("--limit", type=int, default=0, help="Maximum template attempts, 0 for no limit")
    parser.add_argument(
        "--pe-rebuild-mode",
        choices=["always", "never"],
        default="always",
        help="Whether to emit and verify rebuilt exports.dll artifacts for matched PE exports.",
    )
    args = parser.parse_args()
    return auto_match(args)


if __name__ == "__main__":
    raise SystemExit(main())
