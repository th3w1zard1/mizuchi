#!/usr/bin/env python3
"""Match trivial unpacked swkotor functions with high-level C candidates."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VC_ROOT = Path("/run/media/brunner56/MyBook/ReconstructKitSource/toolchains/msvc8.0-main")
DEFAULT_WINEPREFIX = ROOT / "target/toolchain-acquire/vctoolkit2003/wineprefix"


def run(args: list[str], *, env: dict[str, str] | None = None, timeout: int = 90) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, cwd=ROOT, env=env, text=True, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        message = f"timed out after {timeout} seconds"
        return subprocess.CompletedProcess(args, 124, stdout, f"{stderr}\n{message}".strip())


def iter_inventory(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


@dataclass(frozen=True)
class Candidate:
    symbol: str
    source: str
    kind: str
    extra_flags: tuple[str, ...] = field(default_factory=tuple)


def stack_args(ret_bytes: int) -> str:
    count = ret_bytes // 4
    return ", ".join(f"int a{i}" for i in range(count)) or "void"


def fastcall_symbol(name: str, arg_bytes: int) -> str:
    return f"@{name}@{arg_bytes}"


def signed_disp8(value: int) -> int:
    return value - 0x100 if value >= 0x80 else value


def offset_expr(base: str, offset: int) -> str:
    if offset < 0:
        return f"{base} - 0x{-offset:x}"
    return f"{base} + 0x{offset:x}"


def self_offset_expr(offset: int) -> str:
    return offset_expr("(char *)self", offset)


def field_getter_source(name: str, return_type: str, load_type: str, offset: int) -> str:
    return "\n".join(
        [
            f"{return_type} __fastcall {name}(void *self) {{",
            f"    return *({load_type} *)({self_offset_expr(offset)});",
            "}",
            "",
        ]
    )


def field_add_source(name: str, offset: int, value: int) -> str:
    return "\n".join(
        [
            f"unsigned int __fastcall {name}(void *self) {{",
            f"    return *(unsigned int *)({self_offset_expr(offset)}) + 0x{value:x}u;",
            "}",
            "",
        ]
    )


def field_u8_mask_source(name: str, offset: int, mask: int) -> str:
    return "\n".join(
        [
            f"unsigned int __fastcall {name}(void *self) {{",
            "    unsigned int value = 0;",
            f"    *(unsigned char *)&value = *(unsigned char *)({self_offset_expr(offset)});",
            f"    return value & 0x{mask:x}u;",
            "}",
            "",
        ]
    )


def u64_field_getter_source(name: str, offset: int) -> str:
    return "\n".join(
        [
            f"unsigned __int64 __fastcall {name}(void *self) {{",
            f"    return *(unsigned __int64 *)({self_offset_expr(offset)});",
            "}",
            "",
        ]
    )


def global_getter_source(name: str, c_type: str, address: int, *, stdcall_ret: int = 0) -> str:
    conv = "__stdcall " if stdcall_ret else ""
    args = stack_args(stdcall_ret)
    return "\n".join(
        [
            f"{c_type} {conv}{name}({args}) {{",
            f"    return *({c_type} volatile *)0x{address:08x};",
            "}",
            "",
        ]
    )


def field_pointer_source(name: str, offset: int) -> str:
    return "\n".join(
        [
            f"void *__fastcall {name}(void *self) {{",
            f"    return {self_offset_expr(offset)};",
            "}",
            "",
        ]
    )


def field_setter_source(name: str, c_type: str, offset: int, value: int) -> str:
    suffix = "u" if c_type == "unsigned int" else ""
    width = 8 if c_type == "unsigned int" else 2
    return "\n".join(
        [
            f"void __fastcall {name}(void *self) {{",
            f"    *({c_type} *)({self_offset_expr(offset)}) = 0x{value:0{width}x}{suffix};",
            "}",
            "",
        ]
    )


def field_or_source(name: str, offset: int, mask: int) -> str:
    return "\n".join(
        [
            f"void __fastcall {name}(void *self) {{",
            f"    *(unsigned int *)({self_offset_expr(offset)}) |= 0x{mask:x}u;",
            "}",
            "",
        ]
    )


def field_and_source(name: str, offset: int, mask: int) -> str:
    return "\n".join(
        [
            f"void __fastcall {name}(void *self) {{",
            f"    *(unsigned int *)({self_offset_expr(offset)}) &= 0x{mask:08x}u;",
            "}",
            "",
        ]
    )


def byte_nonzero_source(name: str, offset: int) -> str:
    return "\n".join(
        [
            f"int __fastcall {name}(void *self) {{",
            f"    unsigned char value = *(unsigned char *)({self_offset_expr(offset)});",
            "    return value != 0;",
            "}",
            "",
        ]
    )


def u32_nonzero_source(name: str, offset: int) -> str:
    return "\n".join(
        [
            f"int __fastcall {name}(void *self) {{",
            f"    unsigned int value = *(unsigned int *)({self_offset_expr(offset)});",
            "    return value != 0;",
            "}",
            "",
        ]
    )


def byte_not_equal_source(name: str, offset: int, value: int, mask: int | None = None) -> str:
    expr = f"(*(unsigned char *)({self_offset_expr(offset)})"
    if mask is not None:
        expr = f"({expr} & 0x{mask:x}u)"
    else:
        expr = f"{expr})"
    return "\n".join(
        [
            f"int __fastcall {name}(void *self) {{",
            f"    return {expr} != 0x{value:02x}u;",
            "}",
            "",
        ]
    )


def nested_field_getter_source(name: str, first_offset: int, second_offset: int) -> str:
    return "\n".join(
        [
            f"unsigned int __fastcall {name}(void *self) {{",
            f"    void *p = *(void **)({self_offset_expr(first_offset)});",
            f"    return p ? *(unsigned int *)({offset_expr('(char *)p', second_offset)}) : 0;",
            "}",
            "",
        ]
    )


def pair_field_getter_source(name: str, first_offset: int, second_offset: int) -> str:
    return "\n".join(
        [
            f"unsigned int __fastcall {name}(void *self) {{",
            f"    void *p = *(void **)({self_offset_expr(first_offset)});",
            f"    return *(unsigned int *)({offset_expr('(char *)p', second_offset)});",
            "}",
            "",
        ]
    )


def pair_field_getter_typed_source(name: str, return_type: str, load_type: str, first_offset: int, second_offset: int) -> str:
    return "\n".join(
        [
            f"{return_type} __fastcall {name}(void *self) {{",
            f"    void *p = *(void **)({self_offset_expr(first_offset)});",
            f"    return *({load_type} *)({offset_expr('(char *)p', second_offset)});",
            "}",
            "",
        ]
    )


def nested_field_set_u8_zero_source(name: str, first_offset: int, second_offset: int) -> str:
    return "\n".join(
        [
            f"void __fastcall {name}(void *self) {{",
            f"    void *p = *(void **)({self_offset_expr(first_offset)});",
            f"    *(unsigned char *)({offset_expr('(char *)p', second_offset)}) = 0;",
            "}",
            "",
        ]
    )


def nested_field_set_u32_zero_source(name: str, first_offset: int, second_offset: int) -> str:
    return "\n".join(
        [
            f"void __fastcall {name}(void *self) {{",
            f"    void *p = *(void **)({self_offset_expr(first_offset)});",
            f"    *(unsigned int *)({offset_expr('(char *)p', second_offset)}) = 0;",
            "}",
            "",
        ]
    )


def nested_store_stack_u32_source(name: str, first_offset: int, second_offset: int) -> str:
    return "\n".join(
        [
            f"void __fastcall {name}(void *self, int unused, unsigned int value) {{",
            f"    void *p = *(void **)({self_offset_expr(first_offset)});",
            f"    *(unsigned int *)({offset_expr('(char *)p', second_offset)}) = value;",
            "}",
            "",
        ]
    )


def nested_store_stack_u8_source(name: str, first_offset: int, second_offset: int) -> str:
    return "\n".join(
        [
            f"void __fastcall {name}(void *self, int unused, unsigned char value) {{",
            f"    void *p = *(void **)({self_offset_expr(first_offset)});",
            f"    *(unsigned char *)({offset_expr('(char *)p', second_offset)}) = value;",
            "}",
            "",
        ]
    )


def field_array_getter_source(name: str, pointer_offset: int) -> str:
    return "\n".join(
        [
            f"void *__fastcall {name}(void *self, int unused, unsigned int index) {{",
            f"    void **items = *(void ***)({self_offset_expr(pointer_offset)});",
            "    return items[index];",
            "}",
            "",
        ]
    )


def vtable_tailcall_source(name: str, field_offset: int, method_offset: int) -> str:
    slot = method_offset // 4
    return "\n".join(
        [
            "typedef void (__fastcall *ReconstructKitVMethod)(void *);",
            "",
            f"void __fastcall {name}(void *self) {{",
            f"    void *target = *(void **)({self_offset_expr(field_offset)});",
            f"    (*(ReconstructKitVMethod **)target)[0][{slot}](target);",
            "}",
            "",
        ]
    )


def vtable_call_push_imm_source(name: str, method_offset: int, value: int) -> str:
    slot = method_offset // 4
    return "\n".join(
        [
            "typedef void (__fastcall *ReconstructKitVMethodArg)(void *, int, int);",
            "",
            f"void __fastcall {name}(void *self) {{",
            f"    (*(ReconstructKitVMethodArg **)self)[0][{slot}](self, 0, {value});",
            "}",
            "",
        ]
    )


def fastcall_indexed_ptr_source(name: str) -> str:
    return "\n".join(
        [
            f"void *__fastcall {name}(void *self, int unused, unsigned int index) {{",
            "    return (char *)*(void **)self + index * 4;",
            "}",
            "",
        ]
    )


def fastcall_self_source(name: str) -> str:
    return "\n".join(
        [
            f"void *__fastcall {name}(void *self) {{",
            "    return self;",
            "}",
            "",
        ]
    )


def fastcall_pointer_with_stack_arg_source(name: str, offset: int) -> str:
    return "\n".join(
        [
            f"void *__fastcall {name}(void *self, int unused, int a0) {{",
            f"    return {self_offset_expr(offset)};",
            "}",
            "",
        ]
    )


def fastcall_u16_getter_with_stack_arg_source(name: str, offset: int) -> str:
    return "\n".join(
        [
            f"unsigned short __fastcall {name}(void *self, int unused, int a0) {{",
            f"    return *(unsigned short *)({self_offset_expr(offset)});",
            "}",
            "",
        ]
    )


def fastcall_store_one_stack_arg_u16_source(name: str, offset: int) -> str:
    return "\n".join(
        [
            f"void __fastcall {name}(void *self, int unused, unsigned short value) {{",
            f"    *(unsigned short *)({self_offset_expr(offset)}) = value;",
            "}",
            "",
        ]
    )


def fastcall_store_one_stack_arg_source(name: str, offset: int) -> str:
    return "\n".join(
        [
            f"void __fastcall {name}(void *self, int unused, unsigned int value) {{",
            f"    *(unsigned int *)({self_offset_expr(offset)}) = value;",
            "}",
            "",
        ]
    )


def fastcall_store_one_stack_arg_zero_source(name: str) -> str:
    return "\n".join(
        [
            f"void __fastcall {name}(void *self, int unused, unsigned int value) {{",
            "    *(unsigned int *)self = value;",
            "}",
            "",
        ]
    )


def fastcall_store_one_stack_arg_u32_source(name: str, offset: int) -> str:
    return "\n".join(
        [
            f"void __fastcall {name}(void *self, int unused, unsigned int value) {{",
            f"    *(unsigned int *)({self_offset_expr(offset)}) = value;",
            "}",
            "",
        ]
    )


def fastcall_store_one_stack_arg_u8_source(name: str, offset: int) -> str:
    return "\n".join(
        [
            f"void __fastcall {name}(void *self, int unused, unsigned char value) {{",
            f"    *(unsigned char *)({self_offset_expr(offset)}) = value;",
            "}",
            "",
        ]
    )


def fastcall_store_two_stack_args_source(name: str, first_offset: int, second_offset: int) -> str:
    return "\n".join(
        [
            f"void __fastcall {name}(void *self, int unused, unsigned int a0, unsigned int a1) {{",
            f"    *(unsigned int *)({self_offset_expr(first_offset)}) = a0;",
            f"    *(unsigned int *)({self_offset_expr(second_offset)}) = a1;",
            "}",
            "",
        ]
    )


def fastcall_store_pair_from_pointer_source(name: str) -> str:
    return "\n".join(
        [
            f"void __fastcall {name}(void *self, int unused, unsigned int *value) {{",
            "    *(unsigned int *)self = value[0];",
            "    *(unsigned int *)((char *)self + 4) = value[1];",
            "}",
            "",
        ]
    )


def global_setter_source(name: str, c_type: str, address: int, value: int) -> str:
    suffix = "u" if c_type == "unsigned int" else ""
    width = 8 if c_type == "unsigned int" else 2
    return "\n".join(
        [
            f"void {name}(void) {{",
            f"    *({c_type} volatile *)0x{address:08x} = 0x{value:0{width}x}{suffix};",
            "}",
            "",
        ]
    )


def global_inc_source(name: str, address: int) -> str:
    return "\n".join(
        [
            f"void {name}(void) {{",
            f"    ++*(unsigned int *)0x{address:08x};",
            "}",
            "",
        ]
    )


def global_setter_two_u32_source(name: str, first_address: int, second_address: int, value: int) -> str:
    return "\n".join(
        [
            f"unsigned int {name}(void) {{",
            f"    *(unsigned int volatile *)0x{first_address:08x} = 0x{value:08x}u;",
            f"    *(unsigned int volatile *)0x{second_address:08x} = 0x{value:08x}u;",
            f"    return 0x{value:08x}u;",
            "}",
            "",
        ]
    )


def global_param_store_source(name: str, address: int, stack_offset: int) -> str:
    arg_index = (stack_offset - 4) // 4
    args = ", ".join(f"unsigned int a{i}" for i in range(arg_index + 1))
    return "\n".join(
        [
            f"void {name}({args}) {{",
            f"    *(unsigned int volatile *)0x{address:08x} = a{arg_index};",
            "}",
            "",
        ]
    )


def import_call_ecx_source(name: str, address: int) -> str:
    return "\n".join(
        [
            "typedef void (__cdecl *ReconstructKitImportOneArg)(void *);",
            "",
            f"void __fastcall {name}(void *self) {{",
            f"    (*(ReconstructKitImportOneArg volatile *)0x{address:08x})(self);",
            "}",
            "",
        ]
    )


def import_call_imm_source(name: str, address: int, value: int) -> str:
    return "\n".join(
        [
            "typedef void (__cdecl *ReconstructKitImportIntArg)(int);",
            "",
            f"void {name}(void) {{",
            f"    (*(ReconstructKitImportIntArg volatile *)0x{address:08x})({value});",
            "}",
            "",
        ]
    )


def import_call_global_source(name: str, call_address: int, arg_address: int) -> str:
    return "\n".join(
        [
            "typedef void (__cdecl *ReconstructKitImportPtrArg)(void *);",
            "",
            f"void {name}(void) {{",
            f"    (*(ReconstructKitImportPtrArg volatile *)0x{call_address:08x})(*(void * volatile *)0x{arg_address:08x});",
            "}",
            "",
        ]
    )


def copy_field_return_source(name: str, source_offset: int, target_offset: int) -> str:
    return "\n".join(
        [
            f"unsigned int __fastcall {name}(void *self) {{",
            f"    unsigned int value = *(unsigned int *)({self_offset_expr(source_offset)});",
            f"    *(unsigned int *)({self_offset_expr(target_offset)}) = value;",
            "    return value;",
            "}",
            "",
        ]
    )


def inc_field_stackarg_source(name: str, offset: int) -> str:
    return "\n".join(
        [
            f"unsigned int __fastcall {name}(void *self, int unused, int a0) {{",
            f"    unsigned int value = *(unsigned int *)({self_offset_expr(offset)});",
            "    ++value;",
            f"    *(unsigned int *)({self_offset_expr(offset)}) = value;",
            "    return value;",
            "}",
            "",
        ]
    )


def add_two_fields_source(name: str, first_offset: int, second_offset: int) -> str:
    return "\n".join(
        [
            f"unsigned int __fastcall {name}(void *self) {{",
            f"    return *(unsigned int *)({self_offset_expr(first_offset)}) + *(unsigned int *)({self_offset_expr(second_offset)});",
            "}",
            "",
        ]
    )


def signed_greater_or_equal_fields_source(name: str, left_offset: int, right_offset: int, *, stdcall_ret: int = 0) -> str:
    conv = "__fastcall"
    args = "void *self" if stdcall_ret == 0 else "void *self, int unused, int a0"
    return "\n".join(
        [
            f"int {conv} {name}({args}) {{",
            f"    return *(int *)({self_offset_expr(left_offset)}) >= *(int *)({self_offset_expr(right_offset)});",
            "}",
            "",
        ]
    )


def clear_two_fields_return_zero_source(name: str, first_offset: int, second_offset: int) -> str:
    return "\n".join(
        [
            f"unsigned int __fastcall {name}(void *self) {{",
            f"    *(unsigned int *)({self_offset_expr(first_offset)}) = 0;",
            f"    *(unsigned int *)({self_offset_expr(second_offset)}) = 0;",
            "    return 0;",
            "}",
            "",
        ]
    )


def candidate_for(row: dict) -> Candidate | None:
    name = str(row["name"])
    data = bytes.fromhex(row["bytes"])

    if data == b"\xc3":
        return Candidate(f"_{name}", f"void {name}(void) {{\n}}\n", "empty-return")

    if len(data) == 3 and data[0] == 0xC2 and data[2] == 0x00:
        ret = data[1]
        if ret % 4 == 0:
            return Candidate(
                f"_{name}@{ret}",
                f"void __stdcall {name}({stack_args(ret)}) {{\n}}\n",
                "empty-return-stdcall",
            )

    if data == b"\x33\xc0\xc3":
        return Candidate(f"_{name}", f"int {name}(void) {{\n    return 0;\n}}\n", "return-zero-cdecl")

    if len(data) == 3 and data[0] == 0xB0 and data[2] == 0xC3:
        return Candidate(
            f"_{name}",
            f"unsigned char {name}(void) {{\n    return 0x{data[1]:02x};\n}}\n",
            "return-constant-u8-cdecl",
        )

    if data == b"\x32\xc0\xc3":
        return Candidate(
            f"_{name}",
            f"unsigned char {name}(void) {{\n    return 0;\n}}\n",
            "return-zero-u8-cdecl",
        )

    if len(data) == 5 and data[:2] == b"\x33\xc0" and data[2] == 0xC2 and data[4] == 0x00:
        ret = data[3]
        if ret % 4 == 0:
            return Candidate(
                f"_{name}@{ret}",
                f"int __stdcall {name}({stack_args(ret)}) {{\n    return 0;\n}}\n",
                "return-zero-stdcall",
            )

    if len(data) == 6 and data[0] == 0xB8 and data[5] == 0xC3:
        imm = int.from_bytes(data[1:5], "little")
        return Candidate(
            f"_{name}",
            f"unsigned int {name}(void) {{\n    return 0x{imm:08x}u;\n}}\n",
            "return-constant-cdecl",
        )

    if len(data) == 8 and data[0] == 0xB8 and data[5] == 0xC2 and data[7] == 0x00:
        imm = int.from_bytes(data[1:5], "little")
        ret = data[6]
        if ret % 4 == 0:
            return Candidate(
                f"_{name}@{ret}",
                f"unsigned int __stdcall {name}({stack_args(ret)}) {{\n    return 0x{imm:08x}u;\n}}\n",
            "return-constant-stdcall",
        )

    if data == b"\x8b\xc1\xc3":
        return Candidate(
            fastcall_symbol(name, 4),
            fastcall_self_source(name),
            "fastcall-self",
        )

    if len(data) == 4 and data[:2] == b"\x8b\x41" and data[3] == 0xC3:
        offset = data[2]
        return Candidate(
            fastcall_symbol(name, 4),
            field_getter_source(name, "unsigned int", "unsigned int", signed_disp8(offset)),
            "fastcall-field-u32-u8",
        )

    if len(data) == 4 and data[:2] == b"\xd9\x41" and data[3] == 0xC3:
        return Candidate(
            fastcall_symbol(name, 4),
            field_getter_source(name, "float", "float", signed_disp8(data[2])),
            "fastcall-field-f32-u8",
        )

    if (
        len(data) == 7
        and data[:2] == b"\x8b\x41"
        and data[3:5] == b"\x8b\x51"
        and data[5] == ((data[2] + 4) & 0xFF)
        and data[6] == 0xC3
    ):
        return Candidate(
            fastcall_symbol(name, 4),
            u64_field_getter_source(name, signed_disp8(data[2])),
            "fastcall-field-u64-u8",
        )

    if len(data) == 6 and data[:3] == b"\x8b\x41\x04" and data[3:5] == b"\x8b\x00" and data[5] == 0xC3:
        return Candidate(
            fastcall_symbol(name, 4),
            pair_field_getter_source(name, 4, 0),
            "fastcall-nested-field-u32-root-zero",
        )

    if len(data) == 10 and data[:3] == b"\x8b\x41\x04" and data[3:5] == b"\x8a\x80" and data[9] == 0xC3:
        return Candidate(
            fastcall_symbol(name, 4),
            pair_field_getter_typed_source(name, "unsigned char", "unsigned char", 4, int.from_bytes(data[5:9], "little")),
            "fastcall-nested-field-u8-root-u32",
        )

    if len(data) == 11 and data[:3] == b"\x8b\x41\x04" and data[3:6] == b"\x66\x8b\x80" and data[10] == 0xC3:
        return Candidate(
            fastcall_symbol(name, 4),
            pair_field_getter_typed_source(name, "unsigned short", "unsigned short", 4, int.from_bytes(data[6:10], "little")),
            "fastcall-nested-field-u16-root-u32",
        )

    if (
        len(data) == 14
        and data[:2] == b"\x8b\x41"
        and data[3:7] == b"\x85\xc0\x74\x04"
        and data[7:9] == b"\x8b\x40"
        and data[10:] == b"\xc3\x33\xc0\xc3"
    ):
        return Candidate(
            fastcall_symbol(name, 4),
            nested_field_getter_source(name, signed_disp8(data[2]), signed_disp8(data[9])),
            "fastcall-nested-field-u32-null-u8",
        )

    if len(data) == 5 and data[:2] == b"\x8b\x01" and data[2:4] == b"\x8b\x00" and data[4] == 0xC3:
        return Candidate(
            fastcall_symbol(name, 4),
            pair_field_getter_source(name, 0, 0),
            "fastcall-nested-field-u32-root-zero",
        )

    if len(data) == 6 and data[:2] == b"\x8b\x01" and data[2:4] == b"\x8b\x40" and data[5] == 0xC3:
        return Candidate(
            fastcall_symbol(name, 4),
            pair_field_getter_source(name, 0, signed_disp8(data[4])),
            "fastcall-nested-field-u32-root-u8",
        )

    if len(data) == 7 and data[:2] == b"\x8b\x41" and data[3:5] == b"\x8b\x40" and data[6] == 0xC3:
        return Candidate(
            fastcall_symbol(name, 4),
            pair_field_getter_source(name, signed_disp8(data[2]), signed_disp8(data[5])),
            "fastcall-nested-field-u32-u8",
        )

    if len(data) == 10 and data[:2] == b"\x8b\x41" and data[3:5] == b"\x8b\x80" and data[9] == 0xC3:
        offset = int.from_bytes(data[5:9], "little")
        return Candidate(
            fastcall_symbol(name, 4),
            pair_field_getter_source(name, signed_disp8(data[2]), offset),
            "fastcall-nested-field-u32-u32",
        )

    if len(data) == 13 and data[:2] == b"\x8b\x41" and data[3:5] == b"\x8b\x4c" and data[5:7] == b"\x24\x04" and data[7:10] == b"\x8b\x04\x88" and data[10:] == b"\xc2\x04\x00":
        return Candidate(
            fastcall_symbol(name, 12),
            field_array_getter_source(name, signed_disp8(data[2])),
            "fastcall-field-array-get-u32-index",
        )

    if len(data) == 7 and data[:2] == b"\x8b\x81" and data[6] == 0xC3:
        offset = int.from_bytes(data[2:6], "little")
        return Candidate(
            fastcall_symbol(name, 4),
            field_getter_source(name, "unsigned int", "unsigned int", offset),
            "fastcall-field-u32-u32",
        )

    if len(data) == 7 and data[:2] == b"\x8a\x81" and data[6] == 0xC3:
        offset = int.from_bytes(data[2:6], "little")
        return Candidate(
            fastcall_symbol(name, 4),
            field_getter_source(name, "unsigned char", "unsigned char", offset),
            "fastcall-field-u8-u32",
        )

    if len(data) == 5 and data[:3] == b"\x0f\xb7\x41" and data[4] == 0xC3:
        offset = data[3]
        return Candidate(
            fastcall_symbol(name, 4),
            field_getter_source(name, "unsigned int", "unsigned short", signed_disp8(offset)),
            "fastcall-field-u16-u8",
        )

    if len(data) == 5 and data[:3] == b"\x0f\xbf\x41" and data[4] == 0xC3:
        return Candidate(
            fastcall_symbol(name, 4),
            field_getter_source(name, "int", "short", signed_disp8(data[3])),
            "fastcall-field-s16-u8",
        )

    if len(data) == 5 and data[:3] == b"\x66\x8b\x41" and data[4] == 0xC3:
        return Candidate(
            fastcall_symbol(name, 4),
            field_getter_source(name, "unsigned short", "unsigned short", signed_disp8(data[3])),
            "fastcall-field-u16ret-u8",
        )

    if len(data) == 7 and data[:3] == b"\x66\x8b\x41" and data[4:] == b"\xc2\x04\x00":
        return Candidate(
            fastcall_symbol(name, 12),
            fastcall_u16_getter_with_stack_arg_source(name, signed_disp8(data[3])),
            "fastcall-field-u16ret-u8-stackarg",
        )

    if len(data) == 4 and data[:2] == b"\x8a\x41" and data[3] == 0xC3:
        offset = data[2]
        return Candidate(
            fastcall_symbol(name, 4),
            field_getter_source(name, "unsigned char", "unsigned char", signed_disp8(offset)),
            "fastcall-field-u8-u8",
        )

    if len(data) == 4 and data[:2] == b"\x8d\x41" and data[3] == 0xC3:
        offset = data[2]
        return Candidate(
            fastcall_symbol(name, 4),
            field_pointer_source(name, signed_disp8(offset)),
            "fastcall-field-pointer-u8",
        )

    if len(data) == 7 and data[:2] == b"\x8d\x81" and data[6] == 0xC3:
        offset = int.from_bytes(data[2:6], "little")
        return Candidate(
            fastcall_symbol(name, 4),
            field_pointer_source(name, offset),
            "fastcall-field-pointer-u32",
        )

    if len(data) == 9 and data[:2] == b"\x8d\x81" and data[6:] == b"\xc2\x04\x00":
        offset = int.from_bytes(data[2:6], "little")
        return Candidate(
            fastcall_symbol(name, 12),
            fastcall_pointer_with_stack_arg_source(name, offset),
            "fastcall-field-pointer-u32-stackarg",
        )

    if len(data) == 14 and data[:3] == b"\x8b\x41\x04" and data[3:5] == b"\xc7\x80" and data[9:13] == b"\x00\x00\x00\x00" and data[13] == 0xC3:
        return Candidate(
            fastcall_symbol(name, 4),
            nested_field_set_u32_zero_source(name, 4, int.from_bytes(data[5:9], "little")),
            "fastcall-nested-field-set-u32-zero",
        )

    if len(data) == 11 and data[:3] == b"\x8b\x41\x04" and data[3:5] == b"\xc6\x80" and data[9] == 0x00 and data[10] == 0xC3:
        return Candidate(
            fastcall_symbol(name, 4),
            nested_field_set_u8_zero_source(name, 4, int.from_bytes(data[5:9], "little")),
            "fastcall-nested-field-set-u8-zero",
        )

    if len(data) == 16 and data[:3] == b"\x8b\x41\x04" and data[3:7] == b"\x8b\x4c\x24\x04" and data[7:9] == b"\x89\x88" and data[13:] == b"\xc2\x04\x00":
        return Candidate(
            fastcall_symbol(name, 12),
            nested_store_stack_u32_source(name, 4, int.from_bytes(data[9:13], "little")),
            "fastcall-nested-store-stack-u32",
        )

    if len(data) == 16 and data[:3] == b"\x8b\x41\x04" and data[3:7] == b"\x8a\x4c\x24\x04" and data[7:9] == b"\x88\x88" and data[13:] == b"\xc2\x04\x00":
        return Candidate(
            fastcall_symbol(name, 12),
            nested_store_stack_u8_source(name, 4, int.from_bytes(data[9:13], "little")),
            "fastcall-nested-store-stack-u8",
        )

    if data == b"\x8b\x01\x8b\x4c\x24\x04\x8d\x04\x88\xc2\x04\x00":
        return Candidate(
            fastcall_symbol(name, 12),
            fastcall_indexed_ptr_source(name),
            "fastcall-indexed-ptr-scale4",
        )

    if len(data) == 7 and data[:2] == b"\xc7\x01" and data[6] == 0xC3:
        value = int.from_bytes(data[2:6], "little")
        return Candidate(
            fastcall_symbol(name, 4),
            field_setter_source(name, "unsigned int", 0, value),
            "fastcall-field-set-u32-zero",
        )

    if len(data) == 8 and data[:2] == b"\xc7\x41" and data[7] == 0xC3:
        offset = data[2]
        value = int.from_bytes(data[3:7], "little")
        return Candidate(
            fastcall_symbol(name, 4),
            field_setter_source(name, "unsigned int", signed_disp8(offset), value),
            "fastcall-field-set-u32-u8",
        )

    if len(data) == 11 and data[:2] == b"\xc7\x81" and data[10] == 0xC3:
        offset = int.from_bytes(data[2:6], "little")
        value = int.from_bytes(data[6:10], "little")
        return Candidate(
            fastcall_symbol(name, 4),
            field_setter_source(name, "unsigned int", offset, value),
            "fastcall-field-set-u32-u32",
        )

    if len(data) == 13 and data[:4] == b"\x8b\x44\x24\x04" and data[4:6] == b"\x89\x81" and data[10:] == b"\xc2\x04\x00":
        return Candidate(
            fastcall_symbol(name, 12),
            fastcall_store_one_stack_arg_u32_source(name, int.from_bytes(data[6:10], "little")),
            "fastcall-store-one-stack-u32-u32",
        )

    if len(data) == 9 and data[:4] == b"\x8a\x44\x24\x04" and data[4:6] == b"\x88\x81" and data[8:] == b"\xc2":
        # Kept separate from the longer ret form below; this guard is unreachable for valid ret imm16.
        pass

    if len(data) == 13 and data[:4] == b"\x8a\x44\x24\x04" and data[4:6] == b"\x88\x81" and data[10:] == b"\xc2\x04\x00":
        return Candidate(
            fastcall_symbol(name, 12),
            fastcall_store_one_stack_arg_u8_source(name, int.from_bytes(data[6:10], "little")),
            "fastcall-store-one-stack-u8-u32",
        )

    if len(data) == 16 and data[:5] == b"\x66\x0f\xb6\x44\x24" and data[5] == 0x04 and data[6:8] == b"\x66\x89\x81"[:2]:
        return None

    if len(data) == 16 and data[:5] == b"\x66\x0f\xb7\x44\x24" and data[5] == 0x04 and data[6:9] == b"\x66\x89\x81" and data[13:] == b"\xc2\x04\x00":
        return Candidate(
            fastcall_symbol(name, 12),
            fastcall_store_one_stack_arg_u16_source(name, int.from_bytes(data[9:13], "little")),
            "fastcall-store-one-stack-u16-u32",
        )

    if len(data) == 9 and data[:2] == b"\x33\xc0" and data[2:4] == b"\x89\x41" and data[5:7] == b"\x89\x41" and data[8] == 0xC3:
        return Candidate(
            fastcall_symbol(name, 4),
            clear_two_fields_return_zero_source(name, signed_disp8(data[4]), signed_disp8(data[7])),
            "fastcall-clear-two-u32-return-zero-u8",
        )

    if len(data) == 13 and data[:2] == b"\x8b\x81" and data[6:8] == b"\x03\x81" and data[12] == 0xC3:
        return Candidate(
            fastcall_symbol(name, 4),
            add_two_fields_source(name, int.from_bytes(data[2:6], "little"), int.from_bytes(data[8:12], "little")),
            "fastcall-add-two-fields-u32",
        )

    if len(data) == 5 and data[:2] == b"\xc6\x41" and data[4] == 0xC3:
        offset = data[2]
        value = data[3]
        return Candidate(
            fastcall_symbol(name, 4),
            field_setter_source(name, "unsigned char", signed_disp8(offset), value),
            "fastcall-field-set-u8-u8",
        )

    if len(data) == 8 and data[:2] == b"\xc6\x81" and data[7] == 0xC3:
        offset = int.from_bytes(data[2:6], "little")
        value = data[6]
        return Candidate(
            fastcall_symbol(name, 4),
            field_setter_source(name, "unsigned char", offset, value),
            "fastcall-field-set-u8-u32",
        )

    if len(data) == 5 and data[:2] == b"\x83\x49" and data[4] == 0xC3:
        offset = data[2]
        mask = data[3]
        return Candidate(
            fastcall_symbol(name, 4),
            field_or_source(name, signed_disp8(offset), mask),
            "fastcall-field-or-u8-imm8",
        )

    if len(data) == 8 and data[:2] == b"\x83\x89" and data[7] == 0xC3:
        offset = int.from_bytes(data[2:6], "little")
        mask = data[6]
        return Candidate(
            fastcall_symbol(name, 4),
            field_or_source(name, offset, mask),
            "fastcall-field-or-u32-imm8",
        )

    if len(data) == 8 and data[:2] == b"\x81\x49" and data[7] == 0xC3:
        offset = data[2]
        mask = int.from_bytes(data[3:7], "little")
        return Candidate(
            fastcall_symbol(name, 4),
            field_or_source(name, signed_disp8(offset), mask),
            "fastcall-field-or-u8-imm32",
        )

    if len(data) == 11 and data[:2] == b"\x81\x89" and data[10] == 0xC3:
        offset = int.from_bytes(data[2:6], "little")
        mask = int.from_bytes(data[6:10], "little")
        return Candidate(
            fastcall_symbol(name, 4),
            field_or_source(name, offset, mask),
            "fastcall-field-or-u32-imm32",
        )

    if len(data) == 5 and data[:2] == b"\x83\x61" and data[4] == 0xC3:
        offset = signed_disp8(data[2])
        imm = data[3]
        mask = imm | 0xFFFFFF00 if imm & 0x80 else imm
        return Candidate(
            fastcall_symbol(name, 4),
            field_and_source(name, offset, mask),
            "fastcall-field-and-u8-imm8",
        )

    if len(data) == 7 and data[:2] == b"\x8b\x41" and data[3:5] == b"\x89\x41" and data[6] == 0xC3:
        return Candidate(
            fastcall_symbol(name, 4),
            copy_field_return_source(name, signed_disp8(data[2]), signed_disp8(data[5])),
            "fastcall-copy-field-return-u8",
        )

    if len(data) == 7 and data[:2] == b"\x8b\x41" and data[3:5] == b"\x83\xc0" and data[6] == 0xC3:
        return Candidate(
            fastcall_symbol(name, 4),
            field_add_source(name, signed_disp8(data[2]), data[5]),
            "fastcall-field-add-u8-imm8",
        )

    if len(data) == 9 and data[:2] == b"\x8b\x41" and data[3] == 0x05 and data[8] == 0xC3:
        return Candidate(
            fastcall_symbol(name, 4),
            field_add_source(name, signed_disp8(data[2]), int.from_bytes(data[4:8], "little")),
            "fastcall-field-add-u8-imm32",
        )

    if len(data) == 6 and data[0] == 0xA1 and data[5] == 0xC3:
        address = int.from_bytes(data[1:5], "little")
        return Candidate(
            f"_{name}",
            global_getter_source(name, "unsigned int", address),
            "global-getter-u32-cdecl",
        )

    if len(data) == 8 and data[0] == 0xA1 and data[5] == 0xC2 and data[7] == 0x00:
        address = int.from_bytes(data[1:5], "little")
        ret = data[6]
        if ret % 4 == 0:
            return Candidate(
                f"_{name}@{ret}",
                global_getter_source(name, "unsigned int", address, stdcall_ret=ret),
                "global-getter-u32-stdcall",
            )

    if len(data) == 6 and data[0] == 0xA0 and data[5] == 0xC3:
        address = int.from_bytes(data[1:5], "little")
        return Candidate(
            f"_{name}",
            global_getter_source(name, "unsigned char", address),
            "global-getter-u8-cdecl",
        )

    if len(data) == 8 and data[:2] == b"\xc6\x05" and data[7] == 0xC3:
        address = int.from_bytes(data[2:6], "little")
        value = data[6]
        return Candidate(
            f"_{name}",
            global_setter_source(name, "unsigned char", address, value),
            "global-setter-u8-cdecl",
        )

    if len(data) == 11 and data[:2] == b"\xc7\x05" and data[10] == 0xC3:
        address = int.from_bytes(data[2:6], "little")
        value = int.from_bytes(data[6:10], "little")
        return Candidate(
            f"_{name}",
            global_setter_source(name, "unsigned int", address, value),
            "global-setter-u32-cdecl",
        )

    if len(data) == 16 and data[0] == 0xB8 and data[5] == 0xA3 and data[10] == 0xA3 and data[15] == 0xC3:
        value = int.from_bytes(data[1:5], "little")
        first_address = int.from_bytes(data[6:10], "little")
        second_address = int.from_bytes(data[11:15], "little")
        return Candidate(
            f"_{name}",
            global_setter_two_u32_source(name, first_address, second_address, value),
            "global-setter-two-u32-cdecl",
        )

    if len(data) == 13 and data[:2] == b"\x33\xc0" and data[2] == 0xA3 and data[7] == 0xA3 and data[12] == 0xC3:
        first_address = int.from_bytes(data[3:7], "little")
        second_address = int.from_bytes(data[8:12], "little")
        return Candidate(
            f"_{name}",
            global_setter_two_u32_source(name, first_address, second_address, 0),
            "global-setter-two-u32-cdecl",
        )

    if len(data) == 10 and data[:3] == b"\x8b\x44\x24" and data[4] == 0xA3 and data[9] == 0xC3:
        stack_offset = data[3]
        if stack_offset >= 4 and stack_offset % 4 == 0:
            address = int.from_bytes(data[5:9], "little")
            return Candidate(
                f"_{name}",
                global_param_store_source(name, address, stack_offset),
                "global-param-store-u32-cdecl",
            )

    if len(data) == 10 and data[:4] == b"\x8b\x44\x24\x04" and data[4:6] == b"\x89\x41" and data[7:] == b"\xc2\x04\x00":
        return Candidate(
            fastcall_symbol(name, 12),
            fastcall_store_one_stack_arg_source(name, signed_disp8(data[6])),
            "fastcall-store-one-stack-u32-u8",
        )

    if len(data) == 9 and data[:4] == b"\x8b\x44\x24\x04" and data[4:6] == b"\x89\x01" and data[6:] == b"\xc2\x04\x00":
        return Candidate(
            fastcall_symbol(name, 12),
            fastcall_store_one_stack_arg_zero_source(name),
            "fastcall-store-one-stack-u32-zero",
        )

    if data == b"\x8b\x44\x24\x04\x8b\x10\x89\x11\x8b\x40\x04\x89\x41\x04\xc2\x04\x00":
        return Candidate(
            fastcall_symbol(name, 12),
            fastcall_store_pair_from_pointer_source(name),
            "fastcall-store-pair-from-pointer",
        )

    if (
        len(data) == 17
        and data[:8] == b"\x8b\x44\x24\x04\x8b\x54\x24\x08"
        and data[8:10] == b"\x89\x41"
        and data[11:13] == b"\x89\x51"
        and data[14:] == b"\xc2\x08\x00"
    ):
        return Candidate(
            fastcall_symbol(name, 16),
            fastcall_store_two_stack_args_source(name, signed_disp8(data[10]), signed_disp8(data[13])),
            "fastcall-store-two-u32-u8",
        )

    if (
        len(data) == 23
        and data[:8] == b"\x8b\x44\x24\x04\x8b\x54\x24\x08"
        and data[8:10] == b"\x89\x81"
        and data[14:16] == b"\x89\x91"
        and data[20:] == b"\xc2\x08\x00"
    ):
        first_offset = int.from_bytes(data[10:14], "little")
        second_offset = int.from_bytes(data[16:20], "little")
        return Candidate(
            fastcall_symbol(name, 16),
            fastcall_store_two_stack_args_source(name, first_offset, second_offset),
            "fastcall-store-two-u32-u32",
        )

    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=ROOT / "target/swkotor-unpack/facts/function-inventory.jsonl")
    parser.add_argument("--limit", type=int, default=25, help="Max candidates to attempt; 0 means no limit.")
    parser.add_argument("--out", type=Path, default=ROOT / "target/swkotor-trivial-matches/summary.jsonl")
    parser.add_argument("--summary", type=Path, default=ROOT / "target/swkotor-trivial-matches/summary.json")
    parser.add_argument("--text-section", default=".textV", help="Inventory section to match (e.g. .textV, .textU).")
    parser.add_argument("--match-root", type=Path, default=ROOT / "target/swkotor-match")
    parser.add_argument("--vc-root", type=Path, default=DEFAULT_VC_ROOT)
    parser.add_argument("--wineprefix", type=Path, default=DEFAULT_WINEPREFIX)
    parser.add_argument("--progress-every", type=int, default=0, help="Print attempted/matched progress to stderr every N attempted candidates.")
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    matched = 0
    attempted = 0
    rows = []
    with args.out.open("w", encoding="utf-8") as summary:
        for row in iter_inventory(args.inventory):
            if row.get("section") != args.text_section:
                continue
            candidate = candidate_for(row)
            if candidate is None:
                continue
            symbol, source = candidate.symbol, candidate.source
            name = str(row["name"])
            out_dir = args.match_root / name
            out_dir.mkdir(parents=True, exist_ok=True)

            slice_proc = run(
                [
                    str(ROOT / "scripts/swkotor-inventory-slice.py"),
                    "--inventory",
                    str(args.inventory),
                    "--function",
                    name,
                    "--symbol",
                    symbol,
                    "--out-dir",
                    str(out_dir),
                ]
            )
            if slice_proc.returncode != 0:
                record = {
                    "schema": "reconkit.swkotor-trivial-match.v1",
                    "name": name,
                    "entry": row.get("entry"),
                    "section": row.get("section"),
                    "bodyBytes": row.get("bodyBytes"),
                    "instructionCount": row.get("instructionCount"),
                    "symbol": symbol,
                    "kind": candidate.kind,
                    "status": "slice-failed",
                    "differences": -1,
                    "stderr": slice_proc.stderr[-2000:],
                    "outDir": str(out_dir),
                }
                rows.append(record)
                summary.write(json.dumps(record, sort_keys=True) + "\n")
                continue

            candidate_c = out_dir / "candidate.c"
            candidate_obj = out_dir / "candidate.obj"
            candidate_c.write_text(source, encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "VC_ROOT": str(args.vc_root),
                    "WINEPREFIX": str(args.wineprefix),
                    "CL_OPT": "/O2",
                }
            )
            compile_proc = run(
                [
                    "bash",
                    str(ROOT / "scripts/cl-compile.sh"),
                    str(candidate_c),
                    str(candidate_obj),
                    "/GS-",
                    "/Oy",
                    *candidate.extra_flags,
                ],
                env=env,
            )
            (out_dir / "compile.stdout").write_text(compile_proc.stdout, encoding="utf-8")
            (out_dir / "compile.stderr").write_text(compile_proc.stderr, encoding="utf-8")
            if compile_proc.returncode != 0:
                record = {
                    "schema": "reconkit.swkotor-trivial-match.v1",
                    "name": name,
                    "entry": row.get("entry"),
                    "section": row.get("section"),
                    "bodyBytes": row.get("bodyBytes"),
                    "instructionCount": row.get("instructionCount"),
                    "symbol": symbol,
                    "kind": candidate.kind,
                    "status": "compile-failed",
                    "differences": -1,
                    "stderr": compile_proc.stderr[-2000:],
                    "outDir": str(out_dir),
                }
                rows.append(record)
                summary.write(json.dumps(record, sort_keys=True) + "\n")
                attempted += 1
                if args.progress_every and attempted % args.progress_every == 0:
                    print(f"swkotor-match-trivial: attempted={attempted} matched={matched}", file=sys.stderr, flush=True)
                continue

            verify_proc = run(
                [
                    "bash",
                    str(ROOT / "scripts/lib/verify-objdiff.sh"),
                    str(out_dir / "target.obj"),
                    str(candidate_obj),
                    "--out",
                    str(out_dir / "verify.json"),
                    "--raw-out",
                    str(out_dir / "verify.raw.json"),
                ]
            )
            (out_dir / "verify.stdout").write_text(verify_proc.stdout, encoding="utf-8")
            (out_dir / "verify.stderr").write_text(verify_proc.stderr, encoding="utf-8")
            status = "error"
            differences = -1
            if (out_dir / "verify.json").exists():
                report = json.loads((out_dir / "verify.json").read_text(encoding="utf-8"))
                status = str(report.get("status"))
                differences = int(report.get("differences", -1))
            if status == "matched" and differences == 0:
                matched += 1
            attempted += 1
            record = {
                "schema": "reconkit.swkotor-trivial-match.v1",
                "name": name,
                "entry": row.get("entry"),
                "section": row.get("section"),
                "bodyBytes": row.get("bodyBytes"),
                "instructionCount": row.get("instructionCount"),
                "symbol": symbol,
                "kind": candidate.kind,
                "status": status,
                "differences": differences,
                "outDir": str(out_dir),
            }
            rows.append(record)
            summary.write(json.dumps(record, sort_keys=True) + "\n")
            if args.progress_every and attempted % args.progress_every == 0:
                print(f"swkotor-match-trivial: attempted={attempted} matched={matched}", file=sys.stderr, flush=True)
            if args.limit and attempted >= args.limit:
                break

    by_kind = []
    for kind in sorted({str(row.get("kind")) for row in rows}):
        group = [row for row in rows if str(row.get("kind")) == kind]
        by_kind.append(
            {
                "kind": kind,
                "count": len(group),
                "matched": sum(1 for row in group if row.get("status") == "matched" and row.get("differences") == 0),
            }
        )
    rollup = {
        "schema": "reconkit.swkotor-simple-matches-summary.v1",
        "inventory": str(args.inventory),
        "summaryJsonl": str(args.out),
        "attempted": len(rows),
        "matched": sum(1 for row in rows if row.get("status") == "matched" and row.get("differences") == 0),
        "mismatched": sum(1 for row in rows if row.get("status") != "matched" or row.get("differences") != 0),
        "byKind": by_kind,
        "matchedFunctions": [
            row["name"]
            for row in rows
            if row.get("status") == "matched" and row.get("differences") == 0
        ],
    }
    args.summary.write_text(json.dumps(rollup, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "attempted": attempted,
                "matched": matched,
                "summary": str(args.out),
                "rollup": str(args.summary),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
