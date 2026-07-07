"""Target discovery and binary identity helpers."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EXECUTABLE_SUFFIXES = {".exe", ".dll", ".xbe", ".elf", ".so", ""}


@dataclass(frozen=True)
class TargetIdentity:
    input_path: Path
    binary_path: Path
    sha256: str
    size: int
    format: str
    architecture_hint: str
    stable_id: str

    def to_json(self) -> dict[str, Any]:
        return {
            "inputPath": str(self.input_path),
            "binaryPath": str(self.binary_path),
            "sha256": self.sha256,
            "size": self.size,
            "format": self.format,
            "architectureHint": self.architecture_hint,
            "stableId": self.stable_id,
        }


def resolve_target(input_path: Path, preferred_name: str | None = None) -> Path:
    path = input_path.expanduser().resolve()
    if path.is_file():
        return path
    if not path.is_dir():
        raise FileNotFoundError(f"target path does not exist: {path}")

    if preferred_name:
        exact = sorted(path.rglob(preferred_name))
        if exact:
            return exact[0]

    candidates: list[Path] = []
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        if item.suffix.lower() in EXECUTABLE_SUFFIXES and looks_executable(item):
            candidates.append(item)
    if not candidates:
        raise FileNotFoundError(f"no executable-looking file found under {path}")
    return sorted(candidates, key=lambda item: item.stat().st_size, reverse=True)[0]


def looks_executable(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
        if os.access(path, os.X_OK):
            return True
        with path.open("rb") as fh:
            magic = fh.read(4)
        return magic.startswith((b"MZ", b"\x7fELF")) or magic in {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe"}
    except OSError:
        return False


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identify_binary(input_path: Path, preferred_name: str | None = None) -> TargetIdentity:
    binary = resolve_target(input_path, preferred_name)
    digest = sha256_file(binary)
    size = binary.stat().st_size
    fmt, arch = inspect_magic(binary)
    stable_id = f"{binary.stem}-{digest[:12]}".lower()
    return TargetIdentity(
        input_path=input_path.expanduser().resolve(),
        binary_path=binary,
        sha256=digest,
        size=size,
        format=fmt,
        architecture_hint=arch,
        stable_id=stable_id,
    )


def inspect_magic(path: Path) -> tuple[str, str]:
    data = path.read_bytes()[:0x1000]
    if data.startswith(b"MZ"):
        machine = pe_machine(data)
        return ("pe", machine)
    if data.startswith(b"\x7fELF"):
        cls = {1: "x86", 2: "x86_64"}.get(data[4], "unknown")
        return ("elf", cls)
    if data[:4] in {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe"}:
        return ("macho", macho_machine(data))
    return ("unknown", "unknown")


def pe_machine(data: bytes) -> str:
    if len(data) < 0x40:
        return "unknown"
    pe_offset = int.from_bytes(data[0x3C:0x40], "little", signed=False)
    if pe_offset + 6 > len(data) or data[pe_offset : pe_offset + 4] != b"PE\0\0":
        return "unknown"
    machine = int.from_bytes(data[pe_offset + 4 : pe_offset + 6], "little", signed=False)
    return {
        0x014C: "x86",
        0x8664: "x86_64",
        0x01C0: "arm",
        0xAA64: "arm64",
    }.get(machine, f"machine-0x{machine:04x}")


def macho_machine(data: bytes) -> str:
    if len(data) < 8:
        return "unknown"
    magic = data[:4]
    endian = "little" if magic in {b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"} else "big"
    cputype = int.from_bytes(data[4:8], endian, signed=True)
    cpu_arch_abi64 = 0x01000000
    cpu_type_x86 = 7
    cpu_type_arm = 12
    base = cputype & ~cpu_arch_abi64
    is_64 = bool(cputype & cpu_arch_abi64)
    if base == cpu_type_x86:
        return "x86_64" if is_64 else "x86"
    if base == cpu_type_arm:
        return "arm64" if is_64 else "arm"
    return f"cputype-0x{cputype & 0xffffffff:08x}"
