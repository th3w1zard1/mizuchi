#!/usr/bin/env python3
"""Scaffold and verify ELF function byte-slice roundtrips.

This is intentionally stricter than "looks like the same asm": it extracts the
target bytes from the installed executable by symbol address and size, then
compares those exact bytes with a compiled candidate object's text bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


def run(args: list[str]) -> str:
    return subprocess.check_output(args, text=True, errors="replace")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_sections(elf: Path) -> dict[str, dict[str, int | str]]:
    sections: dict[str, dict[str, int | str]] = {}
    for line in run(["readelf", "-SW", str(elf)]).splitlines():
        match = re.match(
            r"\s*\[\s*(\d+)\]\s+(\S+)\s+\S+\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)",
            line,
        )
        if not match:
            continue
        index, name, address, offset, size = match.groups()
        sections[index] = {
            "index": int(index),
            "name": name,
            "address": int(address, 16),
            "offset": int(offset, 16),
            "size": int(size, 16),
        }
    return sections


def parse_symbols(elf: Path) -> list[dict[str, int | str]]:
    symbols: list[dict[str, int | str]] = []
    for line in run(["readelf", "-sW", str(elf)]).splitlines():
        parts = line.split(None, 7)
        if len(parts) < 8 or not parts[0].endswith(":"):
            continue
        _num, value, size, sym_type, bind, _vis, ndx, name = parts
        if sym_type != "FUNC" or ndx == "UND":
            continue
        try:
            address = int(value, 16)
            byte_size = int(size, 10)
        except ValueError:
            continue
        if byte_size <= 0:
            continue
        symbols.append(
            {
                "name": name,
                "address": address,
                "size": byte_size,
                "bind": bind,
                "sectionIndex": ndx,
            }
        )
    return symbols


def find_symbol(elf: Path, name: str) -> dict[str, int | str]:
    matches = [sym for sym in parse_symbols(elf) if sym["name"] == name]
    if not matches:
        raise SystemExit(f"symbol not found in {elf}: {name}")
    if len(matches) > 1:
        exact = [sym for sym in matches if sym["bind"] != "LOCAL"]
        if len(exact) == 1:
            return exact[0]
    return matches[0]


def section_for_symbol(elf: Path, symbol: dict[str, int | str]) -> dict[str, int | str]:
    sections = parse_sections(elf)
    section = sections.get(str(symbol["sectionIndex"]))
    if section is None:
        raise SystemExit(f"section {symbol['sectionIndex']} not found in {elf}")
    return section


def extract_symbol_bytes(elf: Path, symbol_name: str, length: int | None = None) -> tuple[dict[str, int | str], bytes]:
    symbol = find_symbol(elf, symbol_name)
    section = section_for_symbol(elf, symbol)
    address = int(symbol["address"])
    byte_size = int(symbol["size"] if length is None else length)
    offset = int(section["offset"]) + (address - int(section["address"]))
    if offset < int(section["offset"]) or offset + byte_size > int(section["offset"]) + int(section["size"]):
        raise SystemExit(f"symbol slice exceeds section bounds: {symbol_name}")
    with elf.open("rb") as fh:
        fh.seek(offset)
        data = fh.read(byte_size)
    if len(data) != byte_size:
        raise SystemExit(f"short read for {symbol_name}: expected {byte_size}, got {len(data)}")
    meta = dict(symbol)
    meta.update({"section": section["name"], "fileOffset": offset, "extractedSize": byte_size})
    return meta, data


def disassemble(elf: Path, symbol: dict[str, int | str]) -> str:
    start = int(symbol["address"])
    stop = start + int(symbol["size"])
    return run(
        [
            "objdump",
            "-d",
            "--demangle",
            f"--start-address=0x{start:x}",
            f"--stop-address=0x{stop:x}",
            str(elf),
        ]
    )


def scaffold(args: argparse.Namespace) -> int:
    target_meta, target_bytes = extract_symbol_bytes(args.binary, args.symbol)
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "target.bin").write_bytes(target_bytes)
    (out_dir / "target.asm").write_text(disassemble(args.binary, target_meta))
    manifest = {
        "schema": "mizuchi.elf-function-slice.v1",
        "binary": str(args.binary),
        "symbol": args.symbol,
        "target": target_meta,
        "targetSha256": sha256_bytes(target_bytes),
        "candidateObject": None,
        "candidateSymbol": None,
        "status": "scaffolded",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    readme = [
        f"# {args.symbol}",
        "",
        f"Binary: `{args.binary}`",
        f"Address: `0x{int(target_meta['address']):x}`",
        f"Size: `{target_meta['size']}`",
        f"Target SHA256: `{manifest['targetSha256']}`",
        "",
        "Compile a candidate object and verify with:",
        "",
        "```bash",
        f"./scripts/elf-function-slice.py verify --binary {args.binary} --symbol '{args.symbol}' --candidate-object <candidate.o> --candidate-symbol <candidate_symbol>",
        "```",
        "",
    ]
    (out_dir / "README.md").write_text("\n".join(readme))
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def verify(args: argparse.Namespace) -> int:
    target_meta, target_bytes = extract_symbol_bytes(args.binary, args.symbol)
    candidate_meta, candidate_bytes = extract_symbol_bytes(
        args.candidate_object,
        args.candidate_symbol,
        length=len(target_bytes),
    )
    matched = target_bytes == candidate_bytes
    report = {
        "schema": "mizuchi.elf-function-slice-verify.v1",
        "status": "matched" if matched else "mismatched",
        "byteIdentical": matched,
        "binary": str(args.binary),
        "symbol": args.symbol,
        "candidateObject": str(args.candidate_object),
        "candidateSymbol": args.candidate_symbol,
        "target": target_meta,
        "candidate": candidate_meta,
        "targetSha256": sha256_bytes(target_bytes),
        "candidateSha256": sha256_bytes(candidate_bytes),
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if matched else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    scaffold_parser = subparsers.add_parser("scaffold")
    scaffold_parser.add_argument("--binary", type=Path, required=True)
    scaffold_parser.add_argument("--symbol", required=True)
    scaffold_parser.add_argument("--out", type=Path, required=True)
    scaffold_parser.set_defaults(func=scaffold)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--binary", type=Path, required=True)
    verify_parser.add_argument("--symbol", required=True)
    verify_parser.add_argument("--candidate-object", type=Path, required=True)
    verify_parser.add_argument("--candidate-symbol", required=True)
    verify_parser.add_argument("--out", type=Path)
    verify_parser.set_defaults(func=verify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
