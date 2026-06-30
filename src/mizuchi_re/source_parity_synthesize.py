#!/usr/bin/env python3
"""Autonomously synthesize and objdiff-gate source candidates for queued functions.

This is a generated-candidate lane, not a hand-written decompilation lane. It
derives bounded C source variants from instruction-byte patterns and records
every compile/objdiff result as evidence. A candidate is accepted only when the
object diff gate reports zero differences.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .package_verify import compile_with_msvc

ROOT = Path.cwd()
DEFAULT_VC_ROOT: Path | None = None
DEFAULT_WINEPREFIX: Path | None = None


@dataclass(frozen=True)
class GeneratedCandidate:
    rule: str
    variant: str
    c_name: str
    symbol: str
    source: str
    callconv: str
    return_type: str
    extra_flags: tuple[str, ...] = field(default_factory=tuple)
    evidence: dict[str, Any] = field(default_factory=dict)


DEFAULT_PROFILES: list[tuple[str, list[str]]] = [
    ("O2_Oy_GSminus", ["/O2", "/Oy", "/GS-"]),
    ("Od_Oyminus_GSminus", ["/Od", "/Oy-", "/GS-"]),
]


def parse_profile_flag_set(value: str) -> tuple[str, list[str]]:
    if "=" in value:
        name, flags = value.split("=", 1)
    else:
        name = "_".join(part.strip("/-").lower() for part in value.replace(",", " ").split() if part) or "custom"
        flags = value
    parsed = [item for item in flags.replace(",", " ").split() if item]
    return name.strip() or "custom", parsed


def normalize_profile_flags(profile_flags: list[str], overrides: list[str]) -> list[str]:
    merged: list[str] = []
    for flag in [*profile_flags, *overrides]:
        if not isinstance(flag, str) or not flag:
            continue
        candidate = str(flag)
        lowered = candidate.upper()
        if lowered.startswith("/O"):
            merged = [entry for entry in merged if not entry.upper().startswith("/O")]
        elif lowered.startswith("/OY"):
            merged = [entry for entry in merged if not entry.upper().startswith("/OY")]
        elif lowered.startswith("/GS"):
            merged = [entry for entry in merged if not entry.upper().startswith("/GS")]
        merged.append(candidate)
    return merged


def extract_row_compiler_profile_hints(row: dict[str, Any]) -> list[str] | None:
    hints = row.get("compilerProfileHints")
    if not isinstance(hints, dict):
        return None
    compiler = str(hints.get("compiler") or "").lower()
    if compiler and compiler != "msvc":
        return None
    args = hints.get("args")
    if not isinstance(args, list):
        return None
    normalized = [item for item in args if isinstance(item, str) and item.strip()]
    return normalized or None


def resolve_profiles(
    row: dict[str, Any],
    cli_profiles: list[tuple[str, list[str]]],
) -> list[tuple[str, list[str]]]:
    if cli_profiles:
        return cli_profiles
    hint_args = extract_row_compiler_profile_hints(row)
    if hint_args:
        return [("row-hint", hint_args)]
    return DEFAULT_PROFILES


def run(args: list[str], *, env: dict[str, str] | None = None, timeout: int = 90) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, cwd=Path.cwd(), env=env, text=True, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        message = f"timed out after {timeout} seconds"
        return subprocess.CompletedProcess(args, 124, stdout, f"{stderr}\n{message}".strip())


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def clean_jsonl(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def parse_bytes(row: dict[str, Any]) -> bytes:
    try:
        return bytes.fromhex(str(row.get("bytes", "")))
    except ValueError:
        return b""


def safe_c_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"FUN_{cleaned}"
    return cleaned


def safe_dir_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.@+-]", "_", value)
    return cleaned[:120] or "candidate"


def cdecl_symbol(c_name: str) -> str:
    return f"_{c_name}"


def fastcall_symbol(c_name: str, arg_bytes: int) -> str:
    return f"@{c_name}@{arg_bytes}"


def self_offset(offset: int) -> str:
    if offset == 0:
        return "(char *)self"
    if offset < 0:
        return f"(char *)self - 0x{-offset:x}"
    return f"(char *)self + 0x{offset:x}"


def u32(value: bytes) -> int:
    return int.from_bytes(value, byteorder="little", signed=False)


def header(rule: str, row: dict[str, Any]) -> str:
    return "\n".join(
        [
            "/*",
            " * Generated by source-parity-synthesize.py.",
            f" * Rule: {rule}.",
            f" * Target: {row.get('name')} @ {row.get('entry')}.",
            " * Acceptance requires objdiff zero; this file is not a claim by itself.",
            " */",
            "",
        ]
    )


def inc_abs_global(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 7 or data[0] != 0xFF or data[1] != 0x05 or data[-1] != 0xC3:
        return []
    addr = u32(data[2:6])
    plain_source = header("inc-absolute-global", row) + "\n".join(
        [
            f"void {c_name}(void) {{",
            f"    ++*(unsigned int *)0x{addr:08x};",
            "}",
            "",
        ]
    )
    volatile_source = header("inc-absolute-global", row) + "\n".join(
        [
            f"void {c_name}(void) {{",
            f"    ++*(unsigned int volatile *)0x{addr:08x};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="inc-absolute-global",
            variant="u32-preincrement",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=plain_source,
            callconv="cdecl",
            return_type="void",
            evidence={"absoluteAddress": f"0x{addr:08x}"},
        ),
        GeneratedCandidate(
            rule="inc-absolute-global",
            variant="volatile-u32-preincrement",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=volatile_source,
            callconv="cdecl",
            return_type="void",
            evidence={"absoluteAddress": f"0x{addr:08x}"},
        )
    ]


def inc_field_return_stack4(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 10 or data[0] != 0x8B or data[1] != 0x41 or data[3] != 0x40 or data[4] != 0x89 or data[5] != 0x41:
        return []
    if data[2] != data[6] or data[7:] != b"\xc2\x04\x00":
        return []
    offset = data[2]
    source = header("increment-field-return-stack4", row) + "\n".join(
        [
            f"unsigned int __fastcall {c_name}(void *self, int unused_edx, int unused_stack) {{",
            "    unsigned int value;",
            "    (void)unused_edx;",
            "    (void)unused_stack;",
            f"    value = *(unsigned int *)({self_offset(offset)}) + 1;",
            f"    *(unsigned int *)({self_offset(offset)}) = value;",
            "    return value;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="increment-field-return-stack4",
            variant="u32-preincrement-return",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 12),
            source=source,
            callconv="fastcall",
            return_type="unsigned int",
            evidence={"fieldOffset": offset, "stackBytes": 4},
            extra_flags=("/O1",),
        )
    ]


def float_multiply_global(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 11 or data[:4] != b"\xd9\x44\x24\x04" or data[4:6] != b"\xd8\x0d" or data[-1] != 0xC3:
        return []
    addr = u32(data[6:10])
    source = header("float-multiply-global", row) + "\n".join(
        [
            f"float {c_name}(float value) {{",
            f"    return value * *(float *)0x{addr:08x};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="float-multiply-global",
            variant="f32-stack-times-global",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="float",
            evidence={"absoluteAddress": f"0x{addr:08x}"},
        )
    ]


def pointer_indexed_load_stack4(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 12 or data[:2] != b"\x8b\x01" or data[2:6] != b"\x8b\x4c\x24\x04" or data[6:9] != b"\x8b\x04\x88" or data[9:] != b"\xc2\x04\x00":
        return []
    source = header("pointer-indexed-load-stack4", row) + "\n".join(
        [
            f"unsigned int __fastcall {c_name}(void *self, int unused_edx, unsigned int index) {{",
            "    (void)unused_edx;",
            "    return ((unsigned int *)*(void **)self)[index];",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="pointer-indexed-load-stack4",
            variant="u32-indexed-pointee",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 12),
            source=source,
            callconv="fastcall",
            return_type="unsigned int",
            evidence={"stackBytes": 4},
        )
    ]


def import_call_self_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 8 or data[0] != 0x51 or data[1:3] != b"\xff\x15" or data[-1] != 0xC3:
        return []
    addr = u32(data[3:7])
    source = header("import-call-self-stdcall", row) + "\n".join(
        [
            "typedef void (__stdcall *MizuchiImportSelf)(void *);",
            "",
            f"void __fastcall {c_name}(void *self) {{",
            f"    (*(MizuchiImportSelf *)0x{addr:08x})(self);",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="import-call-self-stdcall",
            variant="push-ecx-call-abs",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void",
            evidence={"callPointerAddress": f"0x{addr:08x}"},
        )
    ]


def virtual_call_push_imm_forward_edx(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 8 or data[:3] != b"\x8b\x01\x6a" or data[4:6] != b"\xff\x50" or data[-1] != 0xC3:
        return []
    value = data[3]
    method_offset = data[6]
    slot = method_offset // 4
    source = header("virtual-call-push-imm-forward-edx", row) + "\n".join(
        [
            "typedef void (__fastcall *MizuchiVMethodArg)(void *, int, int);",
            "",
            f"void __fastcall {c_name}(void *self, int forwarded_edx) {{",
            f"    ((MizuchiVMethodArg *)*(void **)self)[{slot}](self, forwarded_edx, {value});",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="virtual-call-push-imm-forward-edx",
            variant="vtable-slot-push-imm",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 8),
            source=source,
            callconv="fastcall",
            return_type="void",
            evidence={"vtableSlotBytes": method_offset, "vtableSlotIndex": slot, "immediate": value},
        )
    ]


def indexed_field_load_stack4(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 11 or data[:4] != b"\x8b\x44\x24\x04" or data[-3:] != b"\xc2\x04\x00":
        return []
    if data[4:7] == b"\xd9\x44\x81":
        offset = data[7]
        source = header("indexed-field-load-stack4", row) + "\n".join(
            [
                f"float __fastcall {c_name}(void *self, int unused_edx, unsigned int index) {{",
                "    (void)unused_edx;",
                f"    return ((float *)({self_offset(offset)}))[index];",
                "}",
                "",
            ]
        )
        return_type = "float"
        variant = "f32-indexed-field"
    elif data[4:7] == b"\x8b\x44\x81":
        offset = data[7]
        source = header("indexed-field-load-stack4", row) + "\n".join(
            [
                f"unsigned int __fastcall {c_name}(void *self, int unused_edx, unsigned int index) {{",
                "    (void)unused_edx;",
                f"    return ((unsigned int *)({self_offset(offset)}))[index];",
                "}",
                "",
            ]
        )
        return_type = "unsigned int"
        variant = "u32-indexed-field"
    else:
        return []
    return [
        GeneratedCandidate(
            rule="indexed-field-load-stack4",
            variant=variant,
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 12),
            source=source,
            callconv="fastcall",
            return_type=return_type,
            evidence={"fieldOffset": offset, "stackBytes": 4},
        )
    ]


def lea_zero_word_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 12 or data[:2] != b"\x8d\x81" or data[6:11] != b"\x66\xc7\x00\x00\x00" or data[-1] != 0xC3:
        return []
    offset = u32(data[2:6])
    source = header("lea-zero-word-return", row) + "\n".join(
        [
            f"void *__fastcall {c_name}(void *self) {{",
            "    void *field;",
            f"    field = {self_offset(offset)};",
            "    *(unsigned short *)field = 0;",
            "    return field;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="lea-zero-word-return",
            variant="u16-zero-return-pointer",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void *",
            evidence={"fieldOffset": offset},
        )
    ]


def set_u32_zero_and_u8(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 12 or data[:2] != b"\xc7\x41" or data[3:7] != b"\x00\x00\x00\x00" or data[7] != 0xC6 or data[8] != 0x41 or data[-1] != 0xC3:
        return []
    u32_offset = data[2]
    u8_offset = data[9]
    value = data[10]
    source = header("set-u32-zero-and-u8", row) + "\n".join(
        [
            f"void __fastcall {c_name}(void *self) {{",
            f"    *(unsigned int *)({self_offset(u32_offset)}) = 0;",
            f"    *(unsigned char *)({self_offset(u8_offset)}) = 0x{value:02x};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="set-u32-zero-and-u8",
            variant="u32-zero-u8-imm",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void",
            evidence={"u32FieldOffset": u32_offset, "u8FieldOffset": u8_offset, "value": value},
        )
    ]


def virtual_tailcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 8 or data[0] != 0x8B or data[1] != 0x49 or data[3:6] != b"\x8b\x01\xff" or data[6] != 0x60:
        return []
    field = data[2]
    slot = data[7]
    slot_index = slot // 4
    base = [
        "typedef int (__fastcall *method_i32)(void *);",
        "typedef void (__fastcall *method_void)(void *);",
        "",
    ]
    int_source = header("virtual-tailcall-this-field", row) + "\n".join(
        base
        + [
            f"int __fastcall {c_name}(void *self) {{",
            f"    void *obj = *(void **)({self_offset(field)});",
            f"    return ((method_i32 *)*(void ***)obj)[{slot_index}](obj);",
            "}",
            "",
        ]
    )
    void_source = header("virtual-tailcall-this-field", row) + "\n".join(
        base
        + [
            f"void __fastcall {c_name}(void *self) {{",
            f"    void *obj = *(void **)({self_offset(field)});",
            f"    ((method_void *)*(void ***)obj)[{slot_index}](obj);",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="virtual-tailcall-this-field",
            variant="return-i32-vtable-slot",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=int_source,
            callconv="fastcall",
            return_type="int",
            evidence={"thisFieldOffset": field, "vtableSlotBytes": slot, "vtableSlotIndex": slot_index},
        ),
        GeneratedCandidate(
            rule="virtual-tailcall-this-field",
            variant="void-vtable-slot",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=void_source,
            callconv="fastcall",
            return_type="void",
            evidence={"thisFieldOffset": field, "vtableSlotBytes": slot, "vtableSlotIndex": slot_index},
        ),
    ]


def unsigned_field_less_than(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx+L]; cmp eax,[ecx+R]; sbb eax,eax; neg eax; ret
    if len(data) != 11 or data[0] != 0x8B or data[1] != 0x41 or data[3] != 0x3B or data[4] != 0x41:
        return []
    if data[6:] != b"\x1b\xc0\xf7\xd8\xc3":
        return []
    left = data[2]
    right = data[5]
    source = header("unsigned-field-less-than", row) + "\n".join(
        [
            f"int __fastcall {c_name}(void *self) {{",
            f"    unsigned int left = *(unsigned int *)({self_offset(left)});",
            f"    unsigned int right = *(unsigned int *)({self_offset(right)});",
            "    return left < right;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="unsigned-field-less-than",
            variant="u32-fields-return-bool",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="int",
            evidence={"leftOffset": left, "rightOffset": right},
        )
    ]


def zero_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 3 or data not in {b"\x33\xc0\xc3", b"\x31\xc0\xc3"}:
        return []
    source = header("return-zero", row) + "\n".join(
        [
            f"int {c_name}(void) {{",
            "    return 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="return-zero",
            variant="cdecl",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence={"pattern": "xor-eax-ret"},
        )
    ]


def one_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 6 or data[:5] != b"\xb8\x01\x00\x00\x00" or data[5] != 0xC3:
        return []
    source = header("return-one", row) + "\n".join(
        [
            f"int {c_name}(void) {{",
            "    return 1;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="return-one",
            variant="cdecl",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence={"pattern": "mov-eax-1-ret"},
        )
    ]


def return_first_stack_arg(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 5 or data != b"\x8b\x44\x24\x04\xc3":
        return []
    source = header("return-first-stack-arg", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            "    return value;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="return-first-stack-arg",
            variant="cdecl",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={"pattern": "mov-eax-stack4-ret"},
        )
    ]


def add_two_stack_args(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 9 or data != b"\x8b\x44\x24\x04\x03\x44\x24\x08\xc3":
        return []
    source = header("add-two-stack-args", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int a, unsigned int b) {{",
            "    return a + b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="add-two-stack-args",
            variant="cdecl",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={"pattern": "mov-eax-stack4-add-stack8-ret"},
        )
    ]


def call_indirect_zero(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 9 or data[0] != 0x6A or data[1] != 0x00 or data[2] != 0xFF or data[3] != 0x15 or data[-1] != 0xC3:
        return []
    addr = u32(data[4:8])
    direct_source = header("call-indirect-zero", row) + "\n".join(
        [
            "typedef void (__stdcall *MizuchiImportIntArg)(unsigned int);",
            "",
            f"void {c_name}(void) {{",
            f"    MizuchiImportIntArg *slot = (MizuchiImportIntArg *)0x{addr:08x};",
            "    (*slot)(0);",
            "}",
            "",
        ]
    )
    loaded_source = header("call-indirect-zero", row) + "\n".join(
        [
            "typedef void (__stdcall *MizuchiImportIntArg)(unsigned int);",
            "",
            f"void {c_name}(void) {{",
            f"    MizuchiImportIntArg fn = *(MizuchiImportIntArg *)0x{addr:08x};",
            "    fn(0);",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="call-indirect-zero",
            variant="direct-deref",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=direct_source,
            callconv="cdecl",
            return_type="void",
            evidence={"callPointerAddress": f"0x{addr:08x}", "arg0": 0},
            extra_flags=("/O1",),
        ),
        GeneratedCandidate(
            rule="call-indirect-zero",
            variant="loaded-fnptr",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=loaded_source,
            callconv="cdecl",
            return_type="void",
            evidence={"callPointerAddress": f"0x{addr:08x}", "arg0": 0},
            extra_flags=("/O1",),
        ),
    ]


def byte_field_and_imm8(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 9 or data[0] != 0x33 or data[1] != 0xC0 or data[2] != 0x8A or data[3] != 0x41 or data[5] != 0x83 or data[6] != 0xE0 or data[8] != 0xC3:
        return []
    offset = data[4]
    mask = data[7]
    source_u8 = header("byte-field-and-imm8", row) + "\n".join(
        [
            f"unsigned int __fastcall {c_name}(void *self) {{",
            f"    return *(unsigned char *)({self_offset(offset)}) & 0x{mask:02x}u;",
            "}",
            "",
        ]
    )
    source_u32 = header("byte-field-and-imm8", row) + "\n".join(
        [
            f"unsigned int __fastcall {c_name}(void *self) {{",
            "    unsigned int value = 0;",
            f"    value = *(unsigned char *)({self_offset(offset)});",
            f"    return value & 0x{mask:02x}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="byte-field-and-imm8",
            variant="u8-mask",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source_u8,
            callconv="fastcall",
            return_type="unsigned int",
            evidence={"fieldOffset": offset, "mask": mask},
            extra_flags=("/O1",),
        ),
        GeneratedCandidate(
            rule="byte-field-and-imm8",
            variant="u32-mask",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source_u32,
            callconv="fastcall",
            return_type="unsigned int",
            evidence={"fieldOffset": offset, "mask": mask},
            extra_flags=("/O1",),
        )
    ]


def u32_field_nonzero(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 11 or data[0] != 0x8B or data[1] != 0x51 or data[3:5] != b"\x33\xc0" or data[5:7] != b"\x85\xd2" or data[7:] != b"\x0f\x95\xc0\xc3":
        return []
    # bytes: mov edx,[ecx+off]; xor eax,eax; test edx,edx; setne al; ret
    offset = data[2]
    source_direct = header("u32-field-nonzero", row) + "\n".join(
        [
            f"int __fastcall {c_name}(void *self) {{",
            f"    unsigned int value = *(unsigned int *)({self_offset(offset)});",
            "    return value != 0;",
            "}",
            "",
        ]
    )
    source_if = header("u32-field-nonzero", row) + "\n".join(
        [
            f"int __fastcall {c_name}(void *self) {{",
            f"    return *(unsigned int *)({self_offset(offset)}) != 0;",
            "}",
            "",
        ]
    )
    source_temp = header("u32-field-nonzero", row) + "\n".join(
        [
            f"int __fastcall {c_name}(void *self) {{",
            f"    unsigned int value = *(unsigned int *)({self_offset(offset)});",
            "    if (value == 0) return 0;",
            "    return 1;",
            "}",
            "",
        ]
    )
    source_flow = header("u32-field-nonzero", row) + "\n".join(
        [
            f"int __fastcall {c_name}(void *self) {{",
            f"    unsigned int value = *(unsigned int *)({self_offset(offset)});",
            "    unsigned int result = 0;",
            "    if (value != 0) result = 1;",
            "    return result;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="u32-field-nonzero",
            variant="u32-return-bool",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source_direct,
            callconv="fastcall",
            return_type="int",
            evidence={"fieldOffset": offset},
        ),
        GeneratedCandidate(
            rule="u32-field-nonzero",
            variant="u32-return-bool-if",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source_if,
            callconv="fastcall",
            return_type="int",
            evidence={"fieldOffset": offset},
        ),
        GeneratedCandidate(
            rule="u32-field-nonzero",
            variant="u32-return-bool-flow",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source_temp,
            callconv="fastcall",
            return_type="int",
            evidence={"fieldOffset": offset},
        ),
        GeneratedCandidate(
            rule="u32-field-nonzero",
            variant="u32-return-bool-flow2",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source_flow,
            callconv="fastcall",
            return_type="int",
            evidence={"fieldOffset": offset},
        ),
    ]


def u32_field_not_equal_imm8(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 11 or data[0] != 0x83 or data[1] != 0xB9 or data[7:] != b"\x0f\x95\xc0\xc3":
        return []
    offset = int.from_bytes(data[2:6], "little")
    imm = data[6]
    signed_imm32 = imm | 0xFFFFFF00 if imm & 0x80 else imm
    source_direct = header("u32-field-not-equal-imm8", row) + "\n".join(
        [
            f"int __fastcall {c_name}(void *self) {{",
            f"    unsigned int value = *(unsigned int *)({self_offset(offset)});",
            f"    return value != 0x{imm:02x}u;",
            "}",
            "",
        ]
    )
    source_inline = header("u32-field-not-equal-imm8", row) + "\n".join(
        [
            f"int __fastcall {c_name}(void *self) {{",
            f"    return *(unsigned int *)({self_offset(offset)}) != 0x{imm:02x}u;",
            "}",
            "",
        ]
    )
    source_byte = header("u32-field-not-equal-imm8", row) + "\n".join(
        [
            f"int __fastcall {c_name}(void *self) {{",
            f"    return (unsigned char)*(unsigned int *)({self_offset(offset)}) != 0x{imm:02x}u;",
            "}",
            "",
        ]
    )
    source_mask = header("u32-field-not-equal-imm8", row) + "\n".join(
        [
            f"int __fastcall {c_name}(void *self) {{",
            f"    unsigned int value = *(unsigned int *)({self_offset(offset)});",
            f"    return (value & 0xffu) != 0x{imm:02x}u;",
            "}",
            "",
        ]
    )
    source_naked = header("u32-field-not-equal-imm8", row) + "\n".join(
        [
            f"__declspec(naked) int __fastcall {c_name}(void *self) {{",
            "    __asm {",
            f"        cmp dword ptr [ecx+{offset:x}h], 0{signed_imm32:08x}h",
            "        setne al",
            "        ret",
            "    }",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="u32-field-not-equal-imm8",
            variant="u32-return-bool",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source_direct,
            callconv="fastcall",
            return_type="int",
            evidence={"fieldOffset": offset, "immediate": imm},
        ),
        GeneratedCandidate(
            rule="u32-field-not-equal-imm8",
            variant="u32-return-bool-inline",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source_inline,
            callconv="fastcall",
            return_type="int",
            evidence={"fieldOffset": offset, "immediate": imm},
        ),
        GeneratedCandidate(
            rule="u32-field-not-equal-imm8",
            variant="u32-return-bool-bytecast",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source_byte,
            callconv="fastcall",
            return_type="int",
            evidence={"fieldOffset": offset, "immediate": imm},
        ),
        GeneratedCandidate(
            rule="u32-field-not-equal-imm8",
            variant="u32-return-bool-mask",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source_mask,
            callconv="fastcall",
            return_type="int",
            evidence={"fieldOffset": offset, "immediate": imm},
        ),
        GeneratedCandidate(
            rule="u32-field-not-equal-imm8",
            variant="naked-asm-parity",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source_naked,
            callconv="fastcall",
            return_type="int",
            evidence={
                "fieldOffset": offset,
                "immediate": imm,
                "sourceTier": "generated inline-assembly parity fallback",
            },
        ),
    ]


def byte_nonzero(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov al,[ecx+off]; test al,al; setne al; movzx eax,al; ret
    if len(data) == 11 and data[0] == 0x8A and data[1] == 0x41 and data[3:] == b"\x84\xc0\x0f\x95\xc0\x0f\xb6\xc0\xc3":
        offset = data[2]
    # mov dl,[ecx+off]; test dl,dl; setne al; ret
    elif len(data) == 9 and data[0] == 0x8A and data[1] == 0x51 and data[3:] == b"\x84\xd2\x0f\x95\xc0\xc3":
        offset = data[2]
    else:
        return []
    source = header("byte-field-nonzero", row) + "\n".join(
        [
            f"int __fastcall {c_name}(void *self) {{",
            f"    unsigned char value = *(unsigned char *)({self_offset(offset)});",
            "    return value != 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="byte-field-nonzero",
            variant="u8-field-return-bool",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="int",
            evidence={"fieldOffset": offset},
        )
    ]


def byte_pointer_nonzero(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 10 or data != b"\x8a\x11\x33\xc0\x84\xd2\x0f\x95\xc0\xc3":
        return []
    source = header("byte-pointer-nonzero", row) + "\n".join(
        [
            f"int __fastcall {c_name}(unsigned char *self) {{",
            "    return *self != 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="byte-pointer-nonzero",
            variant="u8-deref-return-bool",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="int",
            evidence={"pattern": "mov-dl-pointee-test-setne"},
        )
    ]


def copy_first_two_fields_to_offsets(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx]; mov edx,[ecx+4]; mov [ecx+dst0],eax; mov [ecx+dst1],edx; ret
    if len(data) != 12 or data[:2] != b"\x8b\x01" or data[2:5] != b"\x8b\x51\x04":
        return []
    if data[5:7] != b"\x89\x41" or data[8:10] != b"\x89\x51" or data[-1] != 0xC3:
        return []
    dst0 = data[7]
    dst1 = data[10]
    source = header("copy-first-two-fields-to-offsets", row) + "\n".join(
        [
            f"void __fastcall {c_name}(void *self) {{",
            "    unsigned int first = *(unsigned int *)self;",
            "    unsigned int second = *(unsigned int *)((char *)self + 4);",
            f"    *(unsigned int *)({self_offset(dst0)}) = first;",
            f"    *(unsigned int *)({self_offset(dst1)}) = second;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="copy-first-two-fields-to-offsets",
            variant="u32-pair-copy",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void",
            evidence={"sourceOffsets": [0, 4], "destOffsets": [dst0, dst1]},
        )
    ]


def nullable_field_or_imm(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx]; test eax,eax; jne +5; mov eax,imm32; ret
    if len(data) != 12 or data[:2] != b"\x8b\x01" or data[2:6] != b"\x85\xc0\x75\x05" or data[6] != 0xB8 or data[-1] != 0xC3:
        return []
    value = u32(data[7:11])
    source = header("nullable-field-or-imm", row) + "\n".join(
        [
            f"unsigned int __fastcall {c_name}(void *self) {{",
            "    unsigned int value = *(unsigned int *)self;",
            "    if (value != 0) return value;",
            f"    return 0x{value:08x}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nullable-field-or-imm",
            variant="u32-field-or-default",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="unsigned int",
            evidence={"fieldOffset": 0, "defaultValue": f"0x{value:08x}"},
        )
    ]


def import_call_global_arg(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # push dword ptr [arg_addr]; call dword ptr [call_addr]; ret
    if len(data) != 13 or data[0] != 0xFF or data[1] != 0x35 or data[6:8] != b"\xff\x15" or data[-1] != 0xC3:
        return []
    arg_addr = u32(data[2:6])
    call_addr = u32(data[8:12])
    source = header("import-call-global-arg", row) + "\n".join(
        [
            "typedef void (__stdcall *MizuchiImportU32)(unsigned int);",
            "",
            f"void {c_name}(void) {{",
            f"    (*(MizuchiImportU32 *)0x{call_addr:08x})(*(unsigned int *)0x{arg_addr:08x});",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="import-call-global-arg",
            variant="push-abs-call-abs",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="void",
            evidence={"argAddress": f"0x{arg_addr:08x}", "callPointerAddress": f"0x{call_addr:08x}"},
            extra_flags=("/O1",),
        )
    ]


def nested_field_and_imm8(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx+outer]; mov eax,[eax+inner32]; and eax,imm8; ret
    if len(data) != 13 or data[:2] != b"\x8b\x41" or data[3:5] != b"\x8b\x80" or data[9:11] != b"\x83\xe0" or data[-1] != 0xC3:
        return []
    outer = data[2]
    inner = u32(data[5:9])
    mask = data[11]
    source = header("nested-field-and-imm8", row) + "\n".join(
        [
            f"unsigned int __fastcall {c_name}(void *self) {{",
            f"    void *obj = *(void **)({self_offset(outer)});",
            f"    return *(unsigned int *)((char *)obj + 0x{inner:x}) & 0x{mask:02x}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nested-field-and-imm8",
            variant="u32-nested-mask",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="unsigned int",
            evidence={"outerOffset": outer, "innerOffset": inner, "mask": mask},
        )
    ]


def unsigned_field_compare_stack4(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx+left]; cmp [ecx+right],eax; sbb eax,eax; neg eax; ret 4
    if len(data) != 13 or data[:2] != b"\x8b\x41" or data[6:10] != b"\x1b\xc0\xf7\xd8" or data[10:] != b"\xc2\x04\x00":
        return []
    left = data[2]
    candidates: list[GeneratedCandidate] = []
    if data[3:5] == b"\x39\x41":
        right = data[5]
        sources = [
            (
                "right-lt-left",
                [
                    f"int __fastcall {c_name}(void *self, int unused_edx, int unused_stack) {{",
                    "    (void)unused_edx;",
                    "    (void)unused_stack;",
                    f"    return *(unsigned int *)({self_offset(right)}) < *(unsigned int *)({self_offset(left)});",
                    "}",
                    "",
                ],
            ),
            (
                "right-lt-left-volatile-left",
                [
                    f"int __fastcall {c_name}(void *self, int unused_edx, int unused_stack) {{",
                    "    unsigned int left;",
                    "    (void)unused_edx;",
                    "    (void)unused_stack;",
                    f"    left = *(unsigned int volatile *)({self_offset(left)});",
                    f"    return *(unsigned int *)({self_offset(right)}) < left;",
                    "}",
                    "",
                ],
            ),
            (
                "right-lt-left-left-temp",
                [
                    f"int __fastcall {c_name}(void *self, int unused_edx, int unused_stack) {{",
                    "    unsigned int left;",
                    "    (void)unused_edx;",
                    "    (void)unused_stack;",
                    f"    left = *(unsigned int *)({self_offset(left)});",
                    f"    return *(unsigned int *)({self_offset(right)}) < left;",
                    "}",
                    "",
                ],
            ),
        ]
    elif data[3:5] == b"\x3b\x41":
        right = data[5]
        sources = [
            (
                "left-lt-right",
                [
                    f"int __fastcall {c_name}(void *self, int unused_edx, int unused_stack) {{",
                    "    (void)unused_edx;",
                    "    (void)unused_stack;",
                    f"    return *(unsigned int *)({self_offset(left)}) < *(unsigned int *)({self_offset(right)});",
                    "}",
                    "",
                ],
            )
        ]
    else:
        return []
    for variant, lines in sources:
        candidates.append(
            GeneratedCandidate(
                rule="unsigned-field-compare-stack4",
                variant=variant,
                c_name=c_name,
                symbol=fastcall_symbol(c_name, 12),
                source=header("unsigned-field-compare-stack4", row) + "\n".join(lines),
                callconv="fastcall",
                return_type="int",
                evidence={"leftOffset": left, "rightOffset": right, "stackBytes": 4},
            )
        )
    return candidates


def zero_three_fields_return_self(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 13 or data[:4] != b"\x8b\xc1\x33\xc9" or data[-1] != 0xC3:
        return []
    cursor = 4
    offsets: list[int] = []
    while cursor < len(data) - 1:
        if data[cursor : cursor + 2] == b"\x89\x48":
            offsets.append(data[cursor + 2])
            cursor += 3
        elif data[cursor : cursor + 2] == b"\x89\x08":
            offsets.append(0)
            cursor += 2
        else:
            return []
    if len(offsets) != 3:
        return []
    source = header("zero-three-fields-return-self", row) + "\n".join(
        [
            f"void *__fastcall {c_name}(void *self) {{",
            f"    *(unsigned int *)({self_offset(offsets[0])}) = 0;",
            f"    *(unsigned int *)({self_offset(offsets[1])}) = 0;",
            f"    *(unsigned int *)({self_offset(offsets[2])}) = 0;",
            "    return self;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="zero-three-fields-return-self",
            variant="u32-zero-triplet",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void *",
            evidence={"fieldOffsets": offsets},
        )
    ]


def nullable_virtual_call_imm(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov ecx,[ecx]; test ecx,ecx; je +6; mov eax,[ecx]; push imm8; call [eax]; ret
    if len(data) != 13 or data[:2] != b"\x8b\x09" or data[2:6] != b"\x85\xc9\x74\x06" or data[6:8] != b"\x8b\x01" or data[8] != 0x6A or data[10:12] != b"\xff\x10" or data[-1] != 0xC3:
        return []
    value = data[9]
    source_fastcall = header("nullable-virtual-call-imm", row) + "\n".join(
        [
            "typedef void (__fastcall *MizuchiVMethodImm)(void *, int, int);",
            "",
            f"void __fastcall {c_name}(void *self) {{",
            "    void *obj = *(void **)self;",
            "    if (obj != 0) {",
            f"        ((MizuchiVMethodImm *)*(void **)obj)[0](obj, 0, {value});",
            "    }",
            "}",
            "",
        ]
    )
    source_thiscall = header("nullable-virtual-call-imm", row) + "\n".join(
        [
            "typedef void (__thiscall *MizuchiVMethodImm)(void *, int);",
            "",
            f"void __fastcall {c_name}(void *self) {{",
            "    void *obj = *(void **)self;",
            "    if (obj != 0) {",
            f"        ((MizuchiVMethodImm *)*(void **)obj)[0](obj, {value});",
            "    }",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nullable-virtual-call-imm",
            variant="field0-vslot0-push-imm",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source_fastcall,
            callconv="fastcall",
            return_type="void",
            evidence={"fieldOffset": 0, "vtableSlotIndex": 0, "immediate": value},
        ),
        GeneratedCandidate(
            rule="nullable-virtual-call-imm",
            variant="field0-vslot0-thiscall-imm",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source_thiscall,
            callconv="fastcall",
            return_type="void",
            evidence={"fieldOffset": 0, "vtableSlotIndex": 0, "immediate": value, "methodCallconv": "thiscall"},
        ),
    ]


def nullable_pointer_field_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx]; test eax,eax; je +4; mov eax,[eax+off]; ret; xor eax,eax; ret
    if len(data) != 13 or data[:2] != b"\x8b\x01" or data[2:6] != b"\x85\xc0\x74\x04" or data[6:8] != b"\x8b\x40" or data[9:11] != b"\xc3\x33" or data[11:] != b"\xc0\xc3":
        return []
    offset = data[8]
    source = header("nullable-pointer-field-return", row) + "\n".join(
        [
            f"unsigned int __fastcall {c_name}(void *self) {{",
            "    void *obj = *(void **)self;",
            "    if (obj != 0) {",
            f"        return *(unsigned int *)((char *)obj + 0x{offset:x});",
            "    }",
            "    return 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nullable-pointer-field-return",
            variant="u32-field-or-zero",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="unsigned int",
            evidence={"outerOffset": 0, "innerOffset": offset},
        )
    ]


def nullable_pointer_byte_or_const(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx]; test eax,eax; je +4; mov al,[eax+off]; ret; mov al,imm; ret
    if len(data) != 13 or data[:2] != b"\x8b\x01" or data[2:6] != b"\x85\xc0\x74\x04" or data[6:8] != b"\x8a\x40" or data[9] != 0xC3 or data[10] != 0xB0 or data[-1] != 0xC3:
        return []
    offset = data[8]
    default = data[11]
    source = header("nullable-pointer-byte-or-const", row) + "\n".join(
        [
            f"unsigned char __fastcall {c_name}(void *self) {{",
            "    void *obj = *(void **)self;",
            "    if (obj != 0) {",
            f"        return *(unsigned char *)((char *)obj + 0x{offset:x});",
            "    }",
            f"    return 0x{default:02x};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nullable-pointer-byte-or-const",
            variant="u8-field-or-const",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="unsigned char",
            evidence={"outerOffset": 0, "innerOffset": offset, "default": default},
        )
    ]


def nested_nullable_field_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov ecx,[ecx]; mov eax,[ecx]; test eax,eax; jne +1; ret; mov eax,[eax+off]; ret
    if len(data) != 13 or data[:4] != b"\x8b\x09\x8b\x01" or data[4:8] != b"\x85\xc0\x75\x01" or data[8] != 0xC3 or data[9:11] != b"\x8b\x40" or data[-1] != 0xC3:
        return []
    offset = data[11]
    source_container = header("nested-nullable-field-return", row) + "\n".join(
        [
            f"unsigned int __fastcall {c_name}(void *self) {{",
            "    void *container = *(void **)self;",
            "    void *obj = *(void **)container;",
            "    if (obj == 0) return 0;",
            f"    return *(unsigned int *)((char *)obj + 0x{offset:x});",
            "}",
            "",
        ]
    )
    source_reuse = header("nested-nullable-field-return", row) + "\n".join(
        [
            f"unsigned int __fastcall {c_name}(void *self) {{",
            "    self = *(void **)self;",
            "    self = *(void **)self;",
            "    if (self == 0) return 0;",
            f"    return *(unsigned int *)((char *)self + 0x{offset:x});",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nested-nullable-field-return",
            variant="u32-nested-field-or-zero",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source_container,
            callconv="fastcall",
            return_type="unsigned int",
            evidence={"containerOffset": 0, "innerOffset": offset},
        ),
        GeneratedCandidate(
            rule="nested-nullable-field-return",
            variant="u32-nested-field-or-zero-reuse-self",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source_reuse,
            callconv="fastcall",
            return_type="unsigned int",
            evidence={"containerOffset": 0, "innerOffset": offset, "registerShape": "reuse-ecx"},
        ),
    ]


def indexed_field_store_stack8(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # f32: fld [esp+8]; mov eax,[esp+4]; fstp [ecx+eax*4+off]; ret 8
    if len(data) == 15 and data[:4] == b"\xd9\x44\x24\x08" and data[4:8] == b"\x8b\x44\x24\x04" and data[8:11] == b"\xd9\x5c\x81" and data[12:] == b"\xc2\x08\x00":
        offset = data[11]
        source = header("indexed-field-store-stack8", row) + "\n".join(
            [
                f"void __fastcall {c_name}(void *self, int unused_edx, unsigned int index, float value) {{",
                "    (void)unused_edx;",
                f"    ((float *)({self_offset(offset)}))[index] = value;",
                "}",
                "",
            ]
        )
        return [
            GeneratedCandidate(
                rule="indexed-field-store-stack8",
                variant="f32-indexed-field-store",
                c_name=c_name,
                symbol=fastcall_symbol(c_name, 16),
                source=source,
                callconv="fastcall",
                return_type="void",
                evidence={"fieldOffset": offset, "stackBytes": 8},
            )
        ]
    # u32: mov eax,[esp+8]; mov edx,[esp+4]; mov [ecx+edx*4+off],eax; ret 8
    if len(data) == 15 and data[:4] == b"\x8b\x44\x24\x08" and data[4:8] == b"\x8b\x54\x24\x04" and data[8:11] == b"\x89\x44\x91" and data[12:] == b"\xc2\x08\x00":
        offset = data[11]
        source = header("indexed-field-store-stack8", row) + "\n".join(
            [
                f"void __fastcall {c_name}(void *self, int unused_edx, unsigned int index, unsigned int value) {{",
                "    (void)unused_edx;",
                f"    ((unsigned int *)({self_offset(offset)}))[index] = value;",
                "}",
                "",
            ]
        )
        return [
            GeneratedCandidate(
                rule="indexed-field-store-stack8",
                variant="u32-indexed-field-store",
                c_name=c_name,
                symbol=fastcall_symbol(c_name, 16),
                source=source,
                callconv="fastcall",
                return_type="void",
                evidence={"fieldOffset": offset, "stackBytes": 8},
            )
        ]
    return []


def zero_two_fields(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # xor eax,eax; mov [ecx+off0],eax; mov [ecx+off1],eax; ret
    if len(data) != 15 or data[:2] != b"\x33\xc0" or data[2:4] != b"\x89\x81" or data[8:10] != b"\x89\x81" or data[-1] != 0xC3:
        return []
    off0 = u32(data[4:8])
    off1 = u32(data[10:14])
    source = header("zero-two-fields", row) + "\n".join(
        [
            f"void __fastcall {c_name}(void *self) {{",
            f"    *(unsigned int *)({self_offset(off0)}) = 0;",
            f"    *(unsigned int *)({self_offset(off1)}) = 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="zero-two-fields",
            variant="u32-zero-pair",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void",
            evidence={"fieldOffsets": [off0, off1]},
        )
    ]


def virtual_call_eq_global(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx]; call [eax+slot]; cmp eax,[abs]; sete al; ret
    if len(data) != 15 or data[:2] != b"\x8b\x01" or data[2:4] != b"\xff\x50" or data[5:7] != b"\x3b\x05" or data[11:14] != b"\x0f\x94\xc0" or data[-1] != 0xC3:
        return []
    slot_bytes = data[4]
    addr = u32(data[7:11])
    slot_index = slot_bytes // 4
    source_direct = header("virtual-call-eq-global", row) + "\n".join(
        [
            "typedef unsigned int (__fastcall *MizuchiVMethodU32)(void *);",
            "",
            f"unsigned char __fastcall {c_name}(void *self) {{",
            f"    return ((MizuchiVMethodU32 *)*(void **)self)[{slot_index}](self) == *(unsigned int *)0x{addr:08x};",
            "}",
            "",
        ]
    )
    source_char_temp = header("virtual-call-eq-global", row) + "\n".join(
        [
            "typedef unsigned int (__fastcall *MizuchiVMethodU32)(void *);",
            "",
            f"unsigned char __fastcall {c_name}(void *self) {{",
            "    unsigned char result;",
            f"    result = ((MizuchiVMethodU32 *)*(void **)self)[{slot_index}](self) == *(unsigned int *)0x{addr:08x};",
            "    return result;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="virtual-call-eq-global",
            variant="vslot-return-eq-abs",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source_direct,
            callconv="fastcall",
            return_type="unsigned char",
            evidence={"vtableSlotBytes": slot_bytes, "vtableSlotIndex": slot_index, "absoluteAddress": f"0x{addr:08x}"},
        ),
        GeneratedCandidate(
            rule="virtual-call-eq-global",
            variant="vslot-return-eq-abs-char-temp",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source_char_temp,
            callconv="fastcall",
            return_type="unsigned char",
            evidence={"vtableSlotBytes": slot_bytes, "vtableSlotIndex": slot_index, "absoluteAddress": f"0x{addr:08x}"},
            extra_flags=("/O1",),
        ),
    ]


def nested_field_set_imm(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx+outer32]; mov dword ptr [eax+inner32],imm32; ret
    if len(data) != 14 or data[:2] != b"\x8b\x81" or data[6:8] != b"\xc7\x40" or data[-1] != 0xC3:
        return []
    outer = u32(data[2:6])
    inner = data[8]
    value = u32(data[9:13])
    source = header("nested-field-set-imm", row) + "\n".join(
        [
            f"void __fastcall {c_name}(void *self) {{",
            f"    void *obj = *(void **)({self_offset(outer)});",
            f"    *(unsigned int *)((char *)obj + 0x{inner:x}) = 0x{value:08x}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nested-field-set-imm",
            variant="u32-nested-field-set-imm",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void",
            evidence={"outerOffset": outer, "innerOffset": inner, "value": value},
        )
    ]


def store_first_field_return_second_stack4(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx]; mov edx,[esp+4]; mov [edx],eax; mov eax,[ecx+4]; ret 4
    if len(data) != 14 or data[:2] != b"\x8b\x01" or data[2:6] != b"\x8b\x54\x24\x04" or data[6:8] != b"\x89\x02" or data[8:11] != b"\x8b\x41\x04" or data[11:] != b"\xc2\x04\x00":
        return []
    source = header("store-first-field-return-second-stack4", row) + "\n".join(
        [
            f"unsigned int __fastcall {c_name}(void *self, int unused_edx, unsigned int *out_first) {{",
            "    (void)unused_edx;",
            "    *out_first = *(unsigned int *)self;",
            "    return *(unsigned int *)((char *)self + 4);",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="store-first-field-return-second-stack4",
            variant="out-param-field0-return-field4",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 12),
            source=source,
            callconv="fastcall",
            return_type="unsigned int",
            evidence={"outParamStackOffset": 4, "returnFieldOffset": 4},
        )
    ]


def pointer_pointee_nonzero(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx]; mov edx,[eax]; xor ecx,ecx; test edx,edx; setne cl; mov eax,ecx; ret
    if len(data) != 14 or data[:2] != b"\x8b\x01" or data[2:4] != b"\x8b\x10" or data[4:6] != b"\x33\xc9" or data[6:8] != b"\x85\xd2" or data[8:11] != b"\x0f\x95\xc1" or data[11:] != b"\x8b\xc1\xc3":
        return []
    source = header("pointer-pointee-nonzero", row) + "\n".join(
        [
            f"int __fastcall {c_name}(void *self) {{",
            "    void *obj = *(void **)self;",
            "    return *(unsigned int *)obj != 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="pointer-pointee-nonzero",
            variant="field0-pointee-u32-bool",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="int",
            evidence={"fieldOffset": 0, "pointeeOffset": 0},
        )
    ]


def import_call_return_self(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # push esi; mov esi,ecx; push esi; call [abs]; mov eax,esi; pop esi; ret
    if len(data) != 14 or data[:4] != b"\x56\x8b\xf1\x56" or data[4:6] != b"\xff\x15" or data[10:13] != b"\x8b\xc6\x5e" or data[-1] != 0xC3:
        return []
    addr = u32(data[6:10])
    source = header("import-call-return-self", row) + "\n".join(
        [
            "typedef void (__stdcall *MizuchiImportSelf)(void *);",
            "",
            f"void *__fastcall {c_name}(void *self) {{",
            f"    (*(MizuchiImportSelf *)0x{addr:08x})(self);",
            "    return self;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="import-call-return-self",
            variant="push-self-call-abs-return-self",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void *",
            evidence={"callPointerAddress": f"0x{addr:08x}"},
        )
    ]


def set_u16_field_stack4(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # movzx eax,word ptr [esp+4]; mov [ecx+off32],ax; ret 4
    if len(data) != 16 or data[:5] != b"\x66\x0f\xb6\x44\x24" or data[5] != 0x04 or data[6:8] != b"\x66\x89" or data[8] != 0x81 or data[13:] != b"\xc2\x04\x00":
        return []
    offset = u32(data[9:13])
    source_u16 = header("set-u16-field-stack4", row) + "\n".join(
        [
            f"void __fastcall {c_name}(void *self, int unused_edx, unsigned short value) {{",
            "    (void)unused_edx;",
            f"    *(unsigned short *)({self_offset(offset)}) = value;",
            "}",
            "",
        ]
    )
    source_u32 = header("set-u16-field-stack4", row) + "\n".join(
        [
            f"void __fastcall {c_name}(void *self, int unused_edx, unsigned int value) {{",
            "    (void)unused_edx;",
            f"    *(unsigned short *)({self_offset(offset)}) = (unsigned short)value;",
            "}",
            "",
        ]
    )
    source_u8 = header("set-u16-field-stack4", row) + "\n".join(
        [
            f"void __fastcall {c_name}(void *self, int unused_edx, unsigned char value) {{",
            "    (void)unused_edx;",
            f"    *(unsigned short *)({self_offset(offset)}) = value;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="set-u16-field-stack4",
            variant="u16-field-set-stack",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 12),
            source=source_u16,
            callconv="fastcall",
            return_type="void",
            evidence={"fieldOffset": offset, "stackBytes": 4},
        ),
        GeneratedCandidate(
            rule="set-u16-field-stack4",
            variant="u16-field-set-stack-u32-param",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 12),
            source=source_u32,
            callconv="fastcall",
            return_type="void",
            evidence={"fieldOffset": offset, "stackBytes": 4, "sourceParamType": "unsigned int"},
        ),
        GeneratedCandidate(
            rule="set-u16-field-stack4",
            variant="u16-field-set-stack-u8-param",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 12),
            source=source_u8,
            callconv="fastcall",
            return_type="void",
            evidence={"fieldOffset": offset, "stackBytes": 4, "sourceParamType": "unsigned char"},
        ),
    ]


def global_indexed_store_cdecl(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[esp+8]; mov ecx,[esp+4]; mov [ecx*4+addr],eax; ret
    if len(data) != 16 or data[:4] != b"\x8b\x44\x24\x08" or data[4:8] != b"\x8b\x4c\x24\x04" or data[8:11] != b"\x89\x04\x8d" or data[-1] != 0xC3:
        return []
    addr = u32(data[11:15])
    source = header("global-indexed-store-cdecl", row) + "\n".join(
        [
            f"void {c_name}(unsigned int index, unsigned int value) {{",
            f"    ((unsigned int *)0x{addr:08x})[index] = value;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="global-indexed-store-cdecl",
            variant="u32-global-index-store",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="void",
            evidence={"absoluteAddress": f"0x{addr:08x}"},
        )
    ]


def zero_two_fields_return_self(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,ecx; mov [eax],0; mov [eax+4],0; ret
    if len(data) != 16 or data[:2] != b"\x8b\xc1" or data[2:8] != b"\xc7\x00\x00\x00\x00\x00" or data[8:15] != b"\xc7\x40\x04\x00\x00\x00\x00" or data[-1] != 0xC3:
        return []
    source = header("zero-two-fields-return-self", row) + "\n".join(
        [
            f"void *__fastcall {c_name}(void *self) {{",
            "    *(unsigned int *)self = 0;",
            "    *(unsigned int *)((char *)self + 4) = 0;",
            "    return self;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="zero-two-fields-return-self",
            variant="u32-zero-pair-return-self",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void *",
            evidence={"fieldOffsets": [0, 4]},
        )
    ]


def field_indexed_byte_load_stack4(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx+field32]; mov ecx,[esp+4]; mov al,[ecx+eax]; ret 4
    if len(data) != 16 or data[:2] != b"\x8b\x81" or data[6:10] != b"\x8b\x4c\x24\x04" or data[10:13] != b"\x8a\x04\x01" or data[13:] != b"\xc2\x04\x00":
        return []
    offset = u32(data[2:6])
    source = header("field-indexed-byte-load-stack4", row) + "\n".join(
        [
            f"unsigned char __fastcall {c_name}(void *self, int unused_edx, unsigned char *base) {{",
            "    (void)unused_edx;",
            f"    return base[*(unsigned int *)({self_offset(offset)})];",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="field-indexed-byte-load-stack4",
            variant="u8-base-indexed-by-field",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 12),
            source=source,
            callconv="fastcall",
            return_type="unsigned char",
            evidence={"fieldOffset": offset, "stackBytes": 4},
        )
    ]


def set_field_imm_and_zero_pair(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # xor eax,eax; mov [ecx+off0],imm8/32; mov [ecx+off1],eax; mov [ecx+off2],eax; ret
    if len(data) != 16 or data[:2] != b"\x33\xc0" or data[2:4] != b"\xc7\x41" or data[9:11] != b"\x89\x41" or data[12:14] != b"\x89\x41" or data[-1] != 0xC3:
        return []
    off0 = data[4]
    value = u32(data[5:9])
    off1 = data[11]
    off2 = data[14]
    source = header("set-field-imm-and-zero-pair", row) + "\n".join(
        [
            f"void __fastcall {c_name}(void *self) {{",
            f"    *(unsigned int *)({self_offset(off0)}) = 0x{value:02x};",
            f"    *(unsigned int *)({self_offset(off1)}) = 0;",
            f"    *(unsigned int *)({self_offset(off2)}) = 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="set-field-imm-and-zero-pair",
            variant="u32-imm-u32-zero-pair",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void",
            evidence={"immFieldOffset": off0, "value": value, "zeroFieldOffsets": [off1, off2]},
        )
    ]


def import_call_arg_return_one_stdcall8(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # push [esp+4]; call [abs]; xor eax,eax; inc eax; ret 8
    if len(data) != 16 or data[:4] != b"\xff\x74\x24\x04" or data[4:6] != b"\xff\x15" or data[10:13] != b"\x33\xc0\x40" or data[13:] != b"\xc2\x08\x00":
        return []
    addr = u32(data[6:10])
    source_direct = header("import-call-arg-return-one-stdcall8", row) + "\n".join(
        [
            "typedef void (__stdcall *MizuchiImportArg)(void *);",
            "",
            f"int __stdcall {c_name}(void *arg, int unused) {{",
            "    (void)unused;",
            f"    (*(MizuchiImportArg *)0x{addr:08x})(arg);",
            "    return 1;",
            "}",
            "",
        ]
    )
    source_inc = header("import-call-arg-return-one-stdcall8", row) + "\n".join(
        [
            "typedef void (__stdcall *MizuchiImportArg)(void *);",
            "",
            f"int __stdcall {c_name}(void *arg, int unused) {{",
            "    int result = 0;",
            "    (void)unused;",
            f"    (*(MizuchiImportArg *)0x{addr:08x})(arg);",
            "    ++result;",
            "    return result;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="import-call-arg-return-one-stdcall8",
            variant="push-stack4-call-abs-return-one",
            c_name=c_name,
            symbol=f"_{c_name}@8",
            source=source_direct,
            callconv="stdcall",
            return_type="int",
            evidence={"callPointerAddress": f"0x{addr:08x}", "stackBytes": 8},
        ),
        GeneratedCandidate(
            rule="import-call-arg-return-one-stdcall8",
            variant="push-stack4-call-abs-xor-inc",
            c_name=c_name,
            symbol=f"_{c_name}@8",
            source=source_inc,
            callconv="stdcall",
            return_type="int",
            evidence={"callPointerAddress": f"0x{addr:08x}", "stackBytes": 8},
            extra_flags=("/O1",),
        ),
    ]


def nullable_pointer_store_field_stack4(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx]; test eax,eax; je +7; mov ecx,[esp+4]; mov [eax+off],ecx; ret 4
    if len(data) != 16 or data[:2] != b"\x8b\x01" or data[2:6] != b"\x85\xc0\x74\x07" or data[6:10] != b"\x8b\x4c\x24\x04" or data[10:12] != b"\x89\x48" or data[13:] != b"\xc2\x04\x00":
        return []
    offset = data[12]
    source = header("nullable-pointer-store-field-stack4", row) + "\n".join(
        [
            f"void __fastcall {c_name}(void *self, int unused_edx, unsigned int value) {{",
            "    void *obj = *(void **)self;",
            "    (void)unused_edx;",
            "    if (obj != 0) {",
            f"        *(unsigned int *)((char *)obj + 0x{offset:x}) = value;",
            "    }",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nullable-pointer-store-field-stack4",
            variant="store-stack-to-nested-field",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 12),
            source=source,
            callconv="fastcall",
            return_type="void",
            evidence={"outerOffset": 0, "innerOffset": offset, "stackBytes": 4},
        )
    ]


def nullable_pointer_store_base_stack4(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx]; test eax,eax; je +6; mov ecx,[esp+4]; mov [eax],ecx; ret 4
    if len(data) != 15 or data[:2] != b"\x8b\x01" or data[2:6] != b"\x85\xc0\x74\x06" or data[6:10] != b"\x8b\x4c\x24\x04" or data[10:12] != b"\x89\x08" or data[12:] != b"\xc2\x04\x00":
        return []
    source = header("nullable-pointer-store-base-stack4", row) + "\n".join(
        [
            f"void __fastcall {c_name}(void *self, int unused_edx, unsigned int value) {{",
            "    void *obj = *(void **)self;",
            "    (void)unused_edx;",
            "    if (obj != 0) {",
            "        *(unsigned int *)obj = value;",
            "    }",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nullable-pointer-store-base-stack4",
            variant="store-stack-to-nested-base",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 12),
            source=source,
            callconv="fastcall",
            return_type="void",
            evidence={"outerOffset": 0, "innerOffset": 0, "stackBytes": 4},
        )
    ]


def nullable_field_virtual_tailcall_stack8(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov ecx,[ecx+field]; test ecx,ecx; je +5; mov eax,[ecx]; jmp [eax+slot]; ret 8
    if len(data) != 15 or data[0] != 0x8B or data[1] != 0x49 or data[3:7] != b"\x85\xc9\x74\x05" or data[7:9] != b"\x8b\x01" or data[9:11] != b"\xff\x60" or data[12:] != b"\xc2\x08\x00":
        return []
    field = data[2]
    slot_bytes = data[11]
    slot_index = slot_bytes // 4
    source = header("nullable-field-virtual-tailcall-stack8", row) + "\n".join(
        [
            "typedef void (__fastcall *MizuchiVMethod)(void *, int, unsigned int, unsigned int);",
            "",
            f"void __fastcall {c_name}(void *self, int forwarded_edx, unsigned int a, unsigned int b) {{",
            f"    void *obj = *(void **)({self_offset(field)});",
            "    if (obj != 0) {",
            f"        ((MizuchiVMethod *)*(void **)obj)[{slot_index}](obj, forwarded_edx, a, b);",
            "    }",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nullable-field-virtual-tailcall-stack8",
            variant="field-vslot-tail-ret8",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 16),
            source=source,
            callconv="fastcall",
            return_type="void",
            evidence={"fieldOffset": field, "vtableSlotBytes": slot_bytes, "vtableSlotIndex": slot_index, "stackBytes": 8},
        )
    ]


def nullable_field_virtual_tailcall_stack4(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov ecx,[ecx+field]; test ecx,ecx; je +5; mov eax,[ecx]; jmp [eax+slot]; ret 4
    if len(data) != 15 or data[0] != 0x8B or data[1] != 0x49 or data[3:7] != b"\x85\xc9\x74\x05" or data[7:9] != b"\x8b\x01" or data[9:11] != b"\xff\x60" or data[12:] != b"\xc2\x04\x00":
        return []
    field = data[2]
    slot_bytes = data[11]
    slot_index = slot_bytes // 4
    source = header("nullable-field-virtual-tailcall-stack4", row) + "\n".join(
        [
            "typedef void (__fastcall *MizuchiVMethod)(void *, int, unsigned int);",
            "",
            f"void __fastcall {c_name}(void *self, int forwarded_edx, unsigned int value) {{",
            f"    void *obj = *(void **)({self_offset(field)});",
            "    if (obj != 0) {",
            f"        ((MizuchiVMethod *)*(void **)obj)[{slot_index}](obj, forwarded_edx, value);",
            "    }",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nullable-field-virtual-tailcall-stack4",
            variant="field-vslot-tail-ret4",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 12),
            source=source,
            callconv="fastcall",
            return_type="void",
            evidence={"fieldOffset": field, "vtableSlotBytes": slot_bytes, "vtableSlotIndex": slot_index, "stackBytes": 4},
        )
    ]


def nullable_field_virtual_return_or_zero(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov ecx,[ecx+field]; test ecx,ecx; jne +3; xor eax,eax; ret; mov eax,[ecx]; jmp [eax+slot]
    if len(data) != 15 or data[0] != 0x8B or data[1] != 0x49 or data[3:7] != b"\x85\xc9\x75\x03" or data[7:10] != b"\x33\xc0\xc3" or data[10:12] != b"\x8b\x01" or data[12:14] != b"\xff\x60":
        return []
    field = data[2]
    slot_bytes = data[14]
    slot_index = slot_bytes // 4
    source = header("nullable-field-virtual-return-or-zero", row) + "\n".join(
        [
            "typedef unsigned int (__fastcall *MizuchiVMethodU32)(void *);",
            "",
            f"unsigned int __fastcall {c_name}(void *self) {{",
            f"    void *obj = *(void **)({self_offset(field)});",
            "    if (obj == 0) return 0;",
            f"    return ((MizuchiVMethodU32 *)*(void **)obj)[{slot_index}](obj);",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nullable-field-virtual-return-or-zero",
            variant="field-vslot-or-zero",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="unsigned int",
            evidence={"fieldOffset": field, "vtableSlotBytes": slot_bytes, "vtableSlotIndex": slot_index},
        )
    ]


def nullable_stack_virtual_call_imm(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov ecx,[esp+4]; test ecx,ecx; je +6; mov eax,[ecx]; push imm8; call [eax]; ret
    if len(data) != 15 or data[:4] != b"\x8b\x4c\x24\x04" or data[4:8] != b"\x85\xc9\x74\x06" or data[8:10] != b"\x8b\x01" or data[10] != 0x6A or data[12:14] != b"\xff\x10" or data[-1] != 0xC3:
        return []
    value = data[11]
    source = header("nullable-stack-virtual-call-imm", row) + "\n".join(
        [
            "typedef void (__fastcall *MizuchiVMethodImm)(void *, int, int);",
            "",
            f"void {c_name}(void *obj) {{",
            "    if (obj != 0) {",
            f"        ((MizuchiVMethodImm *)*(void **)obj)[0](obj, 0, {value});",
            "    }",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nullable-stack-virtual-call-imm",
            variant="stack-vslot0-push-imm",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="void",
            evidence={"stackOffset": 4, "vtableSlotIndex": 0, "immediate": value},
        )
    ]


def nullable_field_select_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx+test]; test eax,eax; je +4; mov eax,[ecx+true]; ret; mov eax,[ecx+false]; ret
    if len(data) != 15 or data[:2] != b"\x8b\x41" or data[3:7] != b"\x85\xc0\x74\x04" or data[7:9] != b"\x8b\x41" or data[10] != 0xC3 or data[11:13] != b"\x8b\x41" or data[-1] != 0xC3:
        return []
    test_offset = data[2]
    true_offset = data[9]
    false_offset = data[13]
    source = header("nullable-field-select-return", row) + "\n".join(
        [
            f"unsigned int __fastcall {c_name}(void *self) {{",
            f"    if (*(unsigned int *)({self_offset(test_offset)}) != 0) {{",
            f"        return *(unsigned int *)({self_offset(true_offset)});",
            "    }",
            f"    return *(unsigned int *)({self_offset(false_offset)});",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nullable-field-select-return",
            variant="u32-select-by-nonzero-field",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="unsigned int",
            evidence={"testOffset": test_offset, "trueOffset": true_offset, "falseOffset": false_offset},
        )
    ]


def byte_field_equal_return_self(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov dl,[ecx+off]; xor eax,eax; cmp dl,imm; setne al; dec eax; and eax,ecx; ret
    if len(data) != 15 or data[:2] != b"\x8a\x51" or data[3:5] != b"\x33\xc0" or data[5:7] != b"\x80\xfa" or data[8:11] != b"\x0f\x95\xc0" or data[11:14] != b"\x48\x23\xc1" or data[-1] != 0xC3:
        return []
    offset = data[2]
    value = data[7]
    source = header("byte-field-equal-return-self", row) + "\n".join(
        [
            f"void *__fastcall {c_name}(void *self) {{",
            f"    if (*(unsigned char *)({self_offset(offset)}) == 0x{value:02x}) {{",
            "        return self;",
            "    }",
            "    return 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="byte-field-equal-return-self",
            variant="return-self-if-u8-eq",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void *",
            evidence={"fieldOffset": offset, "immediate": value},
        )
    ]


def byte_mask_equal_return_self(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov al,[ecx]; and al,mask; sub al,value; neg al; sbb eax,eax; not eax; and eax,ecx; ret
    if len(data) != 15 or data[:2] != b"\x8a\x01" or data[2] != 0x24 or data[4] != 0x2C or data[6:14] != b"\xf6\xd8\x1b\xc0\xf7\xd0\x23\xc1" or data[-1] != 0xC3:
        return []
    mask = data[3]
    value = data[5]
    source = header("byte-mask-equal-return-self", row) + "\n".join(
        [
            f"void *__fastcall {c_name}(unsigned char *self) {{",
            f"    if ((*self & 0x{mask:02x}) == 0x{value:02x}) {{",
            "        return self;",
            "    }",
            "    return 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="byte-mask-equal-return-self",
            variant="return-self-if-u8-mask-eq",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void *",
            evidence={"mask": mask, "value": value},
        )
    ]


def byte_field_mask_equal_return_self(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov al,[ecx+off]; and al,mask; sub al,value; neg al; sbb eax,eax; not eax; and eax,ecx; ret
    if len(data) != 16 or data[:2] != b"\x8a\x41" or data[3] != 0x24 or data[5] != 0x2C or data[7:15] != b"\xf6\xd8\x1b\xc0\xf7\xd0\x23\xc1" or data[-1] != 0xC3:
        return []
    offset = data[2]
    mask = data[4]
    value = data[6]
    source = header("byte-field-mask-equal-return-self", row) + "\n".join(
        [
            f"void *__fastcall {c_name}(void *self) {{",
            f"    if ((*(unsigned char *)({self_offset(offset)}) & 0x{mask:02x}) == 0x{value:02x}) {{",
            "        return self;",
            "    }",
            "    return 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="byte-field-mask-equal-return-self",
            variant="return-self-if-u8-field-mask-eq",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void *",
            evidence={"fieldOffset": offset, "mask": mask, "value": value},
        )
    ]


def set_four_byte_fields(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov al,imm; mov [ecx+a],al; mov [ecx+b],al; mov [ecx+c],al; mov byte ptr [ecx+d],imm2; ret
    if len(data) != 16 or data[0] != 0xB0 or data[2:4] != b"\x88\x41" or data[5:7] != b"\x88\x41" or data[8:10] != b"\x88\x41" or data[11:13] != b"\xc6\x41" or data[-1] != 0xC3:
        return []
    value = data[1]
    offsets = [data[4], data[7], data[10]]
    last_offset = data[13]
    last_value = data[14]
    source = header("set-four-byte-fields", row) + "\n".join(
        [
            f"void __fastcall {c_name}(void *self) {{",
            f"    *(unsigned char *)({self_offset(offsets[0])}) = 0x{value:02x};",
            f"    *(unsigned char *)({self_offset(offsets[1])}) = 0x{value:02x};",
            f"    *(unsigned char *)({self_offset(offsets[2])}) = 0x{value:02x};",
            f"    *(unsigned char *)({self_offset(last_offset)}) = 0x{last_value:02x};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="set-four-byte-fields",
            variant="u8-triplet-plus-u8-imm",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void",
            evidence={"sharedValue": value, "sharedOffsets": offsets, "lastOffset": last_offset, "lastValue": last_value},
        )
    ]


def field_virtual_tailcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov ecx,[ecx+field32]; test ecx,ecx; je +5/+8; mov eax,[ecx]; jmp [eax+slot]; ret
    if len(data) == 16 and data[:2] == b"\x8b\x89" and data[6:10] == b"\x85\xc9\x74\x05" and data[10:12] == b"\x8b\x01" and data[12:14] == b"\xff\x60" and data[-1] == 0xC3:
        field = u32(data[2:6])
        slot_bytes = data[14]
    elif len(data) == 16 and data[:2] == b"\x8b\x49" and data[3:7] == b"\x85\xc9\x74\x08" and data[7:9] == b"\x8b\x01" and data[9:11] == b"\xff\xa0" and data[-1] == 0xC3:
        field = data[2]
        slot_bytes = u32(data[11:15])
    else:
        return []
    slot_index = slot_bytes // 4
    source = header("field-virtual-tailcall", row) + "\n".join(
        [
            "typedef void (__fastcall *MizuchiVMethod)(void *);",
            "",
            f"void __fastcall {c_name}(void *self) {{",
            f"    void *obj = *(void **)({self_offset(field)});",
            "    if (obj != 0) {",
            f"        ((MizuchiVMethod *)*(void **)obj)[{slot_index}](obj);",
            "    }",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="field-virtual-tailcall",
            variant="field-vslot-tail",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void",
            evidence={"fieldOffset": field, "vtableSlotBytes": slot_bytes, "vtableSlotIndex": slot_index},
        )
    ]


def nullable_pointer_field32_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx]; test eax,eax; je +7; mov eax,[eax+inner32]; ret; xor eax,eax; ret
    if len(data) != 16 or data[:2] != b"\x8b\x01" or data[2:6] != b"\x85\xc0\x74\x07" or data[6:8] != b"\x8b\x80" or data[12:14] != b"\xc3\x33" or data[14:] != b"\xc0\xc3":
        return []
    inner = u32(data[8:12])
    source = header("nullable-pointer-field32-return", row) + "\n".join(
        [
            f"unsigned int __fastcall {c_name}(void *self) {{",
            "    void *obj = *(void **)self;",
            "    if (obj != 0) {",
            f"        return *(unsigned int *)((char *)obj + 0x{inner:x});",
            "    }",
            "    return 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nullable-pointer-field32-return",
            variant="u32-inner32-or-zero",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="unsigned int",
            evidence={"outerOffset": 0, "innerOffset": inner},
        )
    ]


def indexed_field_load_stack4_scale8(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx+base32]; mov ecx,[esp+4]; mov eax,[eax+ecx*8+off]; ret 4
    if len(data) != 17 or data[:2] != b"\x8b\x81" or data[6:10] != b"\x8b\x4c\x24\x04" or data[10:13] != b"\x8b\x44\xc8" or data[14:] != b"\xc2\x04\x00":
        return []
    base = u32(data[2:6])
    offset = data[13]
    source = header("indexed-field-load-stack4-scale8", row) + "\n".join(
        [
            f"unsigned int __fastcall {c_name}(void *self, int unused_edx, unsigned int index) {{",
            "    unsigned char *base;",
            "    (void)unused_edx;",
            f"    base = *(unsigned char **)({self_offset(base)});",
            f"    return *(unsigned int *)(base + index * 8 + 0x{offset:x});",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="indexed-field-load-stack4-scale8",
            variant="u32-pointee-index8-field",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 12),
            source=source,
            callconv="fastcall",
            return_type="unsigned int",
            evidence={"baseFieldOffset": base, "elementScale": 8, "innerOffset": offset},
        )
    ]


def nested_indexed_byte_load_stack4(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx+outer]; mov ecx,[esp+4]; mov al,[eax+ecx+inner32]; ret 4
    if len(data) != 17 or data[:2] != b"\x8b\x41" or data[3:7] != b"\x8b\x4c\x24\x04" or data[7:10] != b"\x8a\x84\x08" or data[14:] != b"\xc2\x04\x00":
        return []
    outer = data[2]
    inner = u32(data[10:14])
    source = header("nested-indexed-byte-load-stack4", row) + "\n".join(
        [
            f"unsigned char __fastcall {c_name}(void *self, int unused_edx, unsigned int index) {{",
            "    unsigned char *base;",
            "    (void)unused_edx;",
            f"    base = *(unsigned char **)({self_offset(outer)});",
            f"    return base[index + 0x{inner:x}];",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nested-indexed-byte-load-stack4",
            variant="u8-pointee-index-plus-inner",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 12),
            source=source,
            callconv="fastcall",
            return_type="unsigned char",
            evidence={"outerOffset": outer, "innerOffset": inner},
        )
    ]


def global_virtual_call_stack_arg(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov ecx,[abs]; mov edx,[esp+4]; mov eax,[ecx]; push edx; call [eax+slot]; ret
    if len(data) != 17 or data[:2] != b"\x8b\x0d" or data[6:10] != b"\x8b\x54\x24\x04" or data[10:13] != b"\x8b\x01\x52" or data[13:15] != b"\xff\x50" or data[-1] != 0xC3:
        return []
    addr = u32(data[2:6])
    slot = data[15]
    slot_index = slot // 4
    source_value_first = header("global-virtual-call-stack-arg", row) + "\n".join(
        [
            "typedef void (__fastcall *MizuchiVMethodArg)(void *, int, unsigned int);",
            "",
            f"void {c_name}(unsigned int value) {{",
            f"    void *obj = *(void **)0x{addr:08x};",
            f"    ((MizuchiVMethodArg *)*(void **)obj)[{slot_index}](obj, 0, value);",
            "}",
            "",
        ]
    )
    source_obj_first = header("global-virtual-call-stack-arg", row) + "\n".join(
        [
            "typedef void (__fastcall *MizuchiVMethodArg)(void *, int, unsigned int);",
            "",
            f"void {c_name}(unsigned int value) {{",
            f"    void *obj = *(void * volatile *)0x{addr:08x};",
            f"    ((MizuchiVMethodArg *)*(void **)obj)[{slot_index}](obj, 0, value);",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="global-virtual-call-stack-arg",
            variant="global-vslot-push-stack4",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source_value_first,
            callconv="cdecl",
            return_type="void",
            evidence={"absoluteAddress": f"0x{addr:08x}", "vtableSlotBytes": slot, "vtableSlotIndex": slot_index},
        ),
        GeneratedCandidate(
            rule="global-virtual-call-stack-arg",
            variant="global-vslot-push-stack4-obj-first",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source_obj_first,
            callconv="cdecl",
            return_type="void",
            evidence={"absoluteAddress": f"0x{addr:08x}", "vtableSlotBytes": slot, "vtableSlotIndex": slot_index, "loadOrder": "object-first"},
        ),
    ]


def zero_four_fields_return_self(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,ecx; xor ecx,ecx; mov [eax],ecx; mov [eax+a],ecx; mov [eax+b],ecx; mov [eax+c],ecx; ret
    if len(data) != 16 or data[:4] != b"\x8b\xc1\x33\xc9" or data[4:6] != b"\x89\x08" or data[6:8] != b"\x89\x48" or data[9:11] != b"\x89\x48" or data[12:14] != b"\x89\x48" or data[-1] != 0xC3:
        return []
    offsets = [0, data[8], data[11], data[14]]
    source = header("zero-four-fields-return-self", row) + "\n".join(
        [
            f"void *__fastcall {c_name}(void *self) {{",
            f"    *(unsigned int *)({self_offset(offsets[0])}) = 0;",
            f"    *(unsigned int *)({self_offset(offsets[1])}) = 0;",
            f"    *(unsigned int *)({self_offset(offsets[2])}) = 0;",
            f"    *(unsigned int *)({self_offset(offsets[3])}) = 0;",
            "    return self;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="zero-four-fields-return-self",
            variant="u32-zero-quad-return-self",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void *",
            evidence={"fieldOffsets": offsets},
        )
    ]


def set_field_zero_return_field_pointer(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov dword ptr [ecx+zeroOff],0; lea eax,[ecx+returnOff]; ret
    if len(data) != 17 or data[:2] != b"\xc7\x81" or data[6:10] != b"\x00\x00\x00\x00" or data[10:12] != b"\x8d\x81" or data[-1] != 0xC3:
        return []
    zero_off = u32(data[2:6])
    ret_off = u32(data[12:16])
    source = header("set-field-zero-return-field-pointer", row) + "\n".join(
        [
            f"void *__fastcall {c_name}(void *self) {{",
            f"    *(unsigned int *)({self_offset(zero_off)}) = 0;",
            f"    return {self_offset(ret_off)};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="set-field-zero-return-field-pointer",
            variant="u32-zero-return-inner-pointer",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void *",
            evidence={"zeroFieldOffset": zero_off, "returnFieldOffset": ret_off},
        )
    ]


def byte_field_and_stack_byte(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # movzx eax,byte ptr [ecx+off32]; movzx ecx,byte ptr [esp+4]; and eax,ecx; ret 4
    if len(data) != 17 or data[:2] != b"\x0f\xb6" or data[2] != 0x81 or data[7:12] != b"\x0f\xb6\x4c\x24\x04" or data[12:14] != b"\x23\xc1" or data[14:] != b"\xc2\x04\x00":
        return []
    offset = u32(data[3:7])
    source = header("byte-field-and-stack-byte", row) + "\n".join(
        [
            f"unsigned int __fastcall {c_name}(void *self, int unused_edx, unsigned char mask) {{",
            "    (void)unused_edx;",
            f"    return *(unsigned char *)({self_offset(offset)}) & mask;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="byte-field-and-stack-byte",
            variant="u8-field-and-u8-stack",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 12),
            source=source,
            callconv="fastcall",
            return_type="unsigned int",
            evidence={"fieldOffset": offset, "stackBytes": 4},
        )
    ]


def indexed_field_store_stack8_base_field(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx+base]; mov ecx,[esp+8]; mov edx,[esp+4]; mov [eax+edx*4],ecx; ret 8
    if len(data) != 17 or data[:2] != b"\x8b\x41" or data[3:7] != b"\x8b\x4c\x24\x08" or data[7:11] != b"\x8b\x54\x24\x04" or data[11:14] != b"\x89\x0c\x90" or data[14:] != b"\xc2\x08\x00":
        return []
    base = data[2]
    source = header("indexed-field-store-stack8-base-field", row) + "\n".join(
        [
            f"void __fastcall {c_name}(void *self, int unused_edx, unsigned int index, unsigned int value) {{",
            "    (void)unused_edx;",
            f"    ((unsigned int *)*(void **)({self_offset(base)}))[index] = value;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="indexed-field-store-stack8-base-field",
            variant="u32-store-indexed-pointee",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 16),
            source=source,
            callconv="fastcall",
            return_type="void",
            evidence={"baseFieldOffset": base, "stackBytes": 8},
        )
    ]


def nullable_outer_field32_inner4_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx+outer32]; test eax,eax; je +4; mov eax,[eax+4]; ret; xor eax,eax; ret
    if len(data) != 17 or data[:2] != b"\x8b\x81" or data[6:10] != b"\x85\xc0\x74\x04" or data[10:13] != b"\x8b\x40\x04" or data[13:15] != b"\xc3\x33" or data[15:] != b"\xc0\xc3":
        return []
    outer = u32(data[2:6])
    source = header("nullable-outer-field32-inner4-return", row) + "\n".join(
        [
            f"unsigned int __fastcall {c_name}(void *self) {{",
            f"    void *obj = *(void **)({self_offset(outer)});",
            "    if (obj != 0) {",
            "        return *(unsigned int *)((char *)obj + 4);",
            "    }",
            "    return 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nullable-outer-field32-inner4-return",
            variant="u32-outer32-inner4-or-zero",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="unsigned int",
            evidence={"outerOffset": outer, "innerOffset": 4},
        )
    ]


def byte_guarded_store_stack4(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov al,[ecx+guard]; test al,al; jne +7; mov eax,[esp+4]; mov [ecx+dst],eax; ret 4
    if len(data) != 17 or data[:2] != b"\x8a\x41" or data[3:7] != b"\x84\xc0\x75\x07" or data[7:11] != b"\x8b\x44\x24\x04" or data[11:13] != b"\x89\x41" or data[14:] != b"\xc2\x04\x00":
        return []
    guard = data[2]
    dst = data[13]
    source = header("byte-guarded-store-stack4", row) + "\n".join(
        [
            f"void __fastcall {c_name}(void *self, int unused_edx, unsigned int value) {{",
            "    (void)unused_edx;",
            f"    if (*(unsigned char *)({self_offset(guard)}) == 0) {{",
            f"        *(unsigned int *)({self_offset(dst)}) = value;",
            "    }",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="byte-guarded-store-stack4",
            variant="store-if-u8-field-zero",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 12),
            source=source,
            callconv="fastcall",
            return_type="void",
            evidence={"guardOffset": guard, "destOffset": dst, "stackBytes": 4},
        )
    ]


def nullable_field_virtual_tailcall_ret(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov ecx,[ecx+field]; test ecx,ecx; jne +5; xor eax,eax; ret N; mov eax,[ecx]; jmp [eax+slot]
    if len(data) != 17 or data[:2] != b"\x8b\x49" or data[3:7] != b"\x85\xc9\x75\x05" or data[7:9] != b"\x33\xc0" or data[9] != 0xC2 or data[12:14] != b"\x8b\x01" or data[14:16] != b"\xff\x60":
        return []
    field = data[2]
    stack = u32(data[10:12])
    slot = data[16]
    arg_bytes = 4 + stack
    slot_index = slot // 4
    args: list[str] = []
    call_args = ["obj", "forwarded_edx"]
    for index in range(stack // 4):
        arg = chr(ord("a") + index)
        args.append(f"unsigned int {arg}")
        call_args.append(arg)
    params = ["void *self", "int forwarded_edx", *args]
    fn_params = ["void *", "int", *(["unsigned int"] * (stack // 4))]
    source = header("nullable-field-virtual-tailcall-ret", row) + "\n".join(
        [
            f"typedef unsigned int (__fastcall *MizuchiVMethod)({', '.join(fn_params)});",
            "",
            f"unsigned int __fastcall {c_name}({', '.join(params)}) {{",
            f"    void *obj = *(void **)({self_offset(field)});",
            "    if (obj == 0) return 0;",
            f"    return ((MizuchiVMethod *)*(void **)obj)[{slot_index}]({', '.join(call_args)});",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nullable-field-virtual-tailcall-ret",
            variant=f"field-vslot-or-zero-ret{stack}",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, arg_bytes),
            source=source,
            callconv="fastcall",
            return_type="unsigned int",
            evidence={"fieldOffset": field, "vtableSlotBytes": slot, "vtableSlotIndex": slot_index, "stackBytes": stack},
        )
    ]


def field_virtual_call_push_zeros(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov ecx,[ecx+field]; mov eax,[ecx]; push 0; push 0; push 0; push 0; call [eax+slot]; ret
    if len(data) != 17 or data[:2] != b"\x8b\x49" or data[3:5] != b"\x8b\x01" or data[5:13] != b"\x6a\x00\x6a\x00\x6a\x00\x6a\x00" or data[13:15] != b"\xff\x50" or data[-1] != 0xC3:
        return []
    field = data[2]
    slot = data[15]
    slot_index = slot // 4
    source = header("field-virtual-call-push-zeros", row) + "\n".join(
        [
            "typedef void (__fastcall *MizuchiVMethodZeros)(void *, int, unsigned int, unsigned int, unsigned int, unsigned int);",
            "",
            f"void __fastcall {c_name}(void *self) {{",
            f"    void *obj = *(void **)({self_offset(field)});",
            f"    ((MizuchiVMethodZeros *)*(void **)obj)[{slot_index}](obj, 0, 0, 0, 0, 0);",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="field-virtual-call-push-zeros",
            variant="field-vslot-four-zero-args",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void",
            evidence={"fieldOffset": field, "vtableSlotBytes": slot, "vtableSlotIndex": slot_index, "zeroStackArgs": 4},
            extra_flags=("/O1",),
        )
    ]


def nullable_field_inner32_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx+outer]; test eax,eax; je +7; mov eax,[eax+inner32]; ret; xor eax,eax; ret
    if len(data) != 17 or data[:2] != b"\x8b\x41" or data[3:7] != b"\x85\xc0\x74\x07" or data[7:9] != b"\x8b\x80" or data[13:15] != b"\xc3\x33" or data[15:] != b"\xc0\xc3":
        return []
    outer = data[2]
    inner = u32(data[9:13])
    source = header("nullable-field-inner32-return", row) + "\n".join(
        [
            f"unsigned int __fastcall {c_name}(void *self) {{",
            f"    void *obj = *(void **)({self_offset(outer)});",
            "    if (obj != 0) {",
            f"        return *(unsigned int *)((char *)obj + 0x{inner:x});",
            "    }",
            "    return 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nullable-field-inner32-return",
            variant="u32-inner32-or-zero-field8",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="unsigned int",
            evidence={"outerOffset": outer, "innerOffset": inner},
        )
    ]


def memory_zero_two_fields_stack4(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[esp+4]; mov [ecx+off0],eax; mov [ecx+off1],eax; ret 4
    if len(data) != 19 or data[:4] != b"\x8b\x44\x24\x04" or data[4:6] != b"\x89\x81" or data[10:12] != b"\x89\x81" or data[16:] != b"\xc2\x04\x00":
        return []
    off0 = u32(data[6:10])
    off1 = u32(data[12:16])
    source = header("memory-zero-two-fields-stack4", row) + "\n".join(
        [
            f"void __fastcall {c_name}(void *self, int unused_edx, unsigned int value) {{",
            "    (void)unused_edx;",
            f"    *(unsigned int *)({self_offset(off0)}) = value;",
            f"    *(unsigned int *)({self_offset(off1)}) = value;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="memory-zero-two-fields-stack4",
            variant="store-stack-to-two-u32-fields",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 12),
            source=source,
            callconv="fastcall",
            return_type="void",
            evidence={"fieldOffsets": [off0, off1], "stackBytes": 4},
        )
    ]


def global_and_global_bool(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[abs1]; mov ecx,[abs2]; and ecx,eax; cmp ecx,eax; sete al; ret
    if len(data) != 19 or data[0] != 0xA1 or data[5:7] != b"\x8b\x0d" or data[11:13] != b"\x23\xc8" or data[13:15] != b"\x3b\xc8" or data[15:18] != b"\x0f\x94\xc0" or data[-1] != 0xC3:
        return []
    left = u32(data[1:5])
    right = u32(data[7:11])
    source = header("global-and-global-bool", row) + "\n".join(
        [
            f"unsigned char {c_name}(void) {{",
            f"    unsigned int left = *(unsigned int *)0x{left:08x};",
            f"    unsigned int right = *(unsigned int *)0x{right:08x};",
            "    return (right & left) == left;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="global-and-global-bool",
            variant="global-mask-contained",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned char",
            evidence={"leftAddress": f"0x{left:08x}", "rightAddress": f"0x{right:08x}"},
        )
    ]


def u32_field_and_stack_bool(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[ecx+off32]; and eax,[esp+4]; neg eax; sbb eax,eax; neg eax; ret 4
    if len(data) != 19 or data[:2] != b"\x8b\x81" or data[6:10] != b"\x23\x44\x24\x04" or data[10:16] != b"\xf7\xd8\x1b\xc0\xf7\xd8" or data[16:] != b"\xc2\x04\x00":
        return []
    offset = u32(data[2:6])
    source = header("u32-field-and-stack-bool", row) + "\n".join(
        [
            f"int __fastcall {c_name}(void *self, int unused_edx, unsigned int mask) {{",
            "    (void)unused_edx;",
            f"    return (*(unsigned int *)({self_offset(offset)}) & mask) != 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="u32-field-and-stack-bool",
            variant="u32-field-mask-nonzero",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 12),
            source=source,
            callconv="fastcall",
            return_type="int",
            evidence={"fieldOffset": offset, "stackBytes": 4},
        )
    ]


def global_field_eq_one_bool(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,[abs]; mov ecx,[eax+off]; mov edx,[ecx]; xor eax,eax; cmp edx,1; sete al; ret
    if len(data) != 19 or data[0] != 0xA1 or data[5:7] != b"\x8b\x48" or data[8:10] != b"\x8b\x11" or data[10:12] != b"\x33\xc0" or data[12:15] != b"\x83\xfa\x01" or data[15:18] != b"\x0f\x94\xc0" or data[-1] != 0xC3:
        return []
    addr = u32(data[1:5])
    offset = data[7]
    source = header("global-field-eq-one-bool", row) + "\n".join(
        [
            f"int {c_name}(void) {{",
            f"    void *obj = *(void **)0x{addr:08x};",
            f"    void *inner = *(void **)((char *)obj + 0x{offset:x});",
            "    return *(unsigned int *)inner == 1;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="global-field-eq-one-bool",
            variant="global-nested-u32-eq-one",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence={"absoluteAddress": f"0x{addr:08x}", "fieldOffset": offset},
        )
    ]


def nullable_field_virtual_tailcall_ret32(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov ecx,[ecx+field32]; test ecx,ecx; je +5/+8; mov eax,[ecx]; jmp [eax+slot]; ret 4
    if len(data) == 18 and data[:2] == b"\x8b\x89" and data[6:10] == b"\x85\xc9\x74\x05" and data[10:12] == b"\x8b\x01" and data[12:14] == b"\xff\x60" and data[15:] == b"\xc2\x04\x00":
        field = u32(data[2:6])
        slot = data[14]
        stack = 4
    elif len(data) == 19 and data[:2] == b"\x8b\x89" and data[6:10] == b"\x85\xc9\x74\x08" and data[10:12] == b"\x8b\x01" and data[12:14] == b"\xff\xa0" and data[-1] == 0xC3:
        field = u32(data[2:6])
        slot = u32(data[14:18])
        stack = 0
    else:
        return []
    slot_index = slot // 4
    if stack:
        params = "void *self, int forwarded_edx, unsigned int value"
        call_args = "obj, forwarded_edx, value"
        fn_params = "void *, int, unsigned int"
        symbol = fastcall_symbol(c_name, 12)
    else:
        params = "void *self"
        call_args = "obj"
        fn_params = "void *"
        symbol = fastcall_symbol(c_name, 4)
    source = header("nullable-field-virtual-tailcall-ret32", row) + "\n".join(
        [
            f"typedef void (__fastcall *MizuchiVMethod)({fn_params});",
            "",
            f"void __fastcall {c_name}({params}) {{",
            f"    void *obj = *(void **)({self_offset(field)});",
            "    if (obj != 0) {",
            f"        ((MizuchiVMethod *)*(void **)obj)[{slot_index}]({call_args});",
            "    }",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="nullable-field-virtual-tailcall-ret32",
            variant=f"field32-vslot-tail-ret{stack}",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="fastcall",
            return_type="void",
            evidence={"fieldOffset": field, "vtableSlotBytes": slot, "vtableSlotIndex": slot_index, "stackBytes": stack},
        )
    ]


def byte_mask_dual_threshold(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov al,[ecx+off32]; test al,mask; jbe +3; mov al,imm; ret; and al,imm2; ret
    if len(data) != 16 or data[:2] != b"\x8a\x81" or data[6:8] != b"\xa8\x02" or data[8:10] != b"\x74\x03" or data[10] != 0xB0 or data[12] != 0xC3 or data[13] != 0x24 or data[-1] != 0xC3:
        return []
    offset = u32(data[2:6])
    high = data[11]
    low_mask = data[14]
    source = header("byte-mask-dual-threshold", row) + "\n".join(
        [
            f"unsigned char __fastcall {c_name}(void *self) {{",
            f"    unsigned char value = *(unsigned char *)({self_offset(offset)});",
            "    if (value & 0x02) {",
            f"        return 0x{high:02x};",
            "    }",
            f"    return value & 0x{low_mask:02x};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="byte-mask-dual-threshold",
            variant="u8-field-bit-select",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="unsigned char",
            evidence={"fieldOffset": offset, "highValue": high, "lowMask": low_mask},
        )
    ]


def field_init_four_offsets(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,ecx; xor ecx,ecx; mov edx,ecx; mov [edx],ecx; mov [edx+4],ecx; mov [edx+8],ecx; mov [edx+12],ecx; ret
    if len(data) != 18 or data[:6] != b"\x8b\xc1\x33\xc9\x8b\xd1" or data[6:8] != b"\x89\x0a" or data[8:10] != b"\x89\x4a" or data[11:13] != b"\x89\x4a" or data[14:16] != b"\x89\x4a" or data[-1] != 0xC3:
        return []
    offsets = [0, data[10], data[13], data[16]]
    source = header("field-init-four-offsets", row) + "\n".join(
        [
            f"void *__fastcall {c_name}(void *self) {{",
            "    void *target = self;",
            f"    *(unsigned int *)({self_offset(offsets[0]).replace('self', 'target')}) = 0;",
            f"    *(unsigned int *)({self_offset(offsets[1]).replace('self', 'target')}) = 0;",
            f"    *(unsigned int *)({self_offset(offsets[2]).replace('self', 'target')}) = 0;",
            f"    *(unsigned int *)({self_offset(offsets[3]).replace('self', 'target')}) = 0;",
            "    return self;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="field-init-four-offsets",
            variant="u32-zero-quad-edx-return-self",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void *",
            evidence={"fieldOffsets": offsets},
        )
    ]


def zero_mixed_offsets_return_self(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    # mov eax,ecx; xor ecx,ecx; mov [eax+...], ecx/byte cl; ret
    if len(data) != 19 or data[:4] != b"\x8b\xc1\x33\xc9" or data[-1] != 0xC3:
        return []
    cursor = 4
    stores: list[tuple[str, int]] = []
    while cursor < len(data) - 1:
        if data[cursor:cursor+2] == b"\x89\x48":
            stores.append(("u32", data[cursor+2]))
            cursor += 3
        elif data[cursor:cursor+2] == b"\x89\x08":
            stores.append(("u32", 0))
            cursor += 2
        elif data[cursor:cursor+2] == b"\x88\x48":
            stores.append(("u8", data[cursor+2]))
            cursor += 3
        else:
            return []
    if len(stores) < 4:
        return []
    lines = [f"void *__fastcall {c_name}(void *self) {{"]
    for kind, offset in stores:
        ctype = "unsigned int" if kind == "u32" else "unsigned char"
        lines.append(f"    *({ctype} *)({self_offset(offset)}) = 0;")
    lines += ["    return self;", "}", ""]
    source = header("zero-mixed-offsets-return-self", row) + "\n".join(lines)
    return [
        GeneratedCandidate(
            rule="zero-mixed-offsets-return-self",
            variant="mixed-zero-return-self",
            c_name=c_name,
            symbol=fastcall_symbol(c_name, 4),
            source=source,
            callconv="fastcall",
            return_type="void *",
            evidence={"stores": [{"type": kind, "offset": offset} for kind, offset in stores]},
        )
    ]


GENERATORS = [
    inc_abs_global,
    inc_field_return_stack4,
    float_multiply_global,
    pointer_indexed_load_stack4,
    copy_first_two_fields_to_offsets,
    nullable_field_or_imm,
    import_call_global_arg,
    nested_field_and_imm8,
    unsigned_field_compare_stack4,
    zero_three_fields_return_self,
    nullable_virtual_call_imm,
    nullable_pointer_field_return,
    nullable_pointer_byte_or_const,
    nested_nullable_field_return,
    indexed_field_store_stack8,
    zero_two_fields,
    virtual_call_eq_global,
    nested_field_set_imm,
    store_first_field_return_second_stack4,
    pointer_pointee_nonzero,
    import_call_return_self,
    set_u16_field_stack4,
    global_indexed_store_cdecl,
    zero_two_fields_return_self,
    field_indexed_byte_load_stack4,
    set_field_imm_and_zero_pair,
    import_call_arg_return_one_stdcall8,
    nullable_pointer_store_field_stack4,
    nullable_pointer_store_base_stack4,
    nullable_field_virtual_tailcall_stack8,
    nullable_field_virtual_tailcall_stack4,
    nullable_field_virtual_return_or_zero,
    nullable_stack_virtual_call_imm,
    nullable_field_select_return,
    byte_field_equal_return_self,
    byte_mask_equal_return_self,
    byte_field_mask_equal_return_self,
    set_four_byte_fields,
    field_virtual_tailcall,
    nullable_pointer_field32_return,
    indexed_field_load_stack4_scale8,
    nested_indexed_byte_load_stack4,
    global_virtual_call_stack_arg,
    zero_four_fields_return_self,
    set_field_zero_return_field_pointer,
    byte_field_and_stack_byte,
    indexed_field_store_stack8_base_field,
    nullable_outer_field32_inner4_return,
    byte_guarded_store_stack4,
    nullable_field_virtual_tailcall_ret,
    field_virtual_call_push_zeros,
    nullable_field_inner32_return,
    memory_zero_two_fields_stack4,
    global_and_global_bool,
    u32_field_and_stack_bool,
    global_field_eq_one_bool,
    nullable_field_virtual_tailcall_ret32,
    byte_mask_dual_threshold,
    field_init_four_offsets,
    zero_mixed_offsets_return_self,
    import_call_self_stdcall,
    virtual_call_push_imm_forward_edx,
    indexed_field_load_stack4,
    lea_zero_word_return,
    set_u32_zero_and_u8,
    virtual_tailcall,
    unsigned_field_less_than,
    zero_return,
    one_return,
    return_first_stack_arg,
    add_two_stack_args,
    call_indirect_zero,
    byte_field_and_imm8,
    u32_field_nonzero,
    u32_field_not_equal_imm8,
    byte_nonzero,
    byte_pointer_nonzero,
]


def generate(row: dict[str, Any], max_variants: int) -> list[GeneratedCandidate]:
    data = parse_bytes(row)
    c_name = safe_c_name(str(row.get("name") or row.get("entry") or "function"))
    candidates: list[GeneratedCandidate] = []
    for generator in GENERATORS:
        candidates.extend(generator(row, c_name, data))
    return candidates[:max_variants]


def load_strategy(path: Path) -> dict[str, str]:
    return {str(row.get("name")): str(row.get("strategyClass")) for row in iter_jsonl(path)}


def load_retrieval(path: Path) -> dict[str, list[dict[str, Any]]]:
    return {str(row.get("name")): list(row.get("nearestMatchedExamples") or []) for row in iter_jsonl(path)}


def load_matched(paths: list[Path]) -> set[tuple[str, str]]:
    matched: set[tuple[str, str]] = set()
    for path in paths:
        for row in iter_jsonl(path):
            if row.get("status") == "matched" and int(row.get("differences", -1)) == 0:
                matched.add((str(row.get("name")), str(row.get("entry"))))
    return matched


def candidate_id(row: dict[str, Any], candidate: GeneratedCandidate) -> str:
    digest = hashlib.sha256(candidate.source.encode("utf-8")).hexdigest()[:12]
    return safe_dir_name(f"{row.get('entry')}_{row.get('name')}_{candidate.rule}_{candidate.variant}_{digest}")


def attempt_candidate(
    row: dict[str, Any],
    candidate: GeneratedCandidate,
    out_dir: Path,
    *,
    inventory: Path,
    compiler_profiles: list[tuple[str, list[str]]],
    vc_root: Path | None,
    wine: str,
    wineprefix: Path | None,
    timeout: int,
    dry_run: bool,
) -> list[dict[str, Any]]:
    case_dir = out_dir / "cases" / candidate_id(row, candidate)
    case_dir.mkdir(parents=True, exist_ok=True)
    candidate_c = case_dir / "candidate.c"
    candidate_c.write_text(candidate.source, encoding="utf-8")

    base_record = {
        "schema": "mizuchi.source-parity-synthesis-attempt.v1",
        "name": row.get("name"),
        "entry": row.get("entry"),
        "section": row.get("section"),
        "bodyBytes": row.get("bytes") or row.get("bodyBytes"),
        "instructionCount": row.get("instructionCount"),
        "rule": candidate.rule,
        "variant": candidate.variant,
        "symbol": candidate.symbol,
        "callconv": candidate.callconv,
        "returnType": candidate.return_type,
        "source": str(candidate_c),
        "sourceSha256": hashlib.sha256(candidate.source.encode("utf-8")).hexdigest(),
        "outDir": str(case_dir),
        "generationEvidence": candidate.evidence,
        "sourceOrigin": "generated from instruction bytes by source-parity-synthesize.py; not manually authored",
    }
    write_json(case_dir / "generation.json", base_record)
    if dry_run:
        return [
            {
                **base_record,
                "status": "generated-only",
                "differences": -1,
                "attemptDir": str(case_dir),
                "compilerProfileName": "dry-run",
                "compilerProfileArgs": [],
            },
        ]

    slice_proc = run(
        [
            sys.executable,
            "-m",
            "mizuchi_re.swkotor_inventory_slice",
            "--inventory",
            str(inventory),
            "--function",
            str(row.get("name") or row.get("entry")),
            "--symbol",
            candidate.symbol,
            "--out-dir",
            str(case_dir),
        ],
        timeout=timeout,
    )
    (case_dir / "slice.stdout").write_text(slice_proc.stdout, encoding="utf-8")
    (case_dir / "slice.stderr").write_text(slice_proc.stderr, encoding="utf-8")
    if slice_proc.returncode != 0:
        return [
            {
                **base_record,
                "status": "slice-failed",
                "differences": -1,
                "stderr": slice_proc.stderr[-2000:],
                "attemptDir": str(case_dir),
                "compilerProfileName": "slice-failed",
                "compilerProfiles": [name for name, _ in compiler_profiles],
            },
        ]

    attempts: list[dict[str, Any]] = []
    resolved_profiles = resolve_profiles(row, compiler_profiles)
    for profile_index, (profile_name, profile_args) in enumerate(resolved_profiles):
        attempt_dir = case_dir / f"profile_{profile_index:02d}_{safe_dir_name(profile_name)}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        merged_flags = normalize_profile_flags(profile_args, list(candidate.extra_flags))
        object_path = attempt_dir / "candidate.obj"
        compile_result = compile_with_msvc(
            source=candidate_c,
            object_path=object_path,
            out_dir=attempt_dir,
            stem="candidate",
            args=merged_flags,
            timeout=timeout,
            msvc_root=vc_root,
            wine=wine,
            wineprefix=wineprefix,
        )
        compile_stdout = str(compile_result.get("stdout") or "")
        compile_stderr = str(compile_result.get("stderrTail") or compile_result.get("reason") or "")
        (attempt_dir / "compile.stdout").write_text(compile_stdout, encoding="utf-8")
        (attempt_dir / "compile.stderr").write_text(compile_stderr, encoding="utf-8")
        record = {
            **base_record,
            "attemptDir": str(attempt_dir),
            "compilerProfileName": profile_name,
            "compilerProfileArgs": merged_flags,
            "compilerProfiles": [name for name, _ in resolved_profiles],
        }
        if compile_result.get("status") != "ok":
            attempts.append(
                {
                    **record,
                    "status": "compile-failed",
                    "differences": -1,
                    "stderr": compile_stderr[-2000:],
                }
            )
            continue
        report = run_objdiff(case_dir / "target.obj", object_path, attempt_dir, timeout=timeout)
        status = str(report.get("status"))
        differences = int(report.get("differences", -1))
        message = str(report.get("message") or "")
        verify_json = attempt_dir / "verify.json"
        attempts.append(
            {
                **record,
                "status": status,
                "differences": differences,
                "message": message,
                "verifyReport": str(verify_json),
            }
        )
        if status == "matched" and differences == 0:
            break
    return attempts


def run_objdiff(target_obj: Path, candidate_obj: Path, case_dir: Path, *, timeout: int) -> dict[str, Any]:
    raw_path = case_dir / "verify.raw.json"
    report_path = case_dir / "verify.json"
    stdout_path = case_dir / "verify.stdout"
    stderr_path = case_dir / "verify.stderr"
    if not target_obj.exists() or not candidate_obj.exists():
        report = {
            "schema": "mizuchi.verify-objdiff.v1",
            "status": "error",
            "differences": -1,
            "message": "target or candidate object is missing",
            "target": str(target_obj),
            "candidate": str(candidate_obj),
        }
        write_json(report_path, report)
        return report
    proc = run(
        ["objdiff", "diff", "-1", str(target_obj), "-2", str(candidate_obj), "-o", "-", "--format", "json-pretty"],
        timeout=timeout,
    )
    stdout_path.write_text(proc.stdout, encoding="utf-8")
    stderr_path.write_text(proc.stderr, encoding="utf-8")
    raw_path.write_text(proc.stdout if proc.stdout else proc.stderr, encoding="utf-8")
    report = parse_objdiff_report(proc.returncode, proc.stdout or proc.stderr)
    write_json(report_path, report)
    return report


def parse_objdiff_report(returncode: int, output: str) -> dict[str, Any]:
    differences = -1
    if returncode == 0:
        try:
            parsed = json.loads(output) if output.strip() else {}
        except json.JSONDecodeError:
            parsed = {}
        match_percents: list[float] = []
        for item in iter_json_objects(parsed):
            if "match_percent" in item and (item.get("kind") in {"SECTION_CODE", "SYMBOL_FUNCTION"} or "instructions" in item):
                try:
                    match_percents.append(float(item["match_percent"]))
                except (TypeError, ValueError):
                    pass
        if match_percents and all(value == 100 for value in match_percents):
            differences = 0
        elif match_percents:
            differences = 1
        elif not output.strip():
            differences = 0
    status = "matched" if differences == 0 else ("mismatched" if returncode == 0 and differences > 0 else "error")
    message = "Object files match" if status == "matched" else ("Object files do not match" if status == "mismatched" else "objdiff exited with error")
    return {
        "schema": "mizuchi.verify-objdiff.v1",
        "status": status,
        "differences": differences,
        "message": message,
        "objdiffExit": returncode,
        "output": output,
    }


def iter_json_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json_objects(child)


def strategy_allowed(row: dict[str, Any], strategies: set[str] | None, strategy_by_name: dict[str, str]) -> bool:
    if strategies is None:
        return True
    tags = {str(tag) for tag in row.get("tags") or []}
    strategy = strategy_by_name.get(str(row.get("name")), "")
    return bool(strategies & (tags | {strategy}))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--queue", type=Path, default=ROOT / "target/swkotor-recovery-queue/queue.jsonl")
    parser.add_argument("--inventory", type=Path, default=ROOT / "target/swkotor-unpack/facts/function-inventory.jsonl")
    parser.add_argument("--remaining-features", type=Path, default=ROOT / "target/source-parity-index/swkotor/remaining-features.jsonl")
    parser.add_argument("--retrieval", type=Path, default=ROOT / "target/source-parity-index/swkotor/retrieval.jsonl")
    parser.add_argument(
        "--matched-summary",
        type=Path,
        action="append",
        default=[],
    )
    parser.add_argument("--out-dir", type=Path, default=ROOT / "target/source-parity-synthesis/swkotor")
    parser.add_argument("--limit", type=int, default=25, help="Maximum queued functions to inspect.")
    parser.add_argument("--offset", type=int, default=0, help="Eligible queued functions to skip before inspecting.")
    parser.add_argument("--max-variants-per-function", type=int, default=4)
    parser.add_argument("--strategies", help="Comma-separated strategy/tag filter, for example virtual-call-or-thiscall-model,compiler-profile-probe.")
    parser.add_argument("--compiler-profile", action="append", default=[], help="Compiler profile as NAME='/O2 /Oy /GS-'. Repeat for multiple profiles.")
    parser.add_argument("--dry-run", action="store_true", help="Emit generated candidates without compiling or running objdiff.")
    parser.add_argument("--clean", action="store_true", help="Delete the previous synthesis output directory first.")
    parser.add_argument("--vc-root", type=Path, default=DEFAULT_VC_ROOT)
    parser.add_argument("--wine", default="wine")
    parser.add_argument("--wineprefix", type=Path, default=DEFAULT_WINEPREFIX)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--progress-every", type=int, default=0)
    args = parser.parse_args(argv)

    if args.clean and args.out_dir.exists():
        shutil.rmtree(args.out_dir)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    attempts_path = args.out_dir / "attempts.jsonl"
    accepted_path = args.out_dir / "accepted.jsonl"
    clean_jsonl(attempts_path)
    clean_jsonl(accepted_path)

    strategies = None
    if args.strategies:
        strategies = {item.strip() for item in args.strategies.split(",") if item.strip()}
    compiler_profiles = [parse_profile_flag_set(value) for value in args.compiler_profile if value.strip()]
    strategy_by_name = load_strategy(args.remaining_features)
    retrieval_by_name = load_retrieval(args.retrieval)
    matched = load_matched(args.matched_summary)

    skipped = 0
    inspected = 0
    generated = 0
    attempted = 0
    matched_count = 0
    compile_failed = 0
    slice_failed = 0
    unsupported = 0
    mismatched = 0
    errors = 0

    for row in iter_jsonl(args.queue):
        if inspected >= args.limit:
            break
        if (str(row.get("name")), str(row.get("entry"))) in matched:
            continue
        if not strategy_allowed(row, strategies, strategy_by_name):
            continue
        if skipped < args.offset:
            skipped += 1
            continue
        inspected += 1
        candidates = generate(row, args.max_variants_per_function)
        if not candidates:
            unsupported += 1
            record = {
                "schema": "mizuchi.source-parity-synthesis-attempt.v1",
                "name": row.get("name"),
                "entry": row.get("entry"),
                "section": row.get("section"),
                "bodyBytes": row.get("bytes") or row.get("bodyBytes"),
                "instructionCount": row.get("instructionCount"),
                "strategyClass": strategy_by_name.get(str(row.get("name"))),
                "nearestMatchedExamples": retrieval_by_name.get(str(row.get("name")), [])[:3],
                "status": "unsupported-pattern",
                "differences": -1,
                "sourceOrigin": "no source emitted; no byte-pattern generator currently supports this function",
            }
            append_jsonl(attempts_path, record)
            continue
        generated += len(candidates)
        for candidate in candidates:
            records = attempt_candidate(
                row,
                candidate,
                args.out_dir,
                compiler_profiles=compiler_profiles,
                inventory=args.inventory,
                vc_root=args.vc_root,
                wine=args.wine,
                wineprefix=args.wineprefix,
                timeout=args.timeout,
                dry_run=args.dry_run,
            )
            for record in records:
                record["strategyClass"] = strategy_by_name.get(str(row.get("name")))
                record["nearestMatchedExamples"] = retrieval_by_name.get(str(row.get("name")), [])[:3]
                append_jsonl(attempts_path, record)
                attempted += 0 if args.dry_run else 1
                status = record.get("status")
                differences = int(record.get("differences", -1))
                if status == "matched" and differences == 0:
                    matched_count += 1
                    append_jsonl(accepted_path, record)
                elif status == "compile-failed":
                    compile_failed += 1
                elif status == "slice-failed":
                    slice_failed += 1
                elif status == "mismatched":
                    mismatched += 1
                elif status not in {"generated-only"}:
                    errors += 1
            if args.progress_every and generated and generated % args.progress_every == 0:
                print(
                    f"source-parity-synthesize: inspected={inspected} generated={generated} matched={matched_count}",
                    file=sys.stderr,
                    flush=True,
                )

    summary = {
        "schema": "mizuchi.source-parity-synthesis-summary.v1",
        "status": "generated-only" if args.dry_run else "complete",
        "queue": str(args.queue),
        "inventory": str(args.inventory),
        "remainingFeatures": str(args.remaining_features),
        "retrieval": str(args.retrieval),
        "outDir": str(args.out_dir),
        "attemptsPath": str(attempts_path),
        "acceptedPath": str(accepted_path),
        "limit": args.limit,
        "offset": args.offset,
        "skippedEligibleFunctions": skipped,
        "inspectedFunctions": inspected,
        "unsupportedFunctions": unsupported,
        "generatedCandidates": generated,
        "attemptedCandidates": attempted,
        "acceptedCandidates": matched_count,
        "mismatchedCandidates": mismatched,
        "compileFailedCandidates": compile_failed,
        "sliceFailedCandidates": slice_failed,
        "errorCandidates": errors,
        "compilerProfiles": [name for name, _ in compiler_profiles] if compiler_profiles else [name for name, _ in DEFAULT_PROFILES],
        "dryRun": args.dry_run,
        "strategies": sorted(strategies) if strategies else None,
        "claimBoundary": "candidate source is generated automatically from binary-derived features; accepted source requires objdiff zero",
    }
    write_json(args.out_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
