#!/usr/bin/env python3
"""Create a COFF target object from a swkotor function inventory row."""

from __future__ import annotations

import argparse
import os
import json
import re
import subprocess
from pathlib import Path


ROOT = Path.cwd()


def run(args: list[str], *, timeout: int = 60, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=False, timeout=timeout, env=env)


def load_record(inventory: Path, key: str) -> dict:
    needle = key.lower()
    with inventory.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("name", "")).lower() == needle:
                return row
            if str(row.get("entry", "")).lower().lstrip("0") == needle.lstrip("0"):
                return row
            if f"0x{str(row.get('entry', '')).lower()}".lstrip("0x") == needle.lstrip("0x"):
                return row
    raise SystemExit(f"function not found in inventory: {key}")


def c_symbol(name: str) -> str:
    symbol = re.sub(r"[^A-Za-z0-9_@]", "_", name)
    if not symbol or symbol[0].isdigit():
        symbol = f"FUN_{symbol}"
    return f"_{symbol}"


def byte_rows(data: bytes) -> str:
    rows = []
    for offset in range(0, len(data), 16):
        rows.append("    .byte " + ", ".join(f"0x{byte:02x}" for byte in data[offset : offset + 16]))
    return "\n".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=ROOT / "target/swkotor-unpack/facts/function-inventory.jsonl")
    parser.add_argument("--function", required=True, help="function name or entry address")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--symbol", help="COFF symbol name to emit, e.g. _FUN_00401590@16")
    parser.add_argument("--cc", default="clang")
    args = parser.parse_args()

    env = os.environ.copy()
    env["CCACHE_DISABLE"] = "1"
    env.setdefault("CCACHE_DIR", str(ROOT / "target/ccache"))
    env.setdefault("CCACHE_TEMPDIR", str(ROOT / "target/ccache-tmp"))
    Path(env["CCACHE_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(env["CCACHE_TEMPDIR"]).mkdir(parents=True, exist_ok=True)

    record = load_record(args.inventory, args.function)
    data = bytes.fromhex(record["bytes"])
    args.out_dir.mkdir(parents=True, exist_ok=True)

    symbol = args.symbol or c_symbol(str(record["name"]))
    asm_path = args.out_dir / "target.S"
    obj_path = args.out_dir / "target.obj"
    bin_path = args.out_dir / "target.text.bin"
    meta_path = args.out_dir / "target-slice.json"

    asm_path.write_text(
        "\n".join(
            [
                "    .text",
                f"    .globl {symbol}",
                f"{symbol}:",
                byte_rows(data),
                "",
            ]
        ),
        encoding="utf-8",
    )

    proc = run([args.cc, "-target", "i686-pc-windows-msvc", "-c", str(asm_path), "-o", str(obj_path)], env=env)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr or proc.stdout or "target object assembly failed")

    proc = run(["objcopy", "-I", "coff-i386", "-O", "binary", "-j", ".text", str(obj_path), str(bin_path)], env=env)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr or proc.stdout or "target .text extraction failed")
    if bin_path.read_bytes() != data:
        raise SystemExit("assembled target .text does not match inventory bytes")

    meta = {
        "schema": "mizuchi.swkotor-inventory-slice.v1",
        "inventory": str(args.inventory),
        "name": record.get("name"),
        "entry": record.get("entry"),
        "entryOffset": record.get("entryOffset"),
        "section": record.get("section"),
        "bodyBytes": record.get("bodyBytes"),
        "instructionCount": record.get("instructionCount"),
        "symbol": symbol,
        "targetAsm": str(asm_path),
        "targetObject": str(obj_path),
        "targetText": str(bin_path),
    }
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(meta, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
