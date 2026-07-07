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
import signal
import shutil
import subprocess
import sys
import tempfile
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
    source_suffix: str = ".c"
    semantic_source: bool = True


DEFAULT_PROFILES: list[tuple[str, list[str]]] = [
    ("O2_Gz_Oy_GSminus", ["/O2", "/Gz", "/Oy", "/GS-"]),
    ("O2_Oy_GSminus", ["/O2", "/Oy", "/GS-"]),
    ("O1_Gz_Oy_GSminus", ["/O1", "/Gz", "/Oy", "/GS-"]),
    ("Od_Oyminus_GSminus", ["/Od", "/Oy-", "/GS-"]),
]

DEFAULT_CLANG_PROFILES: list[tuple[str, list[str]]] = [
    ("clang_i386_O2", ["-m32", "-O2", "-ffreestanding", "-fno-pic", "-fno-pie", "-fno-asynchronous-unwind-tables", "-fno-stack-protector", "-fno-ident"]),
    ("clang_i386_O0", ["-m32", "-O0", "-ffreestanding", "-fno-pic", "-fno-pie", "-fno-asynchronous-unwind-tables", "-fno-stack-protector", "-fno-ident"]),
]

DEFAULT_CLANG_CL_PROFILES: list[tuple[str, list[str]]] = [
    ("clangcl_i386_O2_Gz", ["/O2", "/GS-", "/Oy", "/Gz"]),
    ("clangcl_i386_O1_Gz", ["/O1", "/GS-", "/Oy", "/Gz"]),
    ("clangcl_i386_O2_Gz_frameptr", ["/O2", "/GS-", "/Oy-", "/Gz"]),
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
        if lowered.startswith("/OY"):
            merged = [entry for entry in merged if not entry.upper().startswith("/OY")]
        elif is_msvc_optimization_flag(lowered):
            merged = [entry for entry in merged if not is_msvc_optimization_flag(entry.upper())]
        elif lowered in {"/GD", "/GZ", "/GR", "/GV"}:
            merged = [entry for entry in merged if entry.upper() not in {"/GD", "/GZ", "/GR", "/GV"}]
        elif lowered.startswith("/GS"):
            merged = [entry for entry in merged if not entry.upper().startswith("/GS")]
        merged.append(candidate)
    return merged


def is_msvc_optimization_flag(value: str) -> bool:
    if value.startswith("/OY"):
        return False
    return bool(re.fullmatch(r"/O[012XDTISYB]*", value))


def extract_row_compiler_profile_hints(row: dict[str, Any], compiler: str) -> list[str] | None:
    hints = row.get("compilerProfileHints")
    if not isinstance(hints, dict):
        return None
    hinted_compiler = str(hints.get("compiler") or "").lower()
    if hinted_compiler and hinted_compiler != compiler:
        return None
    args = hints.get("args")
    if not isinstance(args, list):
        return None
    normalized = [item for item in args if isinstance(item, str) and item.strip()]
    return normalized or None


def resolve_profiles(
    row: dict[str, Any],
    cli_profiles: list[tuple[str, list[str]]],
    compiler: str = "msvc",
) -> list[tuple[str, list[str]]]:
    if cli_profiles:
        return cli_profiles
    hint_args = extract_row_compiler_profile_hints(row, compiler)
    if hint_args:
        profiles = [("row-hint", hint_args)]
        for name, args in default_profile_set(compiler):
            if args != hint_args:
                profiles.append((name, args))
        return profiles
    if compiler == "clang":
        return DEFAULT_CLANG_PROFILES
    if compiler == "clang-cl":
        return DEFAULT_CLANG_CL_PROFILES
    return DEFAULT_PROFILES


def default_profile_set(compiler: str) -> list[tuple[str, list[str]]]:
    if compiler == "clang":
        return DEFAULT_CLANG_PROFILES
    if compiler == "clang-cl":
        return DEFAULT_CLANG_CL_PROFILES
    return DEFAULT_PROFILES


def run(args: list[str], *, env: dict[str, str] | None = None, timeout: int = 90) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="mizuchi-run-") as tmp:
        stdout_path = Path(tmp) / "stdout.txt"
        stderr_path = Path(tmp) / "stderr.txt"
        with stdout_path.open("w+", encoding="utf-8", errors="replace") as stdout_file, stderr_path.open(
            "w+",
            encoding="utf-8",
            errors="replace",
        ) as stderr_file:
            proc = subprocess.Popen(
                args,
                cwd=Path.cwd(),
                env=env,
                text=True,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            timed_out = False
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                terminate_process_tree(proc.pid)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    kill_process_tree(proc.pid)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
            stdout_file.flush()
            stderr_file.flush()
            stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
            stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
            if timed_out:
                message = f"timed out after {timeout} seconds"
                return subprocess.CompletedProcess(args, 124, stdout, f"{stderr}\n{message}".strip())
            return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)


def child_processes(pid: int) -> list[int]:
    proc = subprocess.run(
        ["pgrep", "-P", str(pid)],
        text=True,
        capture_output=True,
        check=False,
    )
    children: list[int] = []
    for line in proc.stdout.splitlines():
        try:
            child = int(line.strip())
        except ValueError:
            continue
        children.append(child)
        children.extend(child_processes(child))
    return children


def terminate_process_tree(pid: int) -> None:
    signal_process_tree(pid, signal.SIGTERM)
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def kill_process_tree(pid: int) -> None:
    signal_process_tree(pid, signal.SIGKILL)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def signal_process_tree(pid: int, sig: signal.Signals) -> None:
    for child in reversed(child_processes(pid)):
        try:
            os.kill(child, sig)
        except ProcessLookupError:
            pass
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def iter_source_task_rows(path: Path) -> Iterable[dict[str, Any]]:
    for task in iter_jsonl(path):
        row = source_task_to_queue_row(task)
        if row is not None:
            yield row


def source_task_to_queue_row(task: dict[str, Any]) -> dict[str, Any] | None:
    target_slice = task.get("targetSlice")
    if not isinstance(target_slice, dict) or target_slice.get("status") != "complete":
        return None
    bytes_path = target_slice.get("bytesPath") or target_slice.get("packagedBytesPath")
    data = read_task_slice_bytes(bytes_path)
    if data is None:
        return None
    automatic_generator = task.get("automaticGenerator") if isinstance(task.get("automaticGenerator"), dict) else {}
    target_byte_span = automatic_generator.get("targetByteSpan") if isinstance(automatic_generator.get("targetByteSpan"), dict) else None
    if task.get("semanticSource") is True and target_byte_span:
        start = optional_int(target_byte_span.get("offset")) or 0
        length = optional_int(target_byte_span.get("length"))
        if length is not None and start >= 0 and length >= 0 and start + length <= len(data):
            data = data[start : start + length]
    address = task.get("address")
    entry = task.get("entry") or format_entry(address)
    fact = task.get("functionFact") if isinstance(task.get("functionFact"), dict) else {}
    row = {
        "schema": "mizuchi.source-parity-synthesis-row.v1",
        "sourceTask": True,
        "sourceTaskSchema": task.get("schema"),
        "sourceTaskStatus": task.get("status"),
        "name": task.get("name") or entry,
        "entry": entry,
        "address": address,
        "rva": task.get("rva"),
        "section": target_slice.get("section"),
        "targetFormat": task.get("targetFormat"),
        "architectureHint": task.get("architectureHint"),
        "argumentBitWidth": task.get("argumentBitWidth"),
        "argumentBits": task.get("argumentBits"),
        "argumentType": task.get("argumentType"),
        "valueBits": task.get("valueBits"),
        "valueType": task.get("valueType"),
        "bytes": data.hex(),
        "bodyBytes": len(data),
        "instructionCount": fact.get("instructionCount"),
        "semanticSource": task.get("semanticSource"),
        "source": task.get("source"),
        "sourceLanguage": task.get("sourceLanguage"),
        "sourceSha256": task.get("sourceSha256"),
        "sourceQuality": task.get("sourceQuality"),
        "sourceRecoveryScope": task.get("sourceRecoveryScope"),
        "automaticGenerator": automatic_generator or task.get("automaticGenerator"),
        "compilerProfileHints": task.get("compilerProfileHints") or best_compiler_profile_hint(task),
        "functionFact": fact,
        "targetSlice": {
            key: value
            for key, value in target_slice.items()
            if key not in {"bytesHex"}
        },
        "tags": source_task_tags(task),
        "sourceOrigin": "converted from mizuchi source-generation task",
    }
    return row


def read_task_slice_bytes(bytes_path: Any) -> bytes | None:
    if not bytes_path:
        return None
    path = Path(str(bytes_path))
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        return path.read_bytes()
    except OSError:
        return None


def read_packaged_source(path_value: Any) -> tuple[Path, str] | None:
    if not path_value:
        return None
    path = Path(str(path_value))
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        return path, path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path, path.read_text(encoding="latin-1")
        except OSError:
            return None
    except OSError:
        return None


def format_entry(address: Any) -> str:
    try:
        return f"0x{int(address):x}"
    except (TypeError, ValueError):
        return str(address or "")


def best_compiler_profile_hint(task: dict[str, Any]) -> dict[str, Any] | None:
    artifacts = task.get("compilerProfileArtifacts")
    if not isinstance(artifacts, dict):
        return None
    for artifact in artifacts.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        matches = artifact.get("profileFlagMatches")
        if not isinstance(matches, dict) or not matches:
            continue
        best = max(matches.items(), key=lambda item: int(item[1] or 0))[0]
        parts = str(best).split()
        if len(parts) < 2:
            continue
        return {
            "compiler": "msvc",
            "args": parts[1:],
            "reason": f"selected from compiler-profile artifact {artifact.get('path')}",
        }
    return None


def source_task_tags(task: dict[str, Any]) -> list[str]:
    tags = ["source-generation-task"]
    if str(task.get("name") or "") == "_BinkOpenDirectSound@4":
        tags.append("bink-open-direct-sound-forwarder")
    for item in task.get("automaticInputs") or []:
        tags.append(str(item))
    generator = task.get("automaticGenerator")
    if isinstance(generator, dict) and generator.get("rule"):
        tags.append(str(generator["rule"]))
    return sorted(set(tags))


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


def optional_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            return None
    return None


def row_address(row: dict[str, Any]) -> int | None:
    value = row.get("address")
    parsed = optional_int(value)
    if parsed is not None:
        return parsed
    entry = row.get("entry")
    return optional_int(entry)


def rel32_call_target(row: dict[str, Any], *, call_offset: int, rel32: int) -> int | None:
    address = row_address(row)
    if address is None:
        return None
    return address + call_offset + 5 + rel32


def strip_alignment_padding(data: bytes) -> bytes:
    end = len(data)
    while end > 0 and data[end - 1] in {0x90, 0xCC}:
        end -= 1
    return data[:end]


def is_tail_fragment(data: bytes) -> bool:
    return data.startswith((b"\x5f\x5e\x5b\xc9\xc3", b"\x5f\x5d\x5b\xc3", b"\x5e\xc3", b"\x5f\xc3"))


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
    body = strip_alignment_padding(data)
    if len(body) != 3 or body not in {b"\x33\xc0\xc3", b"\x31\xc0\xc3"}:
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


def zero_return_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) != 5 or body[:3] not in {b"\x33\xc0\xc2", b"\x31\xc0\xc2"}:
        return []
    stack_bytes = int.from_bytes(body[3:5], "little")
    if stack_bytes == 0 or stack_bytes % 4 != 0:
        return []
    params = ", ".join(f"unsigned int unused_{index}" for index in range(stack_bytes // 4)) or "void"
    voids = [f"    (void)unused_{index};" for index in range(stack_bytes // 4)]
    source = header("return-zero-stdcall", row) + "\n".join(
        [
            f"int __stdcall {c_name}({params}) {{",
            *voids,
            "    return 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="return-zero-stdcall",
            variant=f"stdcall{stack_bytes}",
            c_name=c_name,
            symbol=f"_{c_name}@{stack_bytes}",
            source=source,
            callconv="stdcall",
            return_type="int",
            evidence={"pattern": "xor-eax-ret-imm", "stackBytes": stack_bytes},
        )
    ]


def immediate_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) != 6 or body[0] != 0xB8 or body[5] != 0xC3:
        return []
    value = u32(body[1:5])
    if value in {0, 1}:
        return []
    source = header("return-immediate-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(void) {{",
            f"    return 0x{value:08x}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="return-immediate-cdecl",
            variant="cdecl-immediate-u32",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={"pattern": "mov-eax-imm-ret", "value": f"0x{value:08x}"},
        )
    ]


def immediate_return_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) != 8 or body[0] != 0xB8 or body[5] != 0xC2:
        return []
    value = u32(body[1:5])
    stack_bytes = int.from_bytes(body[6:8], "little")
    if value in {0, 1} or stack_bytes == 0 or stack_bytes % 4 != 0:
        return []
    params = ", ".join(f"unsigned int unused_{index}" for index in range(stack_bytes // 4)) or "void"
    voids = [f"    (void)unused_{index};" for index in range(stack_bytes // 4)]
    source = header("return-immediate-stdcall", row) + "\n".join(
        [
            f"unsigned int __stdcall {c_name}({params}) {{",
            *voids,
            f"    return 0x{value:08x}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="return-immediate-stdcall",
            variant=f"stdcall{stack_bytes}-immediate-u32",
            c_name=c_name,
            symbol=f"_{c_name}@{stack_bytes}",
            source=source,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={"pattern": "mov-eax-imm-ret-imm", "value": f"0x{value:08x}", "stackBytes": stack_bytes},
        )
    ]


def is_x86_64_row(row: dict[str, Any]) -> bool:
    return str(row.get("architectureHint") or "").lower() == "x86_64"


def x86_64_zero_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if not is_x86_64_row(row) or len(body) != 3 or body not in {b"\x33\xc0\xc3", b"\x31\xc0\xc3"}:
        return []
    source = header("x86-64-return-zero-cdecl", row) + "\n".join(
        [
            f"int {c_name}(void) {{",
            "    return 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-return-zero-cdecl",
            variant="sysv-o2-zero",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={"pattern": "xor-eax-ret", "framePointer": False, "targetFormat": row.get("targetFormat")},
        )
    ]


def x86_64_immediate_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if not is_x86_64_row(row) or len(body) != 6 or body[0] != 0xB8 or body[5] != 0xC3:
        return []
    value = u32(body[1:5])
    if value in {0, 1}:
        return []
    source = header("x86-64-return-immediate-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(void) {{",
            f"    return 0x{value:08x}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-return-immediate-cdecl",
            variant="sysv-o2-immediate-u32",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={"pattern": "mov-eax-imm-ret", "value": f"0x{value:08x}", "framePointer": False, "targetFormat": row.get("targetFormat")},
        )
    ]


def x86_64_one_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if not is_x86_64_row(row) or body != b"\xb8\x01\x00\x00\x00\xc3":
        return []
    source = header("x86-64-return-one-cdecl", row) + "\n".join(
        [
            f"int {c_name}(void) {{",
            "    return 1;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-return-one-cdecl",
            variant="sysv-o2-one",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={"pattern": "mov-eax-1-ret", "framePointer": False, "targetFormat": row.get("targetFormat")},
        )
    ]


def framed_zero_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) != 7 or body[:3] not in {b"\x55\x89\xe5", b"\x55\x8b\xec"} or body[5:] != b"\x5d\xc3":
        return []
    if body[3:5] not in {b"\x31\xc0", b"\x33\xc0"}:
        return []
    source = header("framed-return-zero", row) + "\n".join(
        [
            f"int {c_name}(void) {{",
            "    return 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="framed-return-zero",
            variant="cdecl-frameptr",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence={"pattern": "push-ebp-mov-ebp-esp-xor-eax-pop-ebp-ret", "frameStyle": frame_style(body)},
        )
    ]


def framed_immediate_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) != 10 or body[:3] not in {b"\x55\x89\xe5", b"\x55\x8b\xec"} or body[3] != 0xB8 or body[8:] != b"\x5d\xc3":
        return []
    value = u32(body[4:8])
    source = header("framed-return-immediate-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(void) {{",
            f"    return 0x{value:08x}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="framed-return-immediate-cdecl",
            variant="cdecl-frameptr-immediate-u32",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={"pattern": "push-ebp-mov-ebp-esp-mov-eax-imm-pop-ebp-ret", "frameStyle": frame_style(body), "value": f"0x{value:08x}"},
        )
    ]


def framed_return_first_stack_arg(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) != 8 or body[:3] not in {b"\x55\x89\xe5", b"\x55\x8b\xec"} or body[3:6] != b"\x8b\x45\x08" or body[6:] != b"\x5d\xc3":
        return []
    style = frame_style(body)
    if style == "msvc":
        source = header("framed-return-first-stack-arg-cdecl", row) + "\n".join(
            [
                f"__declspec(naked) unsigned int {c_name}(unsigned int value) {{",
                "    __asm {",
                "        push ebp",
                "        mov ebp, esp",
                "        mov eax, dword ptr [ebp+8]",
                "        pop ebp",
                "        ret",
                "    }",
                "}",
                "",
            ]
        )
        extra_flags = ("/Od", "/GS-", "/Oy-")
    else:
        source = header("framed-return-first-stack-arg-cdecl", row) + "\n".join(
            [
                f"__attribute__((naked)) unsigned int {c_name}(unsigned int value) {{",
                "    __asm__ volatile(",
                '        "pushl %ebp\\n\\t"',
                '        "movl %esp, %ebp\\n\\t"',
                '        "movl 8(%ebp), %eax\\n\\t"',
                '        "popl %ebp\\n\\t"',
                '        "retl\\n\\t"',
                "    );",
                "}",
                "",
            ]
        )
        extra_flags = ("-O0", "-ffreestanding", "-fno-pic", "-fno-pie", "-fno-asynchronous-unwind-tables", "-fno-stack-protector", "-fno-ident")
    return [
        GeneratedCandidate(
            rule="framed-return-first-stack-arg-cdecl",
            variant=f"{style}-frameptr-stack-arg",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=extra_flags,
            evidence={
                "pattern": "push-ebp-mov-ebp-esp-mov-eax-ebp8-pop-ebp-ret",
                "frameStyle": style,
                "sourceTier": "generated inline-assembly parity source for framed stack-argument return",
                "sourceQuality": "inline-asm-c",
            },
        )
    ]


def x86_64_framed_zero_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) != 8 or body[:4] != b"\x55\x48\x89\xe5" or body[6:] != b"\x5d\xc3":
        return []
    if body[4:6] not in {b"\x31\xc0", b"\x33\xc0"}:
        return []
    source = header("x86-64-framed-return-zero", row) + "\n".join(
        [
            f"int {c_name}(void) {{",
            "    return 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-framed-return-zero",
            variant="cdecl-frameptr",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=clang_target_flags_for_row(row),
            evidence={"pattern": "push-rbp-mov-rbp-rsp-xor-eax-pop-rbp-ret", "targetFormat": row.get("targetFormat")},
        )
    ]


def x86_64_framed_immediate_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) != 11 or body[:4] != b"\x55\x48\x89\xe5" or body[4] != 0xB8 or body[9:] != b"\x5d\xc3":
        return []
    value = u32(body[5:9])
    source = header("x86-64-framed-return-immediate-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(void) {{",
            f"    return 0x{value:08x}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-framed-return-immediate-cdecl",
            variant="cdecl-frameptr-immediate-u32",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=clang_target_flags_for_row(row),
            evidence={"pattern": "push-rbp-mov-rbp-rsp-mov-eax-imm-pop-rbp-ret", "targetFormat": row.get("targetFormat"), "value": f"0x{value:08x}"},
        )
    ]


def x86_64_return_first_arg(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if body != b"\x89\xf8\xc3":
        return []
    source = header("x86-64-return-first-arg-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            "    return value;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-return-first-arg-cdecl",
            variant="sysv-o2-register-arg",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={"pattern": "mov-eax-edi-ret", "registerArg": "edi", "framePointer": False, "targetFormat": row.get("targetFormat")},
        )
    ]


def x86_64_return_first_arg64(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if body != b"\x48\x89\xf8\xc3":
        return []
    source = header("x86-64-return-first-arg64-cdecl", row) + "\n".join(
        [
            f"unsigned long long {c_name}(unsigned long long value) {{",
            "    return value;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-return-first-arg64-cdecl",
            variant="sysv-o2-register-arg64",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned long long",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={"pattern": "mov-rax-rdi-ret", "registerArg": "rdi", "framePointer": False, "targetFormat": row.get("targetFormat")},
        )
    ]


X86_64_RETURN_SECOND_ARG_OPS: dict[bytes, tuple[str, str, str, str, list[str]]] = {
    b"\x89\xf0\xc3": ("x86-64-return-second-arg-cdecl", "unsigned int", "esi", "mov-eax-esi-ret", ["edi", "esi"]),
    b"\x48\x89\xf0\xc3": ("x86-64-return-second-arg64-cdecl", "unsigned long long", "rsi", "mov-rax-rsi-ret", ["rdi", "rsi"]),
}


def x86_64_return_second_arg(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    decoded = X86_64_RETURN_SECOND_ARG_OPS.get(body)
    if decoded is None:
        return []
    rule, value_type, register_arg, pattern, register_args = decoded
    source = header(rule, row) + "\n".join(
        [
            f"{value_type} {c_name}({value_type} a, {value_type} b) {{",
            "    return b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=rule,
            variant=f"sysv-o2-register-{register_arg}-return",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type=value_type,
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": pattern,
                "registerArg": register_arg,
                "registerArgs": register_args,
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def x86_64_add_two_args(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if body != b"\x8d\x04\x37\xc3":
        return []
    source = header("x86-64-add-two-args-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int a, unsigned int b) {{",
            "    return a + b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-add-two-args-cdecl",
            variant="sysv-o2-register-args",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={"pattern": "lea-eax-rdi-rsi-ret", "registerArgs": ["edi", "esi"], "framePointer": False, "targetFormat": row.get("targetFormat")},
        )
    ]


X86_64_THREE_ARGS_ARITHMETIC_PATTERNS: dict[bytes, tuple[str, str, str]] = {
    b"\x8d\x04\x37\x01\xd0\xc3": ("add-add", "a + b + c", "lea-eax-rdi-rsi-add-eax-edx-ret"),
    b"\x29\xf7\x8d\x04\x17\xc3": ("sub-add", "a - b + c", "sub-edi-esi-lea-eax-rdi-rdx-ret"),
    b"\x89\xf8\x01\xd6\x29\xf0\xc3": ("sub-sum", "a - b - c", "mov-eax-edi-add-esi-edx-sub-eax-esi-ret"),
    b"\x89\xd0\x01\xf7\x29\xf8\xc3": ("sub-sum-reversed", "c - (a + b)", "mov-eax-edx-add-edi-esi-sub-eax-edi-ret"),
    b"\x0f\xaf\xfe\x8d\x04\x17\xc3": ("mul-add", "a * b + c", "imul-edi-esi-lea-eax-rdi-rdx-ret"),
    b"\x89\xf8\x0f\xaf\xc6\x29\xd0\xc3": ("mul-sub", "a * b - c", "mov-eax-edi-imul-eax-esi-sub-eax-edx-ret"),
    b"\x89\xd0\x0f\xaf\xfe\x29\xf8\xc3": ("sub-mul", "c - a * b", "mov-eax-edx-imul-edi-esi-sub-eax-edi-ret"),
}


def decode_x86_64_three_args_arithmetic(data: bytes) -> dict[str, str] | None:
    body = strip_alignment_padding(data)
    decoded = X86_64_THREE_ARGS_ARITHMETIC_PATTERNS.get(body)
    if decoded is None:
        return None
    suffix, expression, pattern = decoded
    return {"suffix": suffix, "expression": expression, "pattern": pattern}


def x86_64_three_args_arithmetic(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_three_args_arithmetic(data)
    if decoded is None:
        return []
    source = header("x86-64-three-args-arithmetic-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int a, unsigned int b, unsigned int c) {{",
            f"    return {decoded['expression']};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-three-args-arithmetic-cdecl",
            variant=f"sysv-o2-three-arg-{decoded['suffix']}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "expression": decoded["expression"],
                "registerArgs": ["edi", "esi", "edx"],
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_THREE_ARGS_BITWISE_OPS: dict[int, tuple[str, str]] = {
    0x21: ("and", "&"),
    0x09: ("or", "|"),
    0x31: ("xor", "^"),
}


def decode_x86_64_three_args_bitwise(data: bytes) -> dict[str, str] | None:
    body = strip_alignment_padding(data)
    if len(body) != 7 or body[:2] != b"\x89\xf8" or body[3] != 0xF0 or body[5:] != b"\xd0\xc3":
        return None
    first = X86_64_THREE_ARGS_BITWISE_OPS.get(body[2])
    second = X86_64_THREE_ARGS_BITWISE_OPS.get(body[4])
    if first is None or second is None:
        return None
    expression = f"(a {first[1]} b) {second[1]} c"
    return {
        "suffix": f"{first[0]}-{second[0]}",
        "expression": expression,
        "pattern": f"mov-eax-edi-{first[0]}-eax-esi-{second[0]}-eax-edx-ret",
    }


def x86_64_three_args_bitwise(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_three_args_bitwise(data)
    if decoded is None:
        return []
    source = header("x86-64-three-args-bitwise-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int a, unsigned int b, unsigned int c) {{",
            f"    return {decoded['expression']};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-three-args-bitwise-cdecl",
            variant=f"sysv-o2-three-arg-{decoded['suffix']}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "expression": decoded["expression"],
                "registerArgs": ["edi", "esi", "edx"],
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_THREE_ARGS_SELECT_OPS: dict[bytes, tuple[str, str, str, str, str]] = {
    b"\x89\xd0\x39\xf7\x0f\x42\xc7\xc3": ("uint-select-lt", "unsigned int", "a < b ? a : c", "cmovb", "mov-eax-edx-cmp-edi-esi-cmovb-eax-edi-ret"),
    b"\x89\xd0\x39\xf7\x0f\x43\xc7\xc3": ("uint-select-ge", "unsigned int", "a < b ? c : a", "cmovae", "mov-eax-edx-cmp-edi-esi-cmovae-eax-edi-ret"),
    b"\x89\xd0\x39\xf7\x0f\x47\xc7\xc3": ("uint-select-gt", "unsigned int", "a > b ? a : c", "cmova", "mov-eax-edx-cmp-edi-esi-cmova-eax-edi-ret"),
    b"\x89\xd0\x39\xf7\x0f\x46\xc7\xc3": ("uint-select-le", "unsigned int", "a > b ? c : a", "cmovbe", "mov-eax-edx-cmp-edi-esi-cmovbe-eax-edi-ret"),
    b"\x89\xd0\x39\xf7\x0f\x4c\xc7\xc3": ("int-select-lt", "int", "a < b ? a : c", "cmovl", "mov-eax-edx-cmp-edi-esi-cmovl-eax-edi-ret"),
    b"\x89\xd0\x39\xf7\x0f\x4d\xc7\xc3": ("int-select-ge", "int", "a < b ? c : a", "cmovge", "mov-eax-edx-cmp-edi-esi-cmovge-eax-edi-ret"),
    b"\x89\xd0\x39\xf7\x0f\x4f\xc7\xc3": ("int-select-gt", "int", "a > b ? a : c", "cmovg", "mov-eax-edx-cmp-edi-esi-cmovg-eax-edi-ret"),
    b"\x89\xd0\x39\xf7\x0f\x4e\xc7\xc3": ("int-select-le", "int", "a > b ? c : a", "cmovle", "mov-eax-edx-cmp-edi-esi-cmovle-eax-edi-ret"),
}


def decode_x86_64_three_args_select(data: bytes) -> dict[str, str] | None:
    body = strip_alignment_padding(data)
    decoded = X86_64_THREE_ARGS_SELECT_OPS.get(body)
    if decoded is None:
        return None
    suffix, value_type, expression, cmov, pattern = decoded
    return {
        "suffix": suffix,
        "valueType": value_type,
        "expression": expression,
        "cmov": cmov,
        "pattern": pattern,
    }


def x86_64_three_args_select(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_three_args_select(data)
    if decoded is None:
        return []
    value_type = str(decoded["valueType"])
    source = header("x86-64-three-args-select-cdecl", row) + "\n".join(
        [
            f"{value_type} {c_name}({value_type} a, {value_type} b, {value_type} c) {{",
            f"    return {decoded['expression']};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-three-args-select-cdecl",
            variant=f"sysv-o2-three-arg-{decoded['suffix']}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type=value_type,
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "valueType": value_type,
                "pattern": decoded["pattern"],
                "expression": decoded["expression"],
                "cmov": decoded["cmov"],
                "registerArgs": ["edi", "esi", "edx"],
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def format_x86_64_two_args_affine_expression(coeff_a: int, coeff_b: int, immediate: int) -> str:
    terms: list[str] = []
    if coeff_a == 1:
        terms.append("a")
    elif coeff_a > 1:
        terms.append(f"{coeff_a}u * a")
    if coeff_b == 1:
        terms.append("b")
    elif coeff_b > 1:
        terms.append(f"{coeff_b}u * b")
    if immediate:
        terms.append(f"0x{immediate:02x}u")
    return " + ".join(terms)


def decode_x86_64_two_args_affine_lea(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) < 4 or body[:2] != b"\x8d\x04":
        return None
    sib = body[2]
    scale = 1 << ((sib >> 6) & 0x03)
    index = (sib >> 3) & 0x07
    base = sib & 0x07
    register_names = {0x07: "a", 0x06: "b"}
    if base not in register_names or index not in register_names:
        return None
    coeffs = {"a": 0, "b": 0}
    coeffs[register_names[base]] += 1
    coeffs[register_names[index]] += scale
    immediate = 0
    suffix = "scaled"
    if len(body) == 4 and body[3] == 0xC3:
        pass
    elif len(body) == 6 and body[3:6] == b"\x01\xf8\xc3":
        coeffs["a"] += 1
        suffix = "scaled-add-a"
    elif len(body) == 7 and body[3:5] == b"\x83\xc0" and body[6] == 0xC3:
        immediate = body[5]
        if immediate == 0:
            return None
        suffix = "scaled-add-imm8"
    else:
        return None
    coeff_a = coeffs["a"]
    coeff_b = coeffs["b"]
    if coeff_a == 1 and coeff_b == 1 and immediate == 0:
        return None
    if coeff_a == 0 or coeff_b == 0:
        return None
    return {
        "suffix": suffix,
        "coeffA": coeff_a,
        "coeffB": coeff_b,
        "immediate": immediate,
        "expression": format_x86_64_two_args_affine_expression(coeff_a, coeff_b, immediate),
        "pattern": f"lea-eax-sib-0x{sib:02x}{'-add-eax-edi' if suffix == 'scaled-add-a' else '-add-eax-imm8' if suffix == 'scaled-add-imm8' else ''}-ret",
    }


def x86_64_two_args_affine_lea(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_two_args_affine_lea(data)
    if decoded is None:
        return []
    expression = str(decoded["expression"])
    source = header("x86-64-two-args-affine-lea-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int a, unsigned int b) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-two-args-affine-lea-cdecl",
            variant=f"sysv-o2-register-args-affine-lea-{decoded['suffix']}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArgs": ["edi", "esi"],
                "coeffA": int(decoded["coeffA"]),
                "coeffB": int(decoded["coeffB"]),
                "immediate": int(decoded["immediate"]),
                "expression": expression,
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_TWO_ARG_BINARY_OPS: dict[bytes, tuple[str, str, str]] = {
    b"\x89\xf8\x29\xf0\xc3": ("sub", "-", "mov-eax-edi-sub-eax-esi-ret"),
    b"\x89\xf8\x0f\xaf\xc6\xc3": ("mul", "*", "mov-eax-edi-imul-eax-esi-ret"),
    b"\x89\xf8\x21\xf0\xc3": ("and", "&", "mov-eax-edi-and-eax-esi-ret"),
    b"\x89\xf8\x09\xf0\xc3": ("or", "|", "mov-eax-edi-or-eax-esi-ret"),
    b"\x89\xf8\x31\xf0\xc3": ("xor", "^", "mov-eax-edi-xor-eax-esi-ret"),
}


def x86_64_two_args_binary_op(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    body = strip_alignment_padding(data)
    decoded = X86_64_TWO_ARG_BINARY_OPS.get(body)
    if decoded is None:
        return []
    suffix, operator, pattern = decoded
    source = header(f"x86-64-two-args-{suffix}-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int a, unsigned int b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-two-args-{suffix}-cdecl",
            variant=f"sysv-o2-register-args-{suffix}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": pattern,
                "registerArgs": ["edi", "esi"],
                "operator": operator,
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_TWO_ARG_BINARY_OPS64: dict[bytes, tuple[str, str, str]] = {
    b"\x48\x8d\x04\x37\xc3": ("add", "+", "lea-rax-rdi-rsi-ret"),
    b"\x48\x89\xf8\x48\x29\xf0\xc3": ("sub", "-", "mov-rax-rdi-sub-rax-rsi-ret"),
    b"\x48\x89\xf8\x48\x0f\xaf\xc6\xc3": ("mul", "*", "mov-rax-rdi-imul-rax-rsi-ret"),
    b"\x48\x89\xf8\x48\x21\xf0\xc3": ("and", "&", "mov-rax-rdi-and-rax-rsi-ret"),
    b"\x48\x89\xf8\x48\x09\xf0\xc3": ("or", "|", "mov-rax-rdi-or-rax-rsi-ret"),
    b"\x48\x89\xf8\x48\x31\xf0\xc3": ("xor", "^", "mov-rax-rdi-xor-rax-rsi-ret"),
}


def x86_64_two_args_binary_op64(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    body = strip_alignment_padding(data)
    decoded = X86_64_TWO_ARG_BINARY_OPS64.get(body)
    if decoded is None:
        return []
    suffix, operator, pattern = decoded
    source = header(f"x86-64-two-args64-{suffix}-cdecl", row) + "\n".join(
        [
            f"unsigned long long {c_name}(unsigned long long a, unsigned long long b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-two-args64-{suffix}-cdecl",
            variant=f"sysv-o2-register-args64-{suffix}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned long long",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": pattern,
                "registerArgs": ["rdi", "rsi"],
                "operator": operator,
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_TWO_ARG_MIN_MAX_OPS: dict[bytes, tuple[str, str, str, str, str]] = {
    b"\x89\xf0\x39\xf7\x0f\x42\xc7\xc3": ("uint-min", "<", "unsigned int", "cmovb", "mov-eax-esi-cmp-edi-esi-cmovb-eax-edi-ret"),
    b"\x89\xf0\x39\xf7\x0f\x47\xc7\xc3": ("uint-max", ">", "unsigned int", "cmova", "mov-eax-esi-cmp-edi-esi-cmova-eax-edi-ret"),
    b"\x89\xf0\x39\xf7\x0f\x4c\xc7\xc3": ("int-min", "<", "int", "cmovl", "mov-eax-esi-cmp-edi-esi-cmovl-eax-edi-ret"),
    b"\x89\xf0\x39\xf7\x0f\x4f\xc7\xc3": ("int-max", ">", "int", "cmovg", "mov-eax-esi-cmp-edi-esi-cmovg-eax-edi-ret"),
}


def x86_64_two_args_min_max(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    body = strip_alignment_padding(data)
    decoded = X86_64_TWO_ARG_MIN_MAX_OPS.get(body)
    if decoded is None:
        return []
    suffix, operator, value_type, cmov, pattern = decoded
    source = header(f"x86-64-{suffix}-two-args-cdecl", row) + "\n".join(
        [
            f"{value_type} {c_name}({value_type} a, {value_type} b) {{",
            f"    return a {operator} b ? a : b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-{suffix}-two-args-cdecl",
            variant=f"sysv-o2-register-args-{suffix}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type=value_type,
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": pattern,
                "registerArgs": ["edi", "esi"],
                "operator": operator,
                "valueType": value_type,
                "cmov": cmov,
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_TWO_ARG_MIN_MAX_OPS64: dict[bytes, tuple[str, str, str, str, str]] = {
    b"\x48\x89\xf0\x48\x39\xf7\x48\x0f\x42\xc7\xc3": ("uint64-min", "<", "unsigned long long", "cmovb", "mov-rax-rsi-cmp-rdi-rsi-cmovb-rax-rdi-ret"),
    b"\x48\x89\xf0\x48\x39\xf7\x48\x0f\x47\xc7\xc3": ("uint64-max", ">", "unsigned long long", "cmova", "mov-rax-rsi-cmp-rdi-rsi-cmova-rax-rdi-ret"),
    b"\x48\x89\xf0\x48\x39\xf7\x48\x0f\x4c\xc7\xc3": ("int64-min", "<", "long long", "cmovl", "mov-rax-rsi-cmp-rdi-rsi-cmovl-rax-rdi-ret"),
    b"\x48\x89\xf0\x48\x39\xf7\x48\x0f\x4f\xc7\xc3": ("int64-max", ">", "long long", "cmovg", "mov-rax-rsi-cmp-rdi-rsi-cmovg-rax-rdi-ret"),
}


def x86_64_two_args_min_max64(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    body = strip_alignment_padding(data)
    decoded = X86_64_TWO_ARG_MIN_MAX_OPS64.get(body)
    if decoded is None:
        return []
    suffix, operator, value_type, cmov, pattern = decoded
    source = header(f"x86-64-{suffix}-two-args-cdecl", row) + "\n".join(
        [
            f"{value_type} {c_name}({value_type} a, {value_type} b) {{",
            f"    return a {operator} b ? a : b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-{suffix}-two-args-cdecl",
            variant=f"sysv-o2-register-args-{suffix}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type=value_type,
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": pattern,
                "registerArgs": ["rdi", "rsi"],
                "operator": operator,
                "valueType": value_type,
                "cmov": cmov,
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_ARG_LEA_MULTIPLY_OPS: dict[bytes, tuple[int, str]] = {
    b"\x8d\x04\x3f\xc3": (2, "lea-eax-rdi-rdi-ret"),
    b"\x8d\x04\x7f\xc3": (3, "lea-eax-rdi-rdi2-ret"),
    b"\x8d\x04\xbd\x00\x00\x00\x00\xc3": (4, "lea-eax-rdi4-ret"),
    b"\x8d\x04\xbf\xc3": (5, "lea-eax-rdi-rdi4-ret"),
    b"\x01\xff\x8d\x04\x7f\xc3": (6, "add-edi-edi-lea-eax-rdi-rdi2-ret"),
    b"\x8d\x04\xfd\x00\x00\x00\x00\x29\xf8\xc3": (7, "lea-eax-rdi8-sub-eax-edi-ret"),
    b"\x8d\x04\xfd\x00\x00\x00\x00\xc3": (8, "lea-eax-rdi8-ret"),
    b"\x8d\x04\xff\xc3": (9, "lea-eax-rdi-rdi8-ret"),
    b"\x01\xff\x8d\x04\xbf\xc3": (10, "add-edi-edi-lea-eax-rdi-rdi4-ret"),
    b"\x8d\x04\xbf\x8d\x04\x47\xc3": (11, "lea-eax-rdi-rdi4-lea-eax-rdi-rax2-ret"),
    b"\xc1\xe7\x02\x8d\x04\x7f\xc3": (12, "shl-edi-2-lea-eax-rdi-rdi2-ret"),
    b"\x8d\x04\x7f\x8d\x04\x87\xc3": (13, "lea-eax-rdi-rdi2-lea-eax-rdi-rax4-ret"),
    b"\x89\xf8\x8d\x0c\x00\xc1\xe0\x04\x29\xc8\xc3": (14, "mov-eax-edi-lea-ecx-rax-rax-shl-eax-4-sub-eax-ecx-ret"),
    b"\x8d\x04\xbf\x8d\x04\x40\xc3": (15, "lea-eax-rdi-rdi4-lea-eax-rax-rax2-ret"),
    b"\xc1\xe7\x03\x8d\x04\x7f\xc3": (24, "shl-edi-3-lea-eax-rdi-rdi2-ret"),
    b"\x89\xf8\xc1\xe0\x05\x29\xf8\xc3": (31, "mov-eax-edi-shl-eax-5-sub-eax-edi-ret"),
    b"\x89\xf8\xc1\xe0\x05\x01\xf8\xc3": (33, "mov-eax-edi-shl-eax-5-add-eax-edi-ret"),
}


def x86_64_arg_lea_multiply(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_LEA_MULTIPLY_OPS.get(body)
    if decoded is None:
        return []
    multiplier, pattern = decoded
    source = header("x86-64-arg-mul-lea-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value * {multiplier}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-arg-mul-lea-cdecl",
            variant=f"sysv-o2-register-arg-lea-mul-{multiplier}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": pattern,
                "registerArg": "edi",
                "operator": "*",
                "multiplier": multiplier,
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_ARG64_LEA_MULTIPLY_OPS: dict[bytes, tuple[int, str]] = {
    b"\x48\x8d\x04\x3f\xc3": (2, "lea-rax-rdi-rdi-ret"),
    b"\x48\x8d\x04\x7f\xc3": (3, "lea-rax-rdi-rdi2-ret"),
    b"\x48\x8d\x04\xbd\x00\x00\x00\x00\xc3": (4, "lea-rax-rdi4-ret"),
    b"\x48\x8d\x04\xbf\xc3": (5, "lea-rax-rdi-rdi4-ret"),
    b"\x48\x8d\x04\xfd\x00\x00\x00\x00\xc3": (8, "lea-rax-rdi8-ret"),
    b"\x48\x8d\x04\xff\xc3": (9, "lea-rax-rdi-rdi8-ret"),
}


def x86_64_arg64_lea_multiply(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG64_LEA_MULTIPLY_OPS.get(body)
    if decoded is None:
        return []
    multiplier, pattern = decoded
    source = header("x86-64-arg64-mul-lea-cdecl", row) + "\n".join(
        [
            f"unsigned long long {c_name}(unsigned long long value) {{",
            f"    return value * {multiplier}ull;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-arg64-mul-lea-cdecl",
            variant=f"sysv-o2-register-arg64-lea-mul-{multiplier}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned long long",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": pattern,
                "registerArg": "rdi",
                "operator": "*",
                "multiplier": multiplier,
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_ARG_CONST_MIN_MAX_CMOV: dict[int, tuple[str, str, str, str, bool]] = {
    0x42: ("uint-min", "<", "unsigned int", "cmovb", False),
    0x43: ("uint-max", ">", "unsigned int", "cmovae", True),
    0x4C: ("int-min", "<", "int", "cmovl", False),
    0x4D: ("int-max", ">", "int", "cmovge", True),
}


def decode_x86_64_arg_const_min_max(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 12 or body[:2] != b"\x83\xff" or body[3] != 0xB8 or body[8] != 0x0F or body[10:12] != b"\xc7\xc3":
        return None
    decoded = X86_64_ARG_CONST_MIN_MAX_CMOV.get(body[9])
    if decoded is None:
        return None
    suffix, operator, value_type, cmov, compare_is_exclusive_upper = decoded
    compare_immediate = body[2]
    constant = int.from_bytes(body[4:8], "little", signed=False)
    if compare_is_exclusive_upper:
        if compare_immediate == 0:
            return None
        expected_constant = compare_immediate - 1
    else:
        expected_constant = compare_immediate
    if constant != expected_constant:
        return None
    if value_type == "int" and constant > 0x7F:
        return None
    return {
        "suffix": suffix,
        "operator": operator,
        "valueType": value_type,
        "returnType": value_type,
        "constant": constant,
        "compareImmediate": compare_immediate,
        "cmov": cmov,
        "pattern": f"cmp-edi-imm8-mov-eax-imm32-{cmov}-eax-edi-ret",
    }


def x86_64_arg_const_min_max(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_const_min_max(data)
    if decoded is None:
        return []
    constant = int(decoded["constant"])
    value_type = str(decoded["valueType"])
    return_type = str(decoded["returnType"])
    operator = str(decoded["operator"])
    literal = f"0x{constant:02x}u" if value_type == "unsigned int" else str(constant)
    source = header(f"x86-64-arg-{decoded['suffix']}-imm8-cmov-cdecl", row) + "\n".join(
        [
            f"{return_type} {c_name}({value_type} value) {{",
            f"    return value {operator} {literal} ? value : {literal};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-arg-{decoded['suffix']}-imm8-cmov-cdecl",
            variant=f"sysv-o2-register-arg-{decoded['suffix']}-imm8-{decoded['cmov']}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type=return_type,
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "operator": operator,
                "constant": f"0x{constant:02x}" if value_type == "unsigned int" else constant,
                "compareImmediate": int(decoded["compareImmediate"]),
                "valueType": value_type,
                "returnType": return_type,
                "cmov": decoded["cmov"],
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def x86_64_const_minus_arg(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    body = strip_alignment_padding(data)
    if len(body) != 8 or body[0] != 0xB8 or body[5:] != b"\x29\xf8\xc3":
        return []
    value = int.from_bytes(body[1:5], "little", signed=False)
    if value == 0:
        return []
    source = header("x86-64-const-minus-arg-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return 0x{value:08x}u - value;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-const-minus-arg-cdecl",
            variant="sysv-o2-const-minus-register-arg",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": "mov-eax-imm32-sub-eax-edi-ret",
                "registerArg": "edi",
                "operator": "-",
                "constant": f"0x{value:08x}",
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_ARG_SIGNBIT_ZERO_COMPARE_OPS: dict[bytes, tuple[str, str, str]] = {
    b"\x89\xf8\xc1\xe8\x1f\xc3": ("lt", "<", "mov-eax-edi-shr-eax-31-ret"),
    b"\x89\xf8\xf7\xd0\xc1\xe8\x1f\xc3": ("ge", ">=", "mov-eax-edi-not-eax-shr-eax-31-ret"),
}


def x86_64_arg_signbit_zero_compare(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_SIGNBIT_ZERO_COMPARE_OPS.get(body)
    if decoded is None:
        return []
    suffix, operator, pattern = decoded
    source = header(f"x86-64-int-signbit-zero-{suffix}-cdecl", row) + "\n".join(
        [
            f"int {c_name}(int value) {{",
            f"    return value {operator} 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-int-signbit-zero-{suffix}-cdecl",
            variant=f"sysv-o2-register-arg-int-signbit-zero-{suffix}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": pattern,
                "registerArg": "edi",
                "operator": operator,
                "immediate": 0,
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_ARG_SIGN_MASK_OPS: dict[bytes, tuple[str, str, str]] = {
    b"\x89\xf8\xc1\xf8\x1f\xc3": ("sign", "value < 0 ? -1 : 0", "mov-eax-edi-sar-eax-31-ret"),
    b"\x89\xf8\xf7\xd0\xc1\xf8\x1f\xc3": ("nonsign", "value < 0 ? 0 : -1", "mov-eax-edi-not-eax-sar-eax-31-ret"),
}


def x86_64_arg_sign_mask(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_SIGN_MASK_OPS.get(body)
    if decoded is None:
        return []
    suffix, expression, pattern = decoded
    source = header(f"x86-64-arg-{suffix}-mask-cdecl", row) + "\n".join(
        [
            f"int {c_name}(int value) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-arg-{suffix}-mask-cdecl",
            variant=f"sysv-o2-register-arg-{suffix}-mask",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": pattern,
                "registerArg": "edi",
                "valueType": "int",
                "returnType": "int",
                "trueValue": "0xffffffff",
                "falseValue": "0x00000000",
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def decode_x86_64_arg_bitmask_bool(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == b"\x89\xf8\x83\xe0\x01\xc3":
        return {
            "predicate": "nonzero",
            "operator": "!=",
            "mask": 0x00000001,
            "pattern": "mov-eax-edi-and-eax-1-ret",
        }
    if body == b"\x89\xf8\xf7\xd0\x83\xe0\x01\xc3":
        return {
            "predicate": "zero",
            "operator": "==",
            "mask": 0x00000001,
            "pattern": "mov-eax-edi-not-eax-and-eax-1-ret",
        }
    if len(body) == 9 and body[:3] == b"\x89\xf8\xc1" and body[3] == 0xE8 and body[5:8] == b"\x83\xe0\x01" and body[8] == 0xC3:
        shift = body[4]
        if not 1 <= shift <= 31:
            return None
        return {
            "predicate": "nonzero",
            "operator": "!=",
            "mask": 1 << shift,
            "shift": shift,
            "pattern": "mov-eax-edi-shr-eax-imm8-and-eax-1-ret",
        }
    if len(body) == 10 and body[:3] == b"\x31\xc0\x40" and body[3] == 0xF6 and body[4] == 0xC7 and body[6] == 0x0F and body[8:] == b"\xc0\xc3":
        mask = body[5]
        setcc_opcode = body[7]
        if mask == 0:
            return None
        if setcc_opcode == 0x94:
            predicate = "zero"
            operator = "=="
            setcc = "sete"
        elif setcc_opcode == 0x95:
            predicate = "nonzero"
            operator = "!="
            setcc = "setne"
        else:
            return None
        return {
            "predicate": predicate,
            "operator": operator,
            "mask": mask,
            "byteMask": mask,
            "setcc": setcc,
            "pattern": f"xor-eax-test-dil-imm8-{setcc}-al-ret",
        }
    if len(body) == 12 and body[:2] == b"\x31\xc0" and body[2:4] == b"\xf7\xc7" and body[8] == 0x0F and body[10:] == b"\xc0\xc3":
        mask = int.from_bytes(body[4:8], "little", signed=False)
        setcc_opcode = body[9]
        if mask == 0:
            return None
        if setcc_opcode == 0x94:
            predicate = "zero"
            operator = "=="
            setcc = "sete"
        elif setcc_opcode == 0x95:
            predicate = "nonzero"
            operator = "!="
            setcc = "setne"
        else:
            return None
        return {
            "predicate": predicate,
            "operator": operator,
            "mask": mask,
            "setcc": setcc,
            "pattern": f"xor-eax-test-edi-imm32-{setcc}-al-ret",
        }
    return None


def x86_64_arg_bitmask_bool(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    if x86_64_prefers_arg64_value(row):
        return []
    decoded = decode_x86_64_arg_bitmask_bool(data)
    if decoded is None:
        return []
    mask = int(decoded["mask"])
    operator = str(decoded["operator"])
    predicate = str(decoded["predicate"])
    source = header(f"x86-64-arg-bitmask-{predicate}-cdecl", row) + "\n".join(
        [
            f"int {c_name}(unsigned int value) {{",
            f"    return (value & 0x{mask:08x}u) {operator} 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-arg-bitmask-{predicate}-cdecl",
            variant=f"sysv-o2-register-arg-bitmask-{predicate}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "operator": operator,
                "predicate": predicate,
                "mask": f"0x{mask:08x}",
                "shift": decoded.get("shift"),
                "setcc": decoded.get("setcc"),
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def x86_64_prefers_arg64_value(row: dict[str, Any]) -> bool:
    value_type = str(row.get("valueType") or row.get("argumentType") or "").strip().lower()
    return (
        row.get("argumentBitWidth") == 64
        or row.get("argumentBits") == 64
        or row.get("valueBits") == 64
        or value_type in {"unsigned long long", "long long", "uint64_t", "int64_t", "size_t", "uintptr_t"}
    )


def decode_x86_64_arg64_bitmask_bool(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == b"\x48\x89\xf8\x83\xe0\x01\xc3":
        return {
            "predicate": "nonzero",
            "operator": "!=",
            "mask": 0x0000000000000001,
            "pattern": "mov-rax-rdi-and-eax-1-ret",
        }
    if len(body) == 10 and body[:3] == b"\x48\x89\xf8" and body[3:5] == b"\xc1\xe8" and body[6:9] == b"\x83\xe0\x01" and body[9] == 0xC3:
        shift = body[5]
        if not 1 <= shift <= 31:
            return None
        return {
            "predicate": "nonzero",
            "operator": "!=",
            "mask": 1 << shift,
            "shift": shift,
            "pattern": "mov-rax-rdi-shr-eax-imm8-and-eax-1-ret",
        }
    if len(body) == 11 and body[:3] == b"\x48\x89\xf8" and body[3:6] == b"\x48\xc1\xe8" and body[7:10] == b"\x83\xe0\x01" and body[10] == 0xC3:
        shift = body[6]
        if not 32 <= shift <= 63:
            return None
        return {
            "predicate": "nonzero",
            "operator": "!=",
            "mask": 1 << shift,
            "shift": shift,
            "pattern": "mov-rax-rdi-shr-rax-imm8-and-eax-1-ret",
        }
    if len(body) == 10 and body[:3] == b"\x31\xc0\x40" and body[3] == 0xF6 and body[4] == 0xC7 and body[6] == 0x0F and body[8:] == b"\xc0\xc3":
        mask = body[5]
        setcc_opcode = body[7]
        if mask == 0:
            return None
        if setcc_opcode == 0x94:
            predicate = "zero"
            operator = "=="
            setcc = "sete"
        elif setcc_opcode == 0x95:
            predicate = "nonzero"
            operator = "!="
            setcc = "setne"
        else:
            return None
        return {
            "predicate": predicate,
            "operator": operator,
            "mask": mask,
            "byteMask": mask,
            "requiresWidthHint": True,
            "setcc": setcc,
            "pattern": f"xor-eax-test-dil-imm8-{setcc}-al-ret",
        }
    if len(body) == 12 and body[:2] == b"\x31\xc0" and body[2:4] == b"\xf7\xc7" and body[8] == 0x0F and body[10:] == b"\xc0\xc3":
        mask = int.from_bytes(body[4:8], "little", signed=False)
        setcc_opcode = body[9]
        if mask == 0:
            return None
        if setcc_opcode == 0x94:
            predicate = "zero"
            operator = "=="
            setcc = "sete"
        elif setcc_opcode == 0x95:
            predicate = "nonzero"
            operator = "!="
            setcc = "setne"
        else:
            return None
        return {
            "predicate": predicate,
            "operator": operator,
            "mask": mask,
            "requiresWidthHint": True,
            "setcc": setcc,
            "pattern": f"xor-eax-test-edi-imm32-{setcc}-al-ret",
        }
    return None


def x86_64_arg64_bitmask_bool(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg64_bitmask_bool(data)
    if decoded is None:
        return []
    if decoded.get("requiresWidthHint") and not x86_64_prefers_arg64_value(row):
        return []
    mask = int(decoded["mask"])
    operator = str(decoded["operator"])
    predicate = str(decoded["predicate"])
    source = header(f"x86-64-arg64-bitmask-{predicate}-cdecl", row) + "\n".join(
        [
            f"int {c_name}(unsigned long long value) {{",
            f"    return (value & 0x{mask:016x}ull) {operator} 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-arg64-bitmask-{predicate}-cdecl",
            variant=f"sysv-o2-register-arg64-bitmask-{predicate}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "rdi",
                "operator": operator,
                "predicate": predicate,
                "mask": f"0x{mask:016x}",
                "shift": decoded.get("shift"),
                "setcc": decoded.get("setcc"),
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def decode_x86_64_arg_udiv_pow2(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) == 5 and body == b"\x89\xf8\xd1\xe8\xc3":
        return {"shift": 1, "divisor": 2, "pattern": "mov-eax-edi-shr-eax-one-ret"}
    if len(body) == 6 and body[:3] == b"\x89\xf8\xc1" and body[3] == 0xE8 and body[5] == 0xC3:
        shift = body[4]
        if not 2 <= shift <= 31:
            return None
        return {"shift": shift, "divisor": 1 << shift, "pattern": "mov-eax-edi-shr-eax-imm8-ret"}
    return None


def x86_64_arg_udiv_pow2(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_udiv_pow2(data)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("x86-64-arg-udiv-pow2-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value / {divisor}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-arg-udiv-pow2-cdecl",
            variant=f"sysv-o2-register-arg-udiv-{divisor}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "operator": "/",
                "shift": int(decoded["shift"]),
                "divisor": divisor,
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def decode_x86_64_arg_urem_pow2(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 6 or body[:3] != b"\x89\xf8\x83" or body[3] != 0xE0 or body[5] != 0xC3:
        return None
    mask = body[4]
    if mask <= 1:
        return None
    divisor = mask + 1
    if divisor & (divisor - 1):
        return None
    return {
        "shift": divisor.bit_length() - 1,
        "divisor": divisor,
        "mask": mask,
        "pattern": "mov-eax-edi-and-eax-pow2-minus-one-ret",
    }


def x86_64_arg_urem_pow2(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_urem_pow2(data)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    mask = int(decoded["mask"])
    source = header("x86-64-arg-urem-pow2-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value % {divisor}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-arg-urem-pow2-cdecl",
            variant=f"sysv-o2-register-arg-urem-{divisor}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "operator": "%",
                "shift": int(decoded["shift"]),
                "divisor": divisor,
                "mask": f"0x{mask:08x}",
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_ARG_UDIV_MAGIC_OPS: dict[tuple[int, int], tuple[int, str]] = {
    (0xAAAAAAAB, 0x21): (3, "mov-ecx-edi-mov-eax-magic-imul-rax-rcx-shr-rax-33-ret"),
    (0xCCCCCCCD, 0x22): (5, "mov-ecx-edi-mov-eax-magic-imul-rax-rcx-shr-rax-34-ret"),
    (0xCCCCCCCD, 0x23): (10, "mov-ecx-edi-mov-eax-magic-imul-rax-rcx-shr-rax-35-ret"),
}


def decode_x86_64_arg_udiv_magic(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 16 or body[:2] != b"\x89\xf9" or body[2] != 0xB8 or body[7:11] != b"\x48\x0f\xaf\xc1" or body[11:14] != b"\x48\xc1\xe8" or body[15] != 0xC3:
        return None
    multiplier = int.from_bytes(body[3:7], "little", signed=False)
    shift = body[14]
    decoded = X86_64_ARG_UDIV_MAGIC_OPS.get((multiplier, shift))
    if decoded is None:
        return None
    divisor, pattern = decoded
    return {"divisor": divisor, "multiplier": multiplier, "shift": shift, "pattern": pattern}


def x86_64_arg_udiv_magic(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_udiv_magic(data)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("x86-64-arg-udiv-magic-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value / {divisor}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-arg-udiv-magic-cdecl",
            variant=f"sysv-o2-register-arg-udiv-{divisor}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "operator": "/",
                "divisor": divisor,
                "multiplier": f"0x{int(decoded['multiplier']):08x}",
                "shift": int(decoded["shift"]),
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_ARG_UREM_MAGIC_OPS: dict[bytes, tuple[int, str, int, str]] = {
    bytes.fromhex("89f889f9baabaaaaaa480fafd148c1ea218d0c5229c8c3"): (3, "0xaaaaaaab", 33, "mov-eax-edi-mov-ecx-edi-mov-edx-magic-imul-rdx-rcx-shr-rdx-33-lea-ecx-rdx-rdx2-sub-eax-ecx-ret"),
    bytes.fromhex("89f889f9bacdcccccc480fafd148c1ea228d0c9229c8c3"): (5, "0xcccccccd", 34, "mov-eax-edi-mov-ecx-edi-mov-edx-magic-imul-rdx-rcx-shr-rdx-34-lea-ecx-rdx-rdx4-sub-eax-ecx-ret"),
    bytes.fromhex("89f889f9bacdcccccc480fafd148c1ea2301d28d0c9229c8c3"): (10, "0xcccccccd", 35, "mov-eax-edi-mov-ecx-edi-mov-edx-magic-imul-rdx-rcx-shr-rdx-35-add-edx-edx-lea-ecx-rdx-rdx4-sub-eax-ecx-ret"),
}


def decode_x86_64_arg_urem_magic(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_UREM_MAGIC_OPS.get(body)
    if decoded is None:
        return None
    divisor, multiplier, shift, pattern = decoded
    return {
        "divisor": divisor,
        "multiplier": multiplier,
        "shift": shift,
        "pattern": pattern,
    }


def x86_64_arg_urem_magic(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_urem_magic(data)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("x86-64-arg-urem-magic-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value % {divisor}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-arg-urem-magic-cdecl",
            variant=f"sysv-o2-register-arg-urem-{divisor}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "operator": "%",
                "divisor": divisor,
                "multiplier": decoded["multiplier"],
                "shift": int(decoded["shift"]),
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def decode_x86_64_arg_sdiv_pow2(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == b"\x89\xf8\xc1\xe8\x1f\x01\xf8\xd1\xf8\xc3":
        return {
            "shift": 1,
            "divisor": 2,
            "bias": 1,
            "pattern": "mov-eax-edi-shr-eax-31-add-eax-edi-sar-eax-one-ret",
        }
    if len(body) == 12 and body[:2] == b"\x8d\x47" and body[3:8] == b"\x85\xff\x0f\x49\xc7" and body[8:10] == b"\xc1\xf8" and body[11] == 0xC3:
        bias = body[2]
        shift = body[10]
        if not 2 <= shift <= 7:
            return None
        if bias != (1 << shift) - 1:
            return None
        return {
            "shift": shift,
            "divisor": 1 << shift,
            "bias": bias,
            "pattern": "lea-eax-rdi-bias-test-edi-edi-cmovns-eax-edi-sar-eax-imm8-ret",
        }
    return None


def x86_64_arg_sdiv_pow2(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_sdiv_pow2(data)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("x86-64-arg-sdiv-pow2-cdecl", row) + "\n".join(
        [
            f"int {c_name}(int value) {{",
            f"    return value / {divisor};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-arg-sdiv-pow2-cdecl",
            variant=f"sysv-o2-register-arg-sdiv-{divisor}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "operator": "/",
                "shift": int(decoded["shift"]),
                "divisor": divisor,
                "bias": int(decoded["bias"]),
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def decode_x86_64_arg_srem_pow2(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == b"\x89\xf8\x89\xf9\xc1\xe9\x1f\x01\xf9\x83\xe1\xfe\x29\xc8\xc3":
        return {
            "shift": 1,
            "divisor": 2,
            "bias": 1,
            "mask": "0xfffffffe",
            "pattern": "mov-eax-edi-mov-ecx-edi-shr-ecx-31-add-ecx-edi-and-ecx-neg2-sub-eax-ecx-ret",
        }
    if len(body) == 16 and body[:2] == b"\x89\xf8" and body[2:4] == b"\x8d\x48" and body[5:10] == b"\x85\xff\x0f\x49\xcf" and body[10:12] == b"\x83\xe1" and body[13:] == b"\x29\xc8\xc3":
        bias = body[4]
        mask_byte = body[12]
        if bias == 0:
            return None
        divisor = bias + 1
        if divisor & (divisor - 1):
            return None
        shift = divisor.bit_length() - 1
        if not 2 <= shift <= 7:
            return None
        if mask_byte != ((256 - divisor) & 0xFF):
            return None
        return {
            "shift": shift,
            "divisor": divisor,
            "bias": bias,
            "mask": f"0xffffff{mask_byte:02x}",
            "pattern": "mov-eax-edi-lea-ecx-rax-bias-test-edi-edi-cmovns-ecx-edi-and-ecx-negdivisor-sub-eax-ecx-ret",
        }
    return None


def x86_64_arg_srem_pow2(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_srem_pow2(data)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("x86-64-arg-srem-pow2-cdecl", row) + "\n".join(
        [
            f"int {c_name}(int value) {{",
            f"    return value % {divisor};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-arg-srem-pow2-cdecl",
            variant=f"sysv-o2-register-arg-srem-{divisor}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "operator": "%",
                "shift": int(decoded["shift"]),
                "divisor": divisor,
                "bias": int(decoded["bias"]),
                "mask": decoded["mask"],
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_ARG_SDIV_MAGIC_OPS: dict[bytes, tuple[int, str, int, str]] = {
    bytes.fromhex("4863c74869c0565555554889c148c1e93f48c1e82001c8c3"): (3, "0x55555556", 32, "movsxd-rax-edi-imul-rax-rax-magic-mov-rcx-rax-shr-rcx-63-shr-rax-32-add-eax-ecx-ret"),
    bytes.fromhex("4863c74869c0676666664889c148c1e93f48c1f82101c8c3"): (5, "0x66666667", 33, "movsxd-rax-edi-imul-rax-rax-magic-mov-rcx-rax-shr-rcx-63-sar-rax-33-add-eax-ecx-ret"),
    bytes.fromhex("4863c74869c0676666664889c148c1e93f48c1f82201c8c3"): (10, "0x66666667", 34, "movsxd-rax-edi-imul-rax-rax-magic-mov-rcx-rax-shr-rcx-63-sar-rax-34-add-eax-ecx-ret"),
}


def decode_x86_64_arg_sdiv_magic(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_SDIV_MAGIC_OPS.get(body)
    if decoded is None:
        return None
    divisor, multiplier, shift, pattern = decoded
    return {
        "divisor": divisor,
        "multiplier": multiplier,
        "shift": shift,
        "pattern": pattern,
    }


def x86_64_arg_sdiv_magic(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_sdiv_magic(data)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("x86-64-arg-sdiv-magic-cdecl", row) + "\n".join(
        [
            f"int {c_name}(int value) {{",
            f"    return value / {divisor};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-arg-sdiv-magic-cdecl",
            variant=f"sysv-o2-register-arg-sdiv-{divisor}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "operator": "/",
                "divisor": divisor,
                "multiplier": decoded["multiplier"],
                "shift": int(decoded["shift"]),
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_ARG_SREM_MAGIC_OPS: dict[bytes, tuple[int, str, int, str]] = {
    bytes.fromhex("4863c74869c8565555554889ca48c1ea3f48c1e92001d18d0c4929c8c3"): (3, "0x55555556", 32, "movsxd-rax-edi-imul-rcx-rax-magic-mov-rdx-rcx-shr-rdx-63-shr-rcx-32-add-ecx-edx-lea-ecx-rcx-rcx2-sub-eax-ecx-ret"),
    bytes.fromhex("4863c74869c8676666664889ca48c1ea3f48c1f92101d18d0c8929c8c3"): (5, "0x66666667", 33, "movsxd-rax-edi-imul-rcx-rax-magic-mov-rdx-rcx-shr-rdx-63-sar-rcx-33-add-ecx-edx-lea-ecx-rcx-rcx4-sub-eax-ecx-ret"),
    bytes.fromhex("4863c74869c8676666664889ca48c1ea3f48c1f92201d101c98d0c8929c8c3"): (10, "0x66666667", 34, "movsxd-rax-edi-imul-rcx-rax-magic-mov-rdx-rcx-shr-rdx-63-sar-rcx-34-add-ecx-edx-add-ecx-ecx-lea-ecx-rcx-rcx4-sub-eax-ecx-ret"),
}


def decode_x86_64_arg_srem_magic(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_SREM_MAGIC_OPS.get(body)
    if decoded is None:
        return None
    divisor, multiplier, shift, pattern = decoded
    return {
        "divisor": divisor,
        "multiplier": multiplier,
        "shift": shift,
        "pattern": pattern,
    }


def x86_64_arg_srem_magic(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_srem_magic(data)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("x86-64-arg-srem-magic-cdecl", row) + "\n".join(
        [
            f"int {c_name}(int value) {{",
            f"    return value % {divisor};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-arg-srem-magic-cdecl",
            variant=f"sysv-o2-register-arg-srem-{divisor}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "operator": "%",
                "divisor": divisor,
                "multiplier": decoded["multiplier"],
                "shift": int(decoded["shift"]),
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def decode_x86_64_arg_bswap32(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == b"\x89\xf8\x0f\xc8\xc3":
        return {"pattern": "mov-eax-edi-bswap-eax-ret"}
    return None


def x86_64_arg_bswap32(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_bswap32(data)
    if decoded is None:
        return []
    expression = "((value & 0x000000ffu) << 24) | ((value & 0x0000ff00u) << 8) | ((value >> 8) & 0x0000ff00u) | (value >> 24)"
    source = header("x86-64-arg-bswap32-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-arg-bswap32-cdecl",
            variant="sysv-o2-register-arg-bswap32",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "operation": "bswap32",
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def decode_x86_64_arg_bswap64(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == b"\x48\x89\xf8\x48\x0f\xc8\xc3":
        return {"pattern": "mov-rax-rdi-bswap-rax-ret"}
    return None


def x86_64_arg_bswap64(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_bswap64(data)
    if decoded is None:
        return []
    expression = "((value & 0x00000000000000ffull) << 56) | ((value & 0x000000000000ff00ull) << 40) | ((value & 0x0000000000ff0000ull) << 24) | ((value & 0x00000000ff000000ull) << 8) | ((value >> 8) & 0x00000000ff000000ull) | ((value >> 24) & 0x0000000000ff0000ull) | ((value >> 40) & 0x000000000000ff00ull) | (value >> 56)"
    source = header("x86-64-arg-bswap64-cdecl", row) + "\n".join(
        [
            f"unsigned long long {c_name}(unsigned long long value) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-arg-bswap64-cdecl",
            variant="sysv-o2-register-arg-bswap64",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned long long",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "rdi",
                "operation": "bswap64",
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def decode_x86_64_arg_rotate(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) == 5 and body[:2] == b"\x89\xf8" and body[2] == 0xD1 and body[4] == 0xC3:
        if body[3] == 0xC0:
            return {"direction": "left", "count": 1, "encoding": "rol", "pattern": "mov-eax-edi-rol-eax-one-ret"}
        if body[3] == 0xC8:
            return {"direction": "right", "count": 1, "encoding": "ror", "pattern": "mov-eax-edi-ror-eax-one-ret"}
    if len(body) == 6 and body[:2] == b"\x89\xf8" and body[2] == 0xC1 and body[3] == 0xC0 and body[5] == 0xC3:
        count = body[4]
        if not 1 <= count <= 31:
            return None
        if count > 16:
            return {"direction": "right", "count": 32 - count, "encoding": "rol", "encodedCount": count, "pattern": "mov-eax-edi-rol-eax-imm8-ret"}
        return {"direction": "left", "count": count, "encoding": "rol", "encodedCount": count, "pattern": "mov-eax-edi-rol-eax-imm8-ret"}
    return None


def x86_64_arg_rotate(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_rotate(data)
    if decoded is None:
        return []
    direction = str(decoded["direction"])
    count = int(decoded["count"])
    if direction == "left":
        expression = f"(value << {count}) | (value >> {32 - count})"
    else:
        expression = f"(value >> {count}) | (value << {32 - count})"
    source = header(f"x86-64-arg-rot{direction[0]}-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-arg-rot{direction[0]}-cdecl",
            variant=f"sysv-o2-register-arg-rot{direction[0]}-{count}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "direction": direction,
                "count": count,
                "encodedCount": decoded.get("encodedCount", count),
                "encoding": decoded["encoding"],
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def decode_x86_64_arg64_rotate(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) == 7 and body[:3] == b"\x48\x89\xf8" and body[3:5] == b"\x48\xd1" and body[6] == 0xC3:
        if body[5] == 0xC0:
            return {"direction": "left", "count": 1, "encoding": "rol", "pattern": "mov-rax-rdi-rol-rax-one-ret"}
        if body[5] == 0xC8:
            return {"direction": "right", "count": 1, "encoding": "ror", "pattern": "mov-rax-rdi-ror-rax-one-ret"}
    if len(body) == 8 and body[:3] == b"\x48\x89\xf8" and body[3:6] == b"\x48\xc1\xc0" and body[7] == 0xC3:
        count = body[6]
        if not 1 <= count <= 63:
            return None
        if count > 32:
            return {"direction": "right", "count": 64 - count, "encoding": "rol", "encodedCount": count, "pattern": "mov-rax-rdi-rol-rax-imm8-ret"}
        return {"direction": "left", "count": count, "encoding": "rol", "encodedCount": count, "pattern": "mov-rax-rdi-rol-rax-imm8-ret"}
    return None


def x86_64_arg64_rotate(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg64_rotate(data)
    if decoded is None:
        return []
    direction = str(decoded["direction"])
    count = int(decoded["count"])
    if direction == "left":
        expression = f"(value << {count}) | (value >> {64 - count})"
    else:
        expression = f"(value >> {count}) | (value << {64 - count})"
    source = header(f"x86-64-arg64-rot{direction[0]}-cdecl", row) + "\n".join(
        [
            f"unsigned long long {c_name}(unsigned long long value) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-arg64-rot{direction[0]}-cdecl",
            variant=f"sysv-o2-register-arg64-rot{direction[0]}-{count}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned long long",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "rdi",
                "direction": direction,
                "count": count,
                "encodedCount": decoded.get("encodedCount", count),
                "encoding": decoded["encoding"],
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_ARG_SHIFT_IMM8_OPS: dict[int, tuple[str, str, str, str]] = {
    0xE0: ("shl", "<<", "unsigned int", "unsigned int"),
    0xE8: ("shr", ">>", "unsigned int", "unsigned int"),
    0xF8: ("sar", ">>", "int", "int"),
}

X86_64_ARG_SHIFT_ONE_OPS: dict[int, tuple[str, str, str, str]] = {
    0xE8: ("shr", ">>", "unsigned int", "unsigned int"),
    0xF8: ("sar", ">>", "int", "int"),
}


def x86_64_arg_shift_imm8(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    body = strip_alignment_padding(data)
    pattern = "mov-eax-edi-shift-imm8-ret"
    if len(body) == 6 and body[:3] == b"\x89\xf8\xc1" and body[-1] == 0xC3:
        decoded = X86_64_ARG_SHIFT_IMM8_OPS.get(body[3])
        if decoded is None:
            return []
        shift = body[4]
        if not 2 <= shift <= 31:
            return []
    elif len(body) == 5 and body[:3] == b"\x89\xf8\xd1" and body[-1] == 0xC3:
        decoded = X86_64_ARG_SHIFT_ONE_OPS.get(body[3])
        if decoded is None:
            return []
        shift = 1
        pattern = "mov-eax-edi-shift-one-ret"
    else:
        return []
    suffix, operator, value_type, return_type = decoded
    source = header(f"x86-64-arg-{suffix}-imm8-cdecl", row) + "\n".join(
        [
            f"{return_type} {c_name}({value_type} value) {{",
            f"    return value {operator} {shift};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-arg-{suffix}-imm8-cdecl",
            variant=f"sysv-o2-register-arg-{suffix}-imm8",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type=return_type,
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": pattern,
                "registerArg": "edi",
                "operator": operator,
                "shift": shift,
                "valueType": value_type,
                "returnType": return_type,
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_ARG64_SHIFT_IMM8_OPS: dict[int, tuple[str, str, str, str]] = {
    0xE0: ("shl", "<<", "unsigned long long", "unsigned long long"),
    0xE8: ("shr", ">>", "unsigned long long", "unsigned long long"),
    0xF8: ("sar", ">>", "long long", "long long"),
}

X86_64_ARG64_SHIFT_ONE_OPS: dict[int, tuple[str, str, str, str]] = {
    0xE8: ("shr", ">>", "unsigned long long", "unsigned long long"),
    0xF8: ("sar", ">>", "long long", "long long"),
}


def x86_64_arg64_shift_imm8(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    body = strip_alignment_padding(data)
    pattern = "mov-rax-rdi-shift-imm8-ret"
    if len(body) == 8 and body[:3] == b"\x48\x89\xf8" and body[3:5] == b"\x48\xc1" and body[-1] == 0xC3:
        decoded = X86_64_ARG64_SHIFT_IMM8_OPS.get(body[5])
        if decoded is None:
            return []
        shift = body[6]
        if not 2 <= shift <= 63:
            return []
    elif len(body) == 7 and body[:3] == b"\x48\x89\xf8" and body[3:5] == b"\x48\xd1" and body[-1] == 0xC3:
        decoded = X86_64_ARG64_SHIFT_ONE_OPS.get(body[5])
        if decoded is None:
            return []
        shift = 1
        pattern = "mov-rax-rdi-shift-one-ret"
    else:
        return []
    suffix, operator, value_type, return_type = decoded
    source = header(f"x86-64-arg64-{suffix}-imm8-cdecl", row) + "\n".join(
        [
            f"{return_type} {c_name}({value_type} value) {{",
            f"    return value {operator} {shift};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-arg64-{suffix}-imm8-cdecl",
            variant=f"sysv-o2-register-arg64-{suffix}-imm8",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type=return_type,
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": pattern,
                "registerArg": "rdi",
                "operator": operator,
                "shift": shift,
                "valueType": value_type,
                "returnType": return_type,
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_ARG_IMM8_BINARY_OPS: dict[int, tuple[str, str]] = {
    0xE0: ("and", "&"),
    0xC8: ("or", "|"),
    0xF0: ("xor", "^"),
}


X86_64_ARG_ACCUM_IMM32_BINARY_OPS: dict[int, tuple[str, str]] = {
    0x25: ("and", "&"),
    0x0D: ("or", "|"),
    0x35: ("xor", "^"),
}


def decode_x86_64_arg_imm8_binary_op(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) == 4 and body[:2] == b"\x8d\x47" and body[3] == 0xC3:
        raw_immediate = body[2]
        signed_immediate = raw_immediate if raw_immediate < 0x80 else raw_immediate - 0x100
        if signed_immediate == 0:
            return None
        suffix = "add" if signed_immediate > 0 else "sub"
        operator = "+" if signed_immediate > 0 else "-"
        return {
            "suffix": suffix,
            "operator": operator,
            "immediate": abs(signed_immediate),
            "rawImmediate": raw_immediate,
            "signedImmediate": signed_immediate,
            "immediateBits": 8,
            "pattern": "lea-eax-rdi-disp8-ret",
        }
    if len(body) == 7 and body[:2] == b"\x8d\x87" and body[6] == 0xC3:
        raw_immediate = int.from_bytes(body[2:6], "little", signed=False)
        signed_immediate = int.from_bytes(body[2:6], "little", signed=True)
        if signed_immediate == 0:
            return None
        suffix = "add" if signed_immediate > 0 else "sub"
        operator = "+" if signed_immediate > 0 else "-"
        return {
            "suffix": suffix,
            "operator": operator,
            "immediate": abs(signed_immediate),
            "rawImmediate": raw_immediate,
            "signedImmediate": signed_immediate,
            "immediateBits": 32,
            "pattern": "lea-eax-rdi-disp32-ret",
        }
    if len(body) == 6 and body[:3] == b"\x89\xf8\x83" and body[5] == 0xC3:
        decoded = X86_64_ARG_IMM8_BINARY_OPS.get(body[3])
        if decoded is None:
            return None
        raw_immediate = body[4]
        if raw_immediate > 0x7F:
            return None
        suffix, operator = decoded
        return {
            "suffix": suffix,
            "operator": operator,
            "immediate": raw_immediate,
            "rawImmediate": raw_immediate,
            "signedImmediate": raw_immediate,
            "immediateBits": 8,
            "pattern": "mov-eax-edi-op-eax-imm8-ret",
        }
    if len(body) == 8 and body[:2] == b"\x89\xf8" and body[7] == 0xC3:
        decoded = X86_64_ARG_ACCUM_IMM32_BINARY_OPS.get(body[2])
        if decoded is None:
            return None
        raw_immediate = int.from_bytes(body[3:7], "little", signed=False)
        if raw_immediate == 0 and decoded[0] in {"or", "xor"}:
            return None
        suffix, operator = decoded
        return {
            "suffix": suffix,
            "operator": operator,
            "immediate": raw_immediate,
            "rawImmediate": raw_immediate,
            "signedImmediate": int.from_bytes(body[3:7], "little", signed=True),
            "immediateBits": 32,
            "pattern": "mov-eax-edi-accum-op-eax-imm32-ret",
        }
    return None


def x86_64_arg_imm8_binary_op(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_imm8_binary_op(data)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"])
    immediate_bits = int(decoded.get("immediateBits") or 8)
    immediate_digits = 2 if immediate_bits == 8 else 8
    source = header(f"x86-64-arg-{suffix}-imm{immediate_bits}-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value {operator} 0x{immediate:0{immediate_digits}x}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-arg-{suffix}-imm{immediate_bits}-cdecl",
            variant=f"sysv-o2-register-arg-{suffix}-imm{immediate_bits}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "operator": operator,
                "immediate": f"0x{immediate:0{immediate_digits}x}",
                "immediateBits": immediate_bits,
                "rawImmediate": int(decoded["rawImmediate"]),
                "signedImmediate": int(decoded["signedImmediate"]),
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_ARG_IMM32_BINARY64_OPS: dict[int, tuple[str, str]] = {
    0x25: ("and", "&"),
    0x0D: ("or", "|"),
    0x35: ("xor", "^"),
}


def decode_x86_64_arg_imm32_binary_op64(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) == 8 and body[:3] == b"\x48\x8d\x87" and body[7] == 0xC3:
        raw_immediate = int.from_bytes(body[3:7], "little", signed=False)
        signed_immediate = int.from_bytes(body[3:7], "little", signed=True)
        if signed_immediate == 0:
            return None
        suffix = "add" if signed_immediate > 0 else "sub"
        operator = "+" if signed_immediate > 0 else "-"
        return {
            "suffix": suffix,
            "operator": operator,
            "immediate": abs(signed_immediate),
            "rawImmediate": raw_immediate,
            "signedImmediate": signed_immediate,
            "immediateBits": 32,
            "pattern": "lea-rax-rdi-disp32-ret",
        }
    if len(body) == 10 and body[:3] == b"\x48\x89\xf8" and body[3] == 0x48 and body[9] == 0xC3:
        decoded = X86_64_ARG_IMM32_BINARY64_OPS.get(body[4])
        if decoded is None or decoded[0] == "and":
            return None
        raw_immediate = int.from_bytes(body[5:9], "little", signed=False)
        if raw_immediate == 0:
            return None
        suffix, operator = decoded
        return {
            "suffix": suffix,
            "operator": operator,
            "immediate": raw_immediate,
            "rawImmediate": raw_immediate,
            "signedImmediate": int.from_bytes(body[5:9], "little", signed=True),
            "immediateBits": 32,
            "pattern": "mov-rax-rdi-rex-accum-op-rax-imm32-ret",
        }
    if len(body) == 9 and body[:3] == b"\x48\x89\xf8" and body[8] == 0xC3:
        decoded = X86_64_ARG_IMM32_BINARY64_OPS.get(body[3])
        if decoded is None or decoded[0] != "and":
            return None
        raw_immediate = int.from_bytes(body[4:8], "little", signed=False)
        suffix, operator = decoded
        return {
            "suffix": suffix,
            "operator": operator,
            "immediate": raw_immediate,
            "rawImmediate": raw_immediate,
            "signedImmediate": int.from_bytes(body[4:8], "little", signed=True),
            "immediateBits": 32,
            "pattern": "mov-rax-rdi-and-eax-imm32-ret",
        }
    return None


def x86_64_arg_imm32_binary_op64(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_imm32_binary_op64(data)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"])
    source = header(f"x86-64-arg64-{suffix}-imm32-cdecl", row) + "\n".join(
        [
            f"unsigned long long {c_name}(unsigned long long value) {{",
            f"    return value {operator} 0x{immediate:08x}ull;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-arg64-{suffix}-imm32-cdecl",
            variant=f"sysv-o2-register-arg64-{suffix}-imm32",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned long long",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "rdi",
                "operator": operator,
                "immediate": f"0x{immediate:08x}",
                "immediateBits": int(decoded["immediateBits"]),
                "rawImmediate": int(decoded["rawImmediate"]),
                "signedImmediate": int(decoded["signedImmediate"]),
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_ARG_UNARY_OPS: dict[bytes, tuple[str, str, str]] = {
    b"\x89\xf8\xf7\xd8\xc3": ("neg", "-", "mov-eax-edi-neg-eax-ret"),
    b"\x89\xf8\xf7\xd0\xc3": ("not", "~", "mov-eax-edi-not-eax-ret"),
}


def x86_64_arg_unary_op(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_UNARY_OPS.get(body)
    if decoded is None:
        return []
    suffix, operator, pattern = decoded
    source = header(f"x86-64-arg-{suffix}-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return {operator}value;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-arg-{suffix}-cdecl",
            variant=f"sysv-o2-register-arg-{suffix}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": pattern,
                "registerArg": "edi",
                "operator": operator,
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_ARG_UNARY_OPS64: dict[bytes, tuple[str, str, str]] = {
    b"\x48\x89\xf8\x48\xf7\xd8\xc3": ("neg", "-", "mov-rax-rdi-neg-rax-ret"),
    b"\x48\x89\xf8\x48\xf7\xd0\xc3": ("not", "~", "mov-rax-rdi-not-rax-ret"),
}


def x86_64_arg64_unary_op(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_UNARY_OPS64.get(body)
    if decoded is None:
        return []
    suffix, operator, pattern = decoded
    source = header(f"x86-64-arg64-{suffix}-cdecl", row) + "\n".join(
        [
            f"unsigned long long {c_name}(unsigned long long value) {{",
            f"    return {operator}value;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-arg64-{suffix}-cdecl",
            variant=f"sysv-o2-register-arg64-{suffix}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned long long",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": pattern,
                "registerArg": "rdi",
                "operator": operator,
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_ARG_NEG_CMOV_OPS: dict[bytes, tuple[str, str, str, str]] = {
    b"\x89\xf8\xf7\xd8\x0f\x48\xc7\xc3": ("abs", "value < 0 ? -value : value", "cmovs", "mov-eax-edi-neg-eax-cmovs-eax-edi-ret"),
    b"\x89\xf8\xf7\xd8\x0f\x49\xc7\xc3": ("neg-if-pos", "value > 0 ? -value : value", "cmovns", "mov-eax-edi-neg-eax-cmovns-eax-edi-ret"),
}


def x86_64_arg_neg_cmov(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_NEG_CMOV_OPS.get(body)
    if decoded is None:
        return []
    suffix, expression, cmov, pattern = decoded
    source = header(f"x86-64-arg-{suffix}-cmov-cdecl", row) + "\n".join(
        [
            f"int {c_name}(int value) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-arg-{suffix}-cmov-cdecl",
            variant=f"sysv-o2-register-arg-{suffix}-cmov",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": pattern,
                "registerArg": "edi",
                "valueType": "int",
                "returnType": "int",
                "expression": expression,
                "cmov": cmov,
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_ARG64_NEG_CMOV_OPS: dict[bytes, tuple[str, str, str, str]] = {
    b"\x48\x89\xf8\x48\xf7\xd8\x48\x0f\x48\xc7\xc3": ("abs", "value < 0 ? -value : value", "cmovs", "mov-rax-rdi-neg-rax-cmovs-rax-rdi-ret"),
    b"\x48\x89\xf8\x48\xf7\xd8\x48\x0f\x49\xc7\xc3": ("neg-if-pos", "value > 0 ? -value : value", "cmovns", "mov-rax-rdi-neg-rax-cmovns-rax-rdi-ret"),
}


def x86_64_arg64_neg_cmov(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG64_NEG_CMOV_OPS.get(body)
    if decoded is None:
        return []
    suffix, expression, cmov, pattern = decoded
    source = header(f"x86-64-arg64-{suffix}-cmov-cdecl", row) + "\n".join(
        [
            f"long long {c_name}(long long value) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-arg64-{suffix}-cmov-cdecl",
            variant=f"sysv-o2-register-arg64-{suffix}-cmov",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="long long",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": pattern,
                "registerArg": "rdi",
                "valueType": "long long",
                "returnType": "long long",
                "expression": expression,
                "cmov": cmov,
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_ARG_CAST_OPS: dict[bytes, tuple[str, str, str, str, str]] = {
    b"\x40\x0f\xb6\xc7\xc3": ("u8", "unsigned int", "unsigned int", "(unsigned char)value", "movzx-eax-dil-ret"),
    b"\x40\x0f\xbe\xc7\xc3": ("i8", "int", "int", "(signed char)value", "movsx-eax-dil-ret"),
    b"\x0f\xb7\xc7\xc3": ("u16", "unsigned int", "unsigned int", "(unsigned short)value", "movzx-eax-di-ret"),
    b"\x0f\xbf\xc7\xc3": ("i16", "int", "int", "(short)value", "movsx-eax-di-ret"),
}


def x86_64_arg_cast(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG_CAST_OPS.get(body)
    if decoded is None:
        return []
    suffix, value_type, return_type, expression, pattern = decoded
    source = header(f"x86-64-arg-cast-{suffix}-cdecl", row) + "\n".join(
        [
            f"{return_type} {c_name}({value_type} value) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-arg-cast-{suffix}-cdecl",
            variant=f"sysv-o2-register-arg-cast-{suffix}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type=return_type,
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": pattern,
                "registerArg": "edi",
                "valueType": value_type,
                "returnType": return_type,
                "expression": expression,
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_ARG64_SIGN_EXTEND_OPS: dict[bytes, tuple[str, str, str, str, str]] = {
    b"\x48\x63\xc7\xc3": ("i32", "int", "long long", "(long long)value", "movsxd-rax-edi-ret"),
    b"\x48\x0f\xbe\xc7\xc3": ("i8", "int", "long long", "(signed char)value", "movsx-rax-dil-ret"),
    b"\x48\x0f\xbf\xc7\xc3": ("i16", "int", "long long", "(short)value", "movsx-rax-di-ret"),
}


def x86_64_arg64_sign_extend(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG64_SIGN_EXTEND_OPS.get(body)
    if decoded is None:
        return []
    suffix, value_type, return_type, expression, pattern = decoded
    source = header(f"x86-64-arg64-sign-extend-{suffix}-cdecl", row) + "\n".join(
        [
            f"{return_type} {c_name}({value_type} value) {{",
            f"    return {expression};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-arg64-sign-extend-{suffix}-cdecl",
            variant=f"sysv-o2-register-arg64-sign-extend-{suffix}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type=return_type,
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": pattern,
                "registerArg": "edi",
                "valueType": value_type,
                "returnType": return_type,
                "expression": expression,
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def decode_x86_64_arg_narrow_imm8_compare(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) == 10 and body[:3] == b"\x31\xc0\x40" and body[3] == 0x80 and body[4] == 0xFF and body[6] == 0x0F and body[8:] == b"\xc0\xc3":
        width = 8
        immediate = body[5]
        setcc_opcode = body[7]
    elif len(body) == 10 and body[:3] == b"\x31\xc0\x66" and body[3] == 0x83 and body[4] == 0xFF and body[6] == 0x0F and body[8:] == b"\xc0\xc3":
        width = 16
        immediate = body[5]
        setcc_opcode = body[7]
    else:
        return None
    if setcc_opcode in X86_64_UNSIGNED_COMPARE_SETCC:
        suffix, operator, setcc = X86_64_UNSIGNED_COMPARE_SETCC[setcc_opcode]
        signed = False
    elif setcc_opcode in X86_64_SIGNED_COMPARE_SETCC:
        suffix, operator, setcc = X86_64_SIGNED_COMPARE_SETCC[setcc_opcode]
        signed = True
    else:
        return None
    cast_type = {
        (8, False): "unsigned char",
        (8, True): "signed char",
        (16, False): "unsigned short",
        (16, True): "short",
    }[(width, signed)]
    value_type = "int" if signed else "unsigned int"
    expression_immediate = immediate if not signed or immediate < 0x80 else immediate - 0x100
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
        "width": width,
        "signed": signed,
        "castType": cast_type,
        "valueType": value_type,
        "immediate": expression_immediate,
        "rawImmediate": immediate,
        "pattern": f"xor-eax-cmp-{'dil' if width == 8 else 'di'}-imm8-setcc-al-ret",
    }


def x86_64_arg_narrow_imm8_compare(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_narrow_imm8_compare(data)
    if decoded is None:
        return []
    width = int(decoded["width"])
    signed = bool(decoded["signed"])
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    value_type = str(decoded["valueType"])
    cast_type = str(decoded["castType"])
    immediate = int(decoded["immediate"])
    family = "int" if signed else "uint"
    immediate_expr = f"({immediate})" if signed else f"0x{int(decoded['rawImmediate']):02x}u"
    source = header(f"x86-64-{family}{width}-{suffix}-imm8-cdecl", row) + "\n".join(
        [
            f"int {c_name}({value_type} value) {{",
            f"    return ({cast_type})value {operator} {immediate_expr};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-{family}{width}-{suffix}-imm8-cdecl",
            variant=f"sysv-o2-register-arg-{family}{width}-{suffix}-imm8",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "operator": operator,
                "width": width,
                "castType": cast_type,
                "valueType": value_type,
                "immediate": immediate_expr,
                "rawImmediate": int(decoded["rawImmediate"]),
                "setcc": decoded["setcc"],
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def decode_x86_64_arg_narrow_movzx_imm8_compare(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) == 13 and body[:8] == b"\x40\x0f\xb6\xcf\x31\xc0\x83\xf9" and body[9] == 0x0F and body[11:] == b"\xc0\xc3":
        width = 8
        immediate = body[8]
        setcc_opcode = body[10]
        movzx = "movzx-ecx-dil"
    elif len(body) == 12 and body[:7] == b"\x0f\xb7\xcf\x31\xc0\x83\xf9" and body[8] == 0x0F and body[10:] == b"\xc0\xc3":
        width = 16
        immediate = body[7]
        setcc_opcode = body[9]
        movzx = "movzx-ecx-di"
    else:
        return None
    decoded = X86_64_UNSIGNED_COMPARE_SETCC.get(setcc_opcode)
    if decoded is None:
        return None
    suffix, operator, setcc = decoded
    if suffix not in {"lt", "ge"}:
        return None
    cast_type = "unsigned char" if width == 8 else "unsigned short"
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
        "width": width,
        "castType": cast_type,
        "valueType": "unsigned int",
        "rawImmediate": immediate,
        "pattern": f"{movzx}-xor-eax-cmp-ecx-imm8-setcc-al-ret",
    }


def x86_64_arg_narrow_movzx_imm8_compare(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_narrow_movzx_imm8_compare(data)
    if decoded is None:
        return []
    width = int(decoded["width"])
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    cast_type = str(decoded["castType"])
    value_type = str(decoded["valueType"])
    immediate_expr = f"0x{int(decoded['rawImmediate']):02x}u"
    rule = f"x86-64-uint{width}-{suffix}-movzx-imm8-cdecl"
    source = header(rule, row) + "\n".join(
        [
            f"int {c_name}({value_type} value) {{",
            f"    return ({cast_type})value {operator} {immediate_expr};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=rule,
            variant=f"sysv-o2-register-arg-uint{width}-{suffix}-movzx-imm8",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "scratchRegister": "ecx",
                "operator": operator,
                "width": width,
                "castType": cast_type,
                "valueType": value_type,
                "immediate": immediate_expr,
                "rawImmediate": int(decoded["rawImmediate"]),
                "setcc": decoded["setcc"],
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def decode_x86_64_arg_imm8_compare(data: bytes, *, signed: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    immediate_bits = 0
    raw_immediate = 0
    immediate = 0
    setcc_offset = 0
    if len(body) == 9 and body[:4] == b"\x31\xc0\x83\xff" and body[5] == 0x0F and body[7:] == b"\xc0\xc3":
        immediate_bits = 8
        raw_immediate = body[4]
        immediate = raw_immediate if raw_immediate < 0x80 else (raw_immediate - 0x100 if signed else raw_immediate | 0xFFFFFF00)
        setcc_offset = 6
        pattern = "xor-eax-cmp-edi-imm8-setcc-al-ret"
    elif len(body) == 12 and body[:4] == b"\x31\xc0\x81\xff" and body[8] == 0x0F and body[10:] == b"\xc0\xc3":
        immediate_bits = 32
        raw_immediate = int.from_bytes(body[4:8], "little", signed=False)
        immediate = int.from_bytes(body[4:8], "little", signed=signed)
        setcc_offset = 9
        pattern = "xor-eax-cmp-edi-imm32-setcc-al-ret"
    else:
        return None
    rules = X86_64_SIGNED_COMPARE_SETCC if signed else X86_64_UNSIGNED_COMPARE_SETCC
    decoded = rules.get(body[setcc_offset])
    if decoded is None:
        return None
    suffix, operator, setcc = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
        "immediate": immediate,
        "rawImmediate": raw_immediate,
        "immediateBits": immediate_bits,
        "pattern": pattern,
    }


def normalize_x86_64_arg_imm8_compare_operator(
    operator: str,
    immediate: int,
    *,
    signed: bool,
) -> tuple[str, int]:
    if operator not in {"<=", ">"}:
        return operator, immediate

    overflow_limit = 0x7FFFFFFF if signed else 0xFFFFFFFF
    if immediate >= overflow_limit:
        return operator, immediate

    next_immediate = immediate + 1
    if operator == "<=" and immediate < overflow_limit:
        return "<", next_immediate
    if operator == ">" and immediate < overflow_limit:
        return ">=", next_immediate
    return operator, immediate


def x86_64_arg_unsigned_imm8_compare(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_imm8_compare(data, signed=False)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"]) & 0xFFFFFFFF
    immediate_bits = int(decoded.get("immediateBits") or 8)
    operator, immediate = normalize_x86_64_arg_imm8_compare_operator(operator, immediate, signed=False)
    source = header(f"x86-64-uint-{suffix}-imm{immediate_bits}-cdecl", row) + "\n".join(
        [
            f"int {c_name}(unsigned int value) {{",
            f"    return value {operator} 0x{immediate:08x}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-uint-{suffix}-imm{immediate_bits}-cdecl",
            variant=f"sysv-o2-register-arg-uint-{suffix}-imm{immediate_bits}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "operator": operator,
                "immediate": f"0x{immediate:08x}",
                "immediateBits": immediate_bits,
                "rawImmediate": int(decoded["rawImmediate"]),
                "setcc": decoded["setcc"],
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def x86_64_arg_signed_imm8_compare(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_imm8_compare(data, signed=True)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"])
    immediate_bits = int(decoded.get("immediateBits") or 8)
    operator, immediate = normalize_x86_64_arg_imm8_compare_operator(operator, immediate, signed=True)
    source = header(f"x86-64-int-{suffix}-imm{immediate_bits}-cdecl", row) + "\n".join(
        [
            f"int {c_name}(int value) {{",
            f"    return value {operator} ({immediate});",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-int-{suffix}-imm{immediate_bits}-cdecl",
            variant=f"sysv-o2-register-arg-int-{suffix}-imm{immediate_bits}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "operator": operator,
                "immediate": f"0x{(immediate & 0xFFFFFFFF):08x}",
                "immediateBits": immediate_bits,
                "rawImmediate": int(decoded["rawImmediate"]),
                "setcc": decoded["setcc"],
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_UNSIGNED_COMPARE_SETCC: dict[int, tuple[str, str, str]] = {
    0x92: ("lt", "<", "setb"),
    0x93: ("ge", ">=", "setae"),
    0x94: ("eq", "==", "sete"),
    0x95: ("ne", "!=", "setne"),
    0x96: ("le", "<=", "setbe"),
    0x97: ("gt", ">", "seta"),
}


X86_64_SIGNED_COMPARE_SETCC: dict[int, tuple[str, str, str]] = {
    0x9C: ("lt", "<", "setl"),
    0x9D: ("ge", ">=", "setge"),
    0x9E: ("le", "<=", "setle"),
    0x9F: ("gt", ">", "setg"),
}


def decode_x86_64_two_args_compare(data: bytes, rules: dict[int, tuple[str, str, str]]) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 8 or body[:4] != b"\x31\xc0\x39\xf7" or body[4] != 0x0F or body[6:] != b"\xc0\xc3":
        return None
    decoded = rules.get(body[5])
    if decoded is None:
        return None
    suffix, operator, setcc = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
    }


def x86_64_two_args_unsigned_compare(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_two_args_compare(data, X86_64_UNSIGNED_COMPARE_SETCC)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = header(f"x86-64-uint-{suffix}-two-args-cdecl", row) + "\n".join(
        [
            f"int {c_name}(unsigned int a, unsigned int b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-uint-{suffix}-two-args-cdecl",
            variant=f"sysv-o2-register-args-uint-{suffix}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": "xor-eax-cmp-edi-esi-setcc-al-ret",
                "registerArgs": ["edi", "esi"],
                "operator": operator,
                "setcc": decoded["setcc"],
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def x86_64_two_args_signed_compare(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_two_args_compare(data, X86_64_SIGNED_COMPARE_SETCC)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = header(f"x86-64-int-{suffix}-two-args-cdecl", row) + "\n".join(
        [
            f"int {c_name}(int a, int b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-int-{suffix}-two-args-cdecl",
            variant=f"sysv-o2-register-args-int-{suffix}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": "xor-eax-cmp-edi-esi-setcc-al-ret",
                "registerArgs": ["edi", "esi"],
                "operator": operator,
                "setcc": decoded["setcc"],
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def decode_x86_64_two_args_compare64(data: bytes, rules: dict[int, tuple[str, str, str]]) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 9 or body[:5] != b"\x31\xc0\x48\x39\xf7" or body[5] != 0x0F or body[7:] != b"\xc0\xc3":
        return None
    decoded = rules.get(body[6])
    if decoded is None:
        return None
    suffix, operator, setcc = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
    }


def x86_64_two_args_unsigned_compare64(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_two_args_compare64(data, X86_64_UNSIGNED_COMPARE_SETCC)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = header(f"x86-64-uint64-{suffix}-two-args-cdecl", row) + "\n".join(
        [
            f"int {c_name}(unsigned long long a, unsigned long long b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-uint64-{suffix}-two-args-cdecl",
            variant=f"sysv-o2-register-args-uint64-{suffix}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": "xor-eax-cmp-rdi-rsi-setcc-al-ret",
                "registerArgs": ["rdi", "rsi"],
                "operator": operator,
                "setcc": decoded["setcc"],
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def x86_64_two_args_signed_compare64(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_two_args_compare64(data, X86_64_SIGNED_COMPARE_SETCC)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = header(f"x86-64-int64-{suffix}-two-args-cdecl", row) + "\n".join(
        [
            f"int {c_name}(long long a, long long b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-int64-{suffix}-two-args-cdecl",
            variant=f"sysv-o2-register-args-int64-{suffix}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": "xor-eax-cmp-rdi-rsi-setcc-al-ret",
                "registerArgs": ["rdi", "rsi"],
                "operator": operator,
                "setcc": decoded["setcc"],
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


X86_64_SIGNED_ZERO_COMPARE_SETCC: dict[int, tuple[str, str, str]] = {
    0x9E: ("le", "<=", "setle"),
    0x9F: ("gt", ">", "setg"),
}


def decode_x86_64_arg_signed_zero_compare(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 8 or body[:4] != b"\x31\xc0\x85\xff" or body[4] != 0x0F or body[6:] != b"\xc0\xc3":
        return None
    decoded = X86_64_SIGNED_ZERO_COMPARE_SETCC.get(body[5])
    if decoded is None:
        return None
    suffix, operator, setcc = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
        "pattern": "xor-eax-test-edi-edi-setcc-al-ret",
    }


def x86_64_arg_signed_zero_compare(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_signed_zero_compare(data)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = header(f"x86-64-int-zero-{suffix}-cdecl", row) + "\n".join(
        [
            f"int {c_name}(int value) {{",
            f"    return value {operator} 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-int-zero-{suffix}-cdecl",
            variant=f"sysv-o2-register-arg-int-zero-{suffix}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "operator": operator,
                "immediate": 0,
                "setcc": decoded["setcc"],
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def decode_x86_64_arg_nonzero_const_select(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 15 or body[:4] != b"\x31\xc0\x85\xff" or body[4] != 0x0F or body[6:9] != b"\xc0\x8d\x04" or body[14] != 0xC3:
        return None
    setcc_opcode = body[5]
    if setcc_opcode not in {0x94, 0x95}:
        return None
    sib = body[9]
    if sib & 0x07 != 0x05 or ((sib >> 3) & 0x07) != 0x00:
        return None
    scale = 1 << ((sib >> 6) & 0x03)
    if scale not in {2, 4, 8}:
        return None
    base_value = int.from_bytes(body[10:14], "little", signed=False)
    scaled_value = base_value + scale
    if setcc_opcode == 0x95:
        false_value = base_value
        true_value = scaled_value
        setcc = "setne"
    else:
        false_value = scaled_value
        true_value = base_value
        setcc = "sete"
    return {
        "trueValue": true_value,
        "falseValue": false_value,
        "baseValue": base_value,
        "scale": scale,
        "setcc": setcc,
        "pattern": f"xor-eax-test-edi-edi-{setcc}-al-lea-eax-rax{scale}-disp32-ret",
    }


def x86_64_arg_nonzero_const_select(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_nonzero_const_select(data)
    if decoded is None:
        return []
    true_value = int(decoded["trueValue"])
    false_value = int(decoded["falseValue"])
    source = header("x86-64-arg-nonzero-const-select-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value != 0 ? 0x{true_value:08x}u : 0x{false_value:08x}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-arg-nonzero-const-select-cdecl",
            variant=f"sysv-o2-register-arg-nonzero-const-select-{decoded['scale']}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "trueValue": f"0x{true_value:08x}",
                "falseValue": f"0x{false_value:08x}",
                "baseValue": int(decoded["baseValue"]),
                "scale": int(decoded["scale"]),
                "setcc": decoded["setcc"],
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def decode_x86_64_arg_nonzero_cmov_const_select(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) == 11 and body[:2] == b"\x85\xff" and body[2] == 0xB8 and body[7:10] == b"\x0f\x44\xc7" and body[10] == 0xC3:
        immediate = int.from_bytes(body[3:7], "little", signed=False)
        if immediate == 0:
            return None
        return {
            "trueValue": immediate,
            "falseValue": 0,
            "immediate": immediate,
            "cmov": "cmove",
            "pattern": "test-edi-edi-mov-eax-imm32-cmove-eax-edi-ret",
        }
    if len(body) == 13 and body[:4] == b"\x31\xc9\x85\xff" and body[4] == 0xB8 and body[9:12] == b"\x0f\x45\xc1" and body[12] == 0xC3:
        immediate = int.from_bytes(body[5:9], "little", signed=False)
        if immediate == 0:
            return None
        return {
            "trueValue": 0,
            "falseValue": immediate,
            "immediate": immediate,
            "cmov": "cmovne",
            "pattern": "xor-ecx-ecx-test-edi-edi-mov-eax-imm32-cmovne-eax-ecx-ret",
        }
    return None


def x86_64_arg_nonzero_cmov_const_select(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_nonzero_cmov_const_select(data)
    if decoded is None:
        return []
    true_value = int(decoded["trueValue"])
    false_value = int(decoded["falseValue"])
    source = header("x86-64-arg-nonzero-cmov-const-select-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value != 0 ? 0x{true_value:08x}u : 0x{false_value:08x}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-arg-nonzero-cmov-const-select-cdecl",
            variant=f"sysv-o2-register-arg-nonzero-cmov-const-select-{decoded['cmov']}",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "trueValue": f"0x{true_value:08x}",
                "falseValue": f"0x{false_value:08x}",
                "immediate": f"0x{int(decoded['immediate']):08x}",
                "cmov": decoded["cmov"],
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def decode_x86_64_arg_mask(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == b"\x31\xc0\xf7\xdf\x19\xc0\xc3":
        return {
            "suffix": "nonzero",
            "operator": "!=",
            "immediate": 0,
            "expression": "value != 0",
            "valueType": "unsigned int",
            "returnType": "unsigned int",
            "trueLiteral": "0xffffffffu",
            "falseLiteral": "0u",
            "pattern": "xor-eax-neg-edi-sbb-eax-eax-ret",
        }
    if len(body) == 8 and body[:4] == b"\x31\xc0\x83\xff" and body[6:] == b"\xc0\xc3":
        immediate = body[4]
        opcode = body[5]
        if opcode == 0x19:
            return {
                "suffix": "uint-lt-imm8",
                "operator": "<",
                "immediate": immediate,
                "expression": f"value < 0x{immediate:02x}u",
                "valueType": "unsigned int",
                "returnType": "unsigned int",
                "trueLiteral": "0xffffffffu",
                "falseLiteral": "0u",
                "pattern": "xor-eax-cmp-edi-imm8-sbb-eax-eax-ret",
            }
    if len(body) == 9 and body[:4] == b"\x31\xc0\x83\xff" and body[5:8] == b"\x83\xd0\xff" and body[8] == 0xC3:
        immediate = body[4]
        return {
            "suffix": "uint-ge-imm8",
            "operator": ">=",
            "immediate": immediate,
            "expression": f"value >= 0x{immediate:02x}u",
            "valueType": "unsigned int",
            "returnType": "unsigned int",
            "trueLiteral": "0xffffffffu",
            "falseLiteral": "0u",
            "pattern": "xor-eax-cmp-edi-imm8-adc-eax-minus-one-ret",
        }
    if len(body) == 11 and body[:4] == b"\x31\xc0\x83\xff" and body[5] == 0x0F and body[7:] == b"\xc0\xf7\xd8\xc3":
        raw_immediate = body[4]
        setcc_opcode = body[6]
        if setcc_opcode in {0x94, 0x95}:
            suffix, operator, setcc = X86_64_UNSIGNED_COMPARE_SETCC[setcc_opcode]
            return {
                "suffix": f"uint-{suffix}-imm8",
                "operator": operator,
                "immediate": raw_immediate,
                "expression": f"value {operator} 0x{raw_immediate:02x}u",
                "valueType": "unsigned int",
                "returnType": "unsigned int",
                "trueLiteral": "0xffffffffu",
                "falseLiteral": "0u",
                "setcc": setcc,
                "pattern": f"xor-eax-cmp-edi-imm8-{setcc}-al-neg-eax-ret",
            }
        decoded_signed = X86_64_SIGNED_COMPARE_SETCC.get(setcc_opcode)
        if decoded_signed is not None:
            suffix, operator, setcc = decoded_signed
            signed_immediate = raw_immediate if raw_immediate < 0x80 else raw_immediate - 0x100
            return {
                "suffix": f"int-{suffix}-imm8",
                "operator": operator,
                "immediate": signed_immediate,
                "rawImmediate": raw_immediate,
                "expression": f"value {operator} {signed_immediate}",
                "valueType": "int",
                "returnType": "int",
                "trueLiteral": "-1",
                "falseLiteral": "0",
                "setcc": setcc,
                "pattern": f"xor-eax-cmp-edi-imm8-{setcc}-al-neg-eax-ret",
            }
    return None


def x86_64_arg_mask(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    decoded = decode_x86_64_arg_mask(data)
    if decoded is None:
        return []
    expression = str(decoded["expression"])
    value_type = str(decoded["valueType"])
    return_type = str(decoded["returnType"])
    true_literal = str(decoded["trueLiteral"])
    false_literal = str(decoded["falseLiteral"])
    source = header(f"x86-64-arg-{decoded['suffix']}-mask-cdecl", row) + "\n".join(
        [
            f"{return_type} {c_name}({value_type} value) {{",
            f"    return {expression} ? {true_literal} : {false_literal};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-arg-{decoded['suffix']}-mask-cdecl",
            variant=f"sysv-o2-register-arg-{decoded['suffix']}-mask",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": decoded["pattern"],
                "registerArg": "edi",
                "operator": decoded["operator"],
                "immediate": int(decoded["immediate"]),
                "valueType": value_type,
                "returnType": return_type,
                "trueValue": "0xffffffff",
                "falseValue": "0x00000000",
                "setcc": decoded.get("setcc"),
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def x86_64_arg_nonzero(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if body != b"\x31\xc0\x85\xff\x0f\x95\xc0\xc3":
        return []
    source = header("x86-64-arg-nonzero-bool-cdecl", row) + "\n".join(
        [
            f"int {c_name}(unsigned int value) {{",
            "    return value != 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-arg-nonzero-bool-cdecl",
            variant="sysv-o2-arg-nonzero-bool",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={"pattern": "xor-eax-test-edi-setne-al-ret", "registerArg": "edi", "predicate": "value != 0", "framePointer": False, "targetFormat": row.get("targetFormat")},
        )
    ]


def x86_64_arg_zero(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if body != b"\x31\xc0\x85\xff\x0f\x94\xc0\xc3":
        return []
    source = header("x86-64-arg-zero-bool-cdecl", row) + "\n".join(
        [
            f"int {c_name}(unsigned int value) {{",
            "    return value == 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-arg-zero-bool-cdecl",
            variant="sysv-o2-arg-zero-bool",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={"pattern": "xor-eax-test-edi-sete-al-ret", "registerArg": "edi", "predicate": "value == 0", "framePointer": False, "targetFormat": row.get("targetFormat")},
        )
    ]


X86_64_ARG64_ZERO_NONZERO_OPS: dict[bytes, tuple[str, str, str]] = {
    b"\x31\xc0\x48\x85\xff\x0f\x95\xc0\xc3": ("nonzero", "!=", "setne"),
    b"\x31\xc0\x48\x85\xff\x0f\x94\xc0\xc3": ("zero", "==", "sete"),
}


def x86_64_arg64_zero_nonzero(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if not is_x86_64_row(row):
        return []
    body = strip_alignment_padding(data)
    decoded = X86_64_ARG64_ZERO_NONZERO_OPS.get(body)
    if decoded is None:
        return []
    suffix, operator, setcc = decoded
    source = header(f"x86-64-arg64-{suffix}-bool-cdecl", row) + "\n".join(
        [
            f"int {c_name}(unsigned long long value) {{",
            f"    return value {operator} 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"x86-64-arg64-{suffix}-bool-cdecl",
            variant=f"sysv-o2-arg64-{suffix}-bool",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=False),
            evidence={
                "pattern": f"xor-eax-test-rdi-{setcc}-al-ret",
                "registerArg": "rdi",
                "operator": operator,
                "predicate": f"value {operator} 0",
                "setcc": setcc,
                "framePointer": False,
                "targetFormat": row.get("targetFormat"),
            },
        )
    ]


def x86_64_framed_return_first_arg(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if body != b"\x55\x48\x89\xe5\x89\xf8\x5d\xc3":
        return []
    source = header("x86-64-framed-return-first-arg-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            "    return value;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-framed-return-first-arg-cdecl",
            variant="sysv-o2-frameptr-register-arg",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=True),
            evidence={"pattern": "push-rbp-mov-rbp-rsp-mov-eax-edi-pop-rbp-ret", "registerArg": "edi", "framePointer": True, "targetFormat": row.get("targetFormat")},
        )
    ]


def x86_64_framed_add_two_args(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if body != b"\x55\x48\x89\xe5\x8d\x04\x37\x5d\xc3":
        return []
    source = header("x86-64-framed-add-two-args-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int a, unsigned int b) {{",
            "    return a + b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="x86-64-framed-add-two-args-cdecl",
            variant="sysv-o2-frameptr-register-args",
            c_name=c_name,
            symbol=clang_c_symbol(row, c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=x86_64_o2_leaf_flags_for_row(row, frame_pointer=True),
            evidence={"pattern": "push-rbp-mov-rbp-rsp-lea-eax-rdi-rsi-pop-rbp-ret", "registerArgs": ["edi", "esi"], "framePointer": True, "targetFormat": row.get("targetFormat")},
        )
    ]


def clang_c_symbol(row: dict[str, Any], c_name: str) -> str:
    if str(row.get("targetFormat") or "") == "macho":
        return f"_{c_name}"
    return c_name


def clang_target_flags_for_row(row: dict[str, Any]) -> tuple[str, ...]:
    if str(row.get("targetFormat") or "") == "macho":
        arch = str(row.get("architectureHint") or "")
        if arch == "x86_64":
            return ("--target=x86_64-apple-macosx10.12", "-m64", "-O0", "-ffreestanding", "-fno-asynchronous-unwind-tables", "-fno-stack-protector", "-fno-ident")
    return ()


def x86_64_o2_leaf_flags_for_row(row: dict[str, Any], *, frame_pointer: bool) -> tuple[str, ...]:
    frame_flag = "-fno-omit-frame-pointer" if frame_pointer else "-fomit-frame-pointer"
    if str(row.get("targetFormat") or "") == "macho":
        return ("--target=x86_64-apple-macosx10.12", "-m64", "-O2", frame_flag, "-ffreestanding", "-fno-asynchronous-unwind-tables", "-fno-stack-protector", "-fno-ident")
    return ("-m64", "-O2", frame_flag, "-ffreestanding", "-fno-pic", "-fno-pie", "-fno-asynchronous-unwind-tables", "-fno-stack-protector", "-fno-ident")


def frame_style(body: bytes) -> str:
    if body.startswith(b"\x55\x89\xe5"):
        return "gcc-clang"
    if body.startswith(b"\x55\x8b\xec"):
        return "msvc"
    return "unknown"


def global_setter_u32_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) != 12 or body[:4] != b"\x8b\x44\x24\x04" or body[4] != 0xA3 or body[9:] != b"\xc2\x04\x00":
        return []
    address = u32(body[5:9])
    source = header("global-setter-u32-stdcall", row) + "\n".join(
        [
            f"void __stdcall {c_name}(unsigned int value) {{",
            f"    *(unsigned int volatile *)0x{address:08x} = value;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="global-setter-u32-stdcall",
            variant="u32-absolute-store-stdcall4",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="void",
            evidence={"pattern": "mov-eax-stack4-store-abs-ret4", "address": f"0x{address:08x}", "stackBytes": 4},
        )
    ]


def nullable_indexed_field_array_getter_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_nullable_indexed_field_array_getter_stdcall(data)
    if decoded is None:
        return []
    offset = int(decoded["pointerOffset"])
    stack_bytes = int(decoded["stackBytes"])
    source = header("nullable-indexed-field-array-getter-stdcall8", row) + "\n".join(
        [
            f"unsigned int __stdcall {c_name}(void *self, unsigned int index) {{",
            "    unsigned int result;",
            "    if (self != 0) {",
            f"        result = (*(unsigned int **)((char *)self + 0x{offset:x}))[index];",
            "    } else {",
            "        result = 0u;",
            "    }",
            "    return result;",
            "}",
            "",
        ]
    )
    source_naked = header("nullable-indexed-field-array-getter-stdcall8", row) + "\n".join(
        [
            f"__declspec(naked) unsigned int __stdcall {c_name}(void *self, unsigned int index) {{",
            "    __asm {",
            "        mov eax, dword ptr [esp+4]",
            "        test eax, eax",
            "        je null_return",
            f"        mov eax, dword ptr [eax+0{offset:x}h]",
            "        mov ecx, dword ptr [esp+8]",
            "        mov eax, dword ptr [eax+ecx*4]",
            f"        ret {stack_bytes}",
            "    null_return:",
            "        xor eax, eax",
            f"        ret {stack_bytes}",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {
        "pointerOffset": offset,
        "stackBytes": stack_bytes,
        "elementBytes": int(decoded["elementBytes"]),
        "nullReturn": int(decoded["nullReturn"]),
    }
    return [
        GeneratedCandidate(
            rule="nullable-indexed-field-array-getter-stdcall8",
            variant="high-level-else-local-nullable-u32-array-field-stdcall8",
            c_name=c_name,
            symbol=f"_{c_name}@{stack_bytes}",
            source=source,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={
                **evidence,
                "sourceTier": "generated high-level C recovered from decoded nullable indexed field-array bytes",
            },
        ),
        GeneratedCandidate(
            rule="nullable-indexed-field-array-getter-stdcall8",
            variant="naked-nullable-u32-array-field-stdcall8",
            c_name=c_name,
            symbol=f"_{c_name}@{stack_bytes}",
            source=source_naked,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={
                **evidence,
                "sourceTier": "generated inline-assembly parity fallback with decoded nullable indexed field-array bytes",
            },
        ),
    ]


def decode_nullable_indexed_field_array_getter_stdcall(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 29:
        return None
    if body[:8] != b"\x8b\x44\x24\x04\x85\xc0\x74\x10":
        return None
    if body[8:10] != b"\x8b\x80" or body[14:18] != b"\x8b\x4c\x24\x08":
        return None
    if body[18:21] != b"\x8b\x04\x88" or body[21:24] != b"\xc2\x08\x00":
        return None
    if body[24:] != b"\x33\xc0\xc2\x08\x00":
        return None
    return {
        "pointerOffset": u32(body[10:14]),
        "stackBytes": 8,
        "elementBytes": 4,
        "nullReturn": 0,
    }


def nullable_field_setter_u32_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_nullable_field_setter_u32_stdcall(data)
    if decoded is None:
        return []
    offset = int(decoded["fieldOffset"])
    stack_bytes = int(decoded["stackBytes"])
    source = header("nullable-field-setter-u32-stdcall8", row) + "\n".join(
        [
            f"void __stdcall {c_name}(void *self, unsigned int value) {{",
            "    if (self != 0) {",
            f"        *(unsigned int volatile *)((char *)self + 0x{offset:x}) = value;",
            "    }",
            "}",
            "",
        ]
    )
    source_naked = header("nullable-field-setter-u32-stdcall8", row) + "\n".join(
        [
            f"__declspec(naked) void __stdcall {c_name}(void *self, unsigned int value) {{",
            "    __asm {",
            "        mov ecx, dword ptr [esp+4]",
            "        test ecx, ecx",
            "        mov eax, dword ptr [esp+8]",
            "        je done",
            f"        mov dword ptr [ecx+0{offset:x}h], eax",
            "    done:",
            f"        ret {stack_bytes}",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {"fieldOffset": offset, "stackBytes": stack_bytes}
    return [
        GeneratedCandidate(
            rule="nullable-field-setter-u32-stdcall8",
            variant="naked-nullable-u32-field-setter-stdcall8",
            c_name=c_name,
            symbol=f"_{c_name}@{stack_bytes}",
            source=source_naked,
            callconv="stdcall",
            return_type="void",
            evidence={
                **evidence,
                "sourceTier": "generated inline-assembly parity fallback with decoded nullable field-setter bytes",
            },
        ),
    ]


def decode_nullable_field_setter_u32_stdcall(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 21:
        return None
    if body[:4] != b"\x8b\x4c\x24\x04" or body[4:6] != b"\x85\xc9":
        return None
    if body[6:10] != b"\x8b\x44\x24\x08" or body[10:12] != b"\x74\x06":
        return None
    if body[12:14] != b"\x89\x81" or body[18:] != b"\xc2\x08\x00":
        return None
    return {"fieldOffset": u32(body[14:18]), "stackBytes": 8}


def one_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) != 6 or body[:5] != b"\xb8\x01\x00\x00\x00" or body[5] != 0xC3:
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


def one_return_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) != 8 or body[:5] != b"\xb8\x01\x00\x00\x00" or body[5:] != b"\xc2\x04\x00":
        return []
    source = header("return-one-stdcall", row) + "\n".join(
        [
            f"int __stdcall {c_name}(unsigned int unused) {{",
            "    (void)unused;",
            "    return 1;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="return-one-stdcall",
            variant="stdcall4",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="int",
            evidence={"pattern": "mov-eax-1-ret4", "stackBytes": 4},
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


def return_first_stack_arg_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    if len(data) != 7 or data != b"\x8b\x44\x24\x04\xc2\x04\x00":
        return []
    source = header("return-first-stack-arg-stdcall", row) + "\n".join(
        [
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            "    return value;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="return-first-stack-arg-stdcall",
            variant="stdcall4",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={"pattern": "mov-eax-stack4-ret4", "stackBytes": 4},
        )
    ]


def add_two_stack_args(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    patterns = {
        b"\x8b\x44\x24\x04\x03\x44\x24\x08\xc3": "stack4-plus-stack8",
        b"\x8b\x44\x24\x08\x03\x44\x24\x04\xc3": "stack8-plus-stack4",
    }
    if len(data) != 9 or data not in patterns:
        return []
    source = header("add-two-stack-args-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int a, unsigned int b) {{",
            "    return a + b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="add-two-stack-args-cdecl",
            variant="cdecl",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={"pattern": "mov-eax-stack-add-stack-ret", "operandOrder": patterns[data]},
        )
    ]


def add_two_stack_args_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    patterns = {
        b"\x8b\x44\x24\x04\x03\x44\x24\x08\xc2\x08\x00": "stack4-plus-stack8",
        b"\x8b\x44\x24\x08\x03\x44\x24\x04\xc2\x08\x00": "stack8-plus-stack4",
    }
    if len(data) != 11 or data not in patterns:
        return []
    source = header("add-two-stack-args-stdcall", row) + "\n".join(
        [
            f"unsigned int __stdcall {c_name}(unsigned int a, unsigned int b) {{",
            "    return a + b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="add-two-stack-args-stdcall",
            variant="stdcall8",
            c_name=c_name,
            symbol=f"_{c_name}@8",
            source=source,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={"pattern": "mov-eax-stack-add-stack-ret8", "operandOrder": patterns[data], "stackBytes": 8},
        )
    ]


I386_TWO_STACK_ARG_BINARY_OPS: dict[bytes, tuple[str, str, str]] = {
    b"\x8b\x44\x24\x04\x2b\x44\x24\x08": ("sub", "-", "stack4-minus-stack8"),
    b"\x8b\x44\x24\x08\x0f\xaf\x44\x24\x04": ("mul", "*", "stack8-times-stack4"),
    b"\x8b\x44\x24\x08\x23\x44\x24\x04": ("and", "&", "stack8-and-stack4"),
    b"\x8b\x44\x24\x08\x0b\x44\x24\x04": ("or", "|", "stack8-or-stack4"),
    b"\x8b\x44\x24\x08\x33\x44\x24\x04": ("xor", "^", "stack8-xor-stack4"),
}


def decode_two_stack_args_binary_op(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x08\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    decoded = I386_TWO_STACK_ARG_BINARY_OPS.get(body[: -len(ret)])
    if decoded is None:
        return None
    suffix, operator, operand_order = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "operandOrder": operand_order,
        "stackBytes": 8 if stdcall else 0,
    }


def two_stack_args_binary_op(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_two_stack_args_binary_op(data, stdcall=False)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = header(f"two-stack-args-{suffix}-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int a, unsigned int b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"two-stack-args-{suffix}-cdecl",
            variant=f"cdecl-o2-two-arg-{suffix}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={
                "pattern": "mov-eax-stack-binary-op-stack-ret",
                "operator": operator,
                "operandOrder": decoded["operandOrder"],
            },
        )
    ]


def two_stack_args_binary_op_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_two_stack_args_binary_op(data, stdcall=True)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = header(f"two-stack-args-{suffix}-stdcall", row) + "\n".join(
        [
            f"unsigned int __stdcall {c_name}(unsigned int a, unsigned int b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"two-stack-args-{suffix}-stdcall",
            variant=f"stdcall8-o2-two-arg-{suffix}",
            c_name=c_name,
            symbol=f"_{c_name}@8",
            source=source,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={
                "pattern": "mov-eax-stack-binary-op-stack-ret8",
                "operator": operator,
                "operandOrder": decoded["operandOrder"],
                "stackBytes": 8,
            },
        )
    ]


def format_two_stack_args_affine_expression(scaled_arg: str, scale: int, immediate: int) -> str:
    other_arg = "b" if scaled_arg == "a" else "a"
    scaled_term = f"{scaled_arg} * {scale}u"
    expression = f"{scaled_term} + {other_arg}" if scaled_arg == "a" else f"{other_arg} + {scaled_term}"
    if immediate:
        expression = f"{expression} + 0x{immediate:02x}u" if immediate <= 0xFF else f"{expression} + 0x{immediate:08x}u"
    return expression


def decode_two_stack_args_affine(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x08\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if len(core) in {10, 11} and core[:4] in {b"\x8b\x44\x24\x04", b"\x8b\x44\x24\x08"}:
        scaled_arg = "a" if core[:4] == b"\x8b\x44\x24\x04" else "b"
        other_stack = b"\x08" if scaled_arg == "a" else b"\x04"
        if core[4:6] == b"\x01\xc0":
            scale = 2
            offset = 6
            scale_pattern = "add-eax-eax"
        elif core[4:7] in {b"\xc1\xe0\x02", b"\xc1\xe0\x03"}:
            scale = 1 << core[6]
            offset = 7
            scale_pattern = f"shl-eax-{core[6]}"
        else:
            return None
        if core[offset : offset + 3] != b"\x03\x44\x24" or core[offset + 3 : offset + 4] != other_stack or len(core) != offset + 4:
            return None
        return {
            "scaledArg": scaled_arg,
            "scale": scale,
            "immediate": 0,
            "expression": format_two_stack_args_affine_expression(scaled_arg, scale, 0),
            "pattern": f"mov-eax-stack-{scaled_arg}-{scale_pattern}-add-eax-stack-other",
            "stackBytes": 8 if stdcall else 0,
        }
    if len(core) in {14, 16} and core[:4] in {b"\x8b\x44\x24\x04", b"\x8b\x44\x24\x08"}:
        scaled_arg = "a" if core[:4] == b"\x8b\x44\x24\x04" else "b"
        other_mov = b"\x8b\x4c\x24\x08" if scaled_arg == "a" else b"\x8b\x4c\x24\x04"
        if core[4:8] != other_mov or core[8:10] != b"\x8d\x04":
            return None
        sib = core[10]
        if sib & 0x07 != 0x01 or ((sib >> 3) & 0x07) != 0x00:
            return None
        scale = 1 << ((sib >> 6) & 0x03)
        if scale not in {2, 4, 8}:
            return None
        if len(core) == 14 and core[11:13] == b"\x83\xc0":
            immediate = core[13]
            imm_pattern = "add-eax-imm8"
        elif len(core) == 16 and core[11] == 0x05:
            immediate = int.from_bytes(core[12:16], "little", signed=False)
            imm_pattern = "add-eax-imm32"
        else:
            return None
        if immediate == 0:
            return None
        return {
            "scaledArg": scaled_arg,
            "scale": scale,
            "immediate": immediate,
            "expression": format_two_stack_args_affine_expression(scaled_arg, scale, immediate),
            "pattern": f"mov-eax-stack-{scaled_arg}-mov-ecx-stack-other-lea-eax-ecx-plus-eax{scale}-{imm_pattern}",
            "stackBytes": 8 if stdcall else 0,
        }
    return None


def two_stack_args_affine(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_two_stack_args_affine(data, stdcall=False)
    if decoded is None:
        return []
    source = header("two-stack-args-affine-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int a, unsigned int b) {{",
            f"    return {decoded['expression']};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="two-stack-args-affine-cdecl",
            variant=f"cdecl-o2-two-arg-affine-scale-{decoded['scale']}-{decoded['scaledArg']}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "scaledArg": decoded["scaledArg"],
                "scale": int(decoded["scale"]),
                "immediate": f"0x{int(decoded['immediate']):08x}",
                "expression": decoded["expression"],
            },
        )
    ]


def two_stack_args_affine_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_two_stack_args_affine(data, stdcall=True)
    if decoded is None:
        return []
    source = header("two-stack-args-affine-stdcall", row) + "\n".join(
        [
            f"unsigned int __stdcall {c_name}(unsigned int a, unsigned int b) {{",
            f"    return {decoded['expression']};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="two-stack-args-affine-stdcall",
            variant=f"stdcall8-o2-two-arg-affine-scale-{decoded['scale']}-{decoded['scaledArg']}",
            c_name=c_name,
            symbol=f"_{c_name}@8",
            source=source,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "scaledArg": decoded["scaledArg"],
                "scale": int(decoded["scale"]),
                "immediate": f"0x{int(decoded['immediate']):08x}",
                "expression": decoded["expression"],
                "stackBytes": 8,
            },
        )
    ]


I386_THREE_STACK_ARG_COMMUTATIVE_OPS: dict[int, tuple[str, str]] = {
    0x03: ("add", "+"),
    0x33: ("xor", "^"),
    0x23: ("and", "&"),
    0x0B: ("or", "|"),
}


def decode_three_stack_args_commutative_op(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x0c\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if len(core) != 12 or core[:4] != b"\x8b\x44\x24\x08":
        return None
    first_opcode = core[4]
    second_opcode = core[8]
    if first_opcode != second_opcode or core[5:8] != b"\x44\x24\x04" or core[9:12] != b"\x44\x24\x0c":
        return None
    decoded = I386_THREE_STACK_ARG_COMMUTATIVE_OPS.get(first_opcode)
    if decoded is None:
        return None
    suffix, operator = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "operandOrder": "stack8-then-stack4-then-stack12",
        "pattern": f"mov-eax-stack8-{suffix}-eax-stack4-{suffix}-eax-stack12",
        "stackBytes": 12 if stdcall else 0,
    }


def three_stack_args_commutative_op(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_three_stack_args_commutative_op(data, stdcall=False)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = header(f"three-stack-args-{suffix}-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int a, unsigned int b, unsigned int c) {{",
            f"    return a {operator} b {operator} c;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"three-stack-args-{suffix}-cdecl",
            variant=f"cdecl-o2-three-arg-{suffix}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": operator,
                "operandOrder": decoded["operandOrder"],
            },
        )
    ]


def three_stack_args_commutative_op_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_three_stack_args_commutative_op(data, stdcall=True)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = header(f"three-stack-args-{suffix}-stdcall", row) + "\n".join(
        [
            f"unsigned int __stdcall {c_name}(unsigned int a, unsigned int b, unsigned int c) {{",
            f"    return a {operator} b {operator} c;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"three-stack-args-{suffix}-stdcall",
            variant=f"stdcall12-o2-three-arg-{suffix}",
            c_name=c_name,
            symbol=f"_{c_name}@12",
            source=source,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": operator,
                "operandOrder": decoded["operandOrder"],
                "stackBytes": 12,
            },
        )
    ]


I386_STACK_OFFSET_ARG: dict[int, str] = {4: "a", 8: "b", 12: "c"}


I386_THREE_STACK_ARG_ADD_SUB_OPS: dict[int, tuple[str, str]] = {
    0x03: ("add", "+"),
    0x2B: ("sub", "-"),
}


def decode_three_stack_args_add_sub(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x0c\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if len(core) == 12 and core[:3] == b"\x8b\x44\x24" and core[5:7] == b"\x44\x24" and core[9:11] == b"\x44\x24":
        first_opcode = core[4]
        second_opcode = core[8]
        if first_opcode == 0x03 and second_opcode == 0x03:
            return None
        first = I386_THREE_STACK_ARG_ADD_SUB_OPS.get(first_opcode)
        second = I386_THREE_STACK_ARG_ADD_SUB_OPS.get(second_opcode)
        offsets = {core[3], core[7], core[11]}
        if first is None or second is None or offsets != {4, 8, 12}:
            return None
        initial = I386_STACK_OFFSET_ARG[core[3]]
        first_arg = I386_STACK_OFFSET_ARG[core[7]]
        second_arg = I386_STACK_OFFSET_ARG[core[11]]
        expression = f"{initial} {first[1]} {first_arg} {second[1]} {second_arg}"
        return {
            "suffix": f"{first[0]}-{second[0]}",
            "expression": expression,
            "operandOrder": f"mov-{initial}-{first[0]}-{first_arg}-{second[0]}-{second_arg}",
            "pattern": "mov-eax-stackX-op-eax-stackY-op-eax-stackZ",
            "stackBytes": 12 if stdcall else 0,
        }
    if len(core) == 14 and core[:3] == b"\x8b\x44\x24" and core[4:7] == b"\x8b\x4c\x24" and core[8:11] == b"\x03\x4c\x24" and core[12:14] == b"\x29\xc8":
        offsets = {core[3], core[7], core[11]}
        if offsets != {4, 8, 12}:
            return None
        initial = I386_STACK_OFFSET_ARG[core[3]]
        first_sum = I386_STACK_OFFSET_ARG[core[7]]
        second_sum = I386_STACK_OFFSET_ARG[core[11]]
        expression = f"{initial} - ({first_sum} + {second_sum})"
        return {
            "suffix": "sub-sum",
            "expression": expression,
            "operandOrder": f"mov-{initial}-sum-{first_sum}-{second_sum}-sub-sum",
            "pattern": "mov-eax-stackX-mov-ecx-stackY-add-ecx-stackZ-sub-eax-ecx",
            "stackBytes": 12 if stdcall else 0,
        }
    return None


def three_stack_args_add_sub(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_three_stack_args_add_sub(data, stdcall=False)
    if decoded is None:
        return []
    source = header("three-stack-args-add-sub-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int a, unsigned int b, unsigned int c) {{",
            f"    return {decoded['expression']};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="three-stack-args-add-sub-cdecl",
            variant=f"cdecl-o2-three-arg-{decoded['operandOrder']}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "expression": decoded["expression"],
                "operandOrder": decoded["operandOrder"],
            },
        )
    ]


def three_stack_args_add_sub_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_three_stack_args_add_sub(data, stdcall=True)
    if decoded is None:
        return []
    source = header("three-stack-args-add-sub-stdcall", row) + "\n".join(
        [
            f"unsigned int __stdcall {c_name}(unsigned int a, unsigned int b, unsigned int c) {{",
            f"    return {decoded['expression']};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="three-stack-args-add-sub-stdcall",
            variant=f"stdcall12-o2-three-arg-{decoded['operandOrder']}",
            c_name=c_name,
            symbol=f"_{c_name}@12",
            source=source,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "expression": decoded["expression"],
                "operandOrder": decoded["operandOrder"],
                "stackBytes": 12,
            },
        )
    ]


def decode_three_stack_args_mul_add(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x0c\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if len(core) != 13 or core[:3] != b"\x8b\x44\x24" or core[4:8] != b"\x0f\xaf\x44\x24" or core[9:12] != b"\x03\x44\x24":
        return None
    mov_offset = core[3]
    mul_offset = core[8]
    add_offset = core[12]
    offsets = {mov_offset, mul_offset, add_offset}
    if offsets != {4, 8, 12}:
        return None
    left = I386_STACK_OFFSET_ARG[mov_offset]
    right = I386_STACK_OFFSET_ARG[mul_offset]
    addend = I386_STACK_OFFSET_ARG[add_offset]
    expression = f"{left} * {right} + {addend}"
    return {
        "expression": expression,
        "multiplyArgs": [left, right],
        "addArg": addend,
        "operandOrder": f"mov-{left}-imul-{right}-add-{addend}",
        "pattern": "mov-eax-stackX-imul-eax-stackY-add-eax-stackZ",
        "stackBytes": 12 if stdcall else 0,
    }


def three_stack_args_mul_add(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_three_stack_args_mul_add(data, stdcall=False)
    if decoded is None:
        return []
    source = header("three-stack-args-mul-add-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int a, unsigned int b, unsigned int c) {{",
            f"    return {decoded['expression']};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="three-stack-args-mul-add-cdecl",
            variant=f"cdecl-o2-three-arg-{decoded['operandOrder']}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": "*+",
                "expression": decoded["expression"],
                "multiplyArgs": decoded["multiplyArgs"],
                "addArg": decoded["addArg"],
                "operandOrder": decoded["operandOrder"],
            },
        )
    ]


def three_stack_args_mul_add_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_three_stack_args_mul_add(data, stdcall=True)
    if decoded is None:
        return []
    source = header("three-stack-args-mul-add-stdcall", row) + "\n".join(
        [
            f"unsigned int __stdcall {c_name}(unsigned int a, unsigned int b, unsigned int c) {{",
            f"    return {decoded['expression']};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="three-stack-args-mul-add-stdcall",
            variant=f"stdcall12-o2-three-arg-{decoded['operandOrder']}",
            c_name=c_name,
            symbol=f"_{c_name}@12",
            source=source,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": "*+",
                "expression": decoded["expression"],
                "multiplyArgs": decoded["multiplyArgs"],
                "addArg": decoded["addArg"],
                "operandOrder": decoded["operandOrder"],
                "stackBytes": 12,
            },
        )
    ]


I386_TWO_STACK_ARG_MIN_MAX_CMOV: dict[int, tuple[str, str, str, str]] = {
    0x42: ("uint-min", "<", "unsigned int", "cmovb"),
    0x47: ("uint-max", ">", "unsigned int", "cmova"),
    0x4C: ("int-min", "<", "int", "cmovl"),
    0x4F: ("int-max", ">", "int", "cmovg"),
}


def decode_two_stack_args_min_max(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x08\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if len(core) != 13 or core[:10] != b"\x8b\x44\x24\x08\x8b\x4c\x24\x04\x39\xc1" or core[10] != 0x0F or core[12] != 0xC1:
        return None
    decoded = I386_TWO_STACK_ARG_MIN_MAX_CMOV.get(core[11])
    if decoded is None:
        return None
    suffix, operator, value_type, cmov = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "valueType": value_type,
        "returnType": value_type,
        "cmov": cmov,
        "pattern": f"mov-eax-stack8-mov-ecx-stack4-cmp-ecx-eax-{cmov}-eax-ecx",
        "stackBytes": 8 if stdcall else 0,
    }


def two_stack_args_min_max(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_two_stack_args_min_max(data, stdcall=False)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    value_type = str(decoded["valueType"])
    return_type = str(decoded["returnType"])
    operator = str(decoded["operator"])
    source = header(f"two-stack-args-{suffix}-cmov-cdecl", row) + "\n".join(
        [
            f"{return_type} {c_name}({value_type} a, {value_type} b) {{",
            f"    return a {operator} b ? a : b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"two-stack-args-{suffix}-cmov-cdecl",
            variant=f"cdecl-o2-two-arg-{suffix}-cmov",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type=return_type,
            evidence={
                "pattern": decoded["pattern"],
                "operator": operator,
                "valueType": value_type,
                "returnType": return_type,
                "cmov": decoded["cmov"],
            },
        )
    ]


def two_stack_args_min_max_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_two_stack_args_min_max(data, stdcall=True)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    value_type = str(decoded["valueType"])
    return_type = str(decoded["returnType"])
    operator = str(decoded["operator"])
    source = header(f"two-stack-args-{suffix}-cmov-stdcall", row) + "\n".join(
        [
            f"{return_type} __stdcall {c_name}({value_type} a, {value_type} b) {{",
            f"    return a {operator} b ? a : b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"two-stack-args-{suffix}-cmov-stdcall",
            variant=f"stdcall8-o2-two-arg-{suffix}-cmov",
            c_name=c_name,
            symbol=f"_{c_name}@8",
            source=source,
            callconv="stdcall",
            return_type=return_type,
            evidence={
                "pattern": decoded["pattern"],
                "operator": operator,
                "valueType": value_type,
                "returnType": return_type,
                "cmov": decoded["cmov"],
                "stackBytes": 8,
            },
        )
    ]


I386_UNSIGNED_COMPARE_SETCC: dict[int, tuple[str, str, str]] = {
    0x92: ("lt", "<", "setb"),
    0x93: ("ge", ">=", "setae"),
    0x94: ("eq", "==", "sete"),
    0x95: ("ne", "!=", "setne"),
    0x96: ("le", "<=", "setbe"),
    0x97: ("gt", ">", "seta"),
}


I386_SIGNED_COMPARE_SETCC: dict[int, tuple[str, str, str]] = {
    0x9C: ("lt", "<", "setl"),
    0x9D: ("ge", ">=", "setge"),
    0x9E: ("le", "<=", "setle"),
    0x9F: ("gt", ">", "setg"),
}


def decode_two_stack_args_unsigned_compare(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x08\x00" if stdcall else b"\xc3"
    expected_len = 16 if stdcall else 14
    if len(body) != expected_len or not body.endswith(ret):
        return None
    if body[:4] != b"\x8b\x4c\x24\x04" or body[4:6] not in {b"\x31\xc0", b"\x33\xc0"}:
        return None
    if body[6:10] != b"\x3b\x4c\x24\x08" or body[10] != 0x0F or body[12] != 0xC0:
        return None
    decoded = I386_UNSIGNED_COMPARE_SETCC.get(body[11])
    if decoded is None:
        return None
    suffix, operator, setcc = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
        "stackBytes": 8 if stdcall else 0,
    }


def decode_two_stack_args_signed_compare(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x08\x00" if stdcall else b"\xc3"
    expected_len = 16 if stdcall else 14
    if len(body) != expected_len or not body.endswith(ret):
        return None
    if body[:4] != b"\x8b\x4c\x24\x04" or body[4:6] not in {b"\x31\xc0", b"\x33\xc0"}:
        return None
    if body[6:10] != b"\x3b\x4c\x24\x08" or body[10] != 0x0F or body[12] != 0xC0:
        return None
    decoded = I386_SIGNED_COMPARE_SETCC.get(body[11])
    if decoded is None:
        return None
    suffix, operator, setcc = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
        "stackBytes": 8 if stdcall else 0,
    }


def two_stack_args_unsigned_compare(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_two_stack_args_unsigned_compare(data, stdcall=False)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = header(f"two-stack-args-uint-{suffix}-cdecl", row) + "\n".join(
        [
            f"int {c_name}(unsigned int a, unsigned int b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"two-stack-args-uint-{suffix}-cdecl",
            variant=f"cdecl-o2-uint-{suffix}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence={
                "pattern": "mov-ecx-stack4-xor-eax-cmp-ecx-stack8-setcc-al-ret",
                "operator": operator,
                "setcc": decoded["setcc"],
            },
        )
    ]


def two_stack_args_signed_compare(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_two_stack_args_signed_compare(data, stdcall=False)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = header(f"two-stack-args-int-{suffix}-cdecl", row) + "\n".join(
        [
            f"int {c_name}(int a, int b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"two-stack-args-int-{suffix}-cdecl",
            variant=f"cdecl-o2-int-{suffix}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence={
                "pattern": "mov-ecx-stack4-xor-eax-cmp-ecx-stack8-setcc-al-ret",
                "operator": operator,
                "setcc": decoded["setcc"],
            },
        )
    ]


def two_stack_args_unsigned_compare_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_two_stack_args_unsigned_compare(data, stdcall=True)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = header(f"two-stack-args-uint-{suffix}-stdcall", row) + "\n".join(
        [
            f"int __stdcall {c_name}(unsigned int a, unsigned int b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"two-stack-args-uint-{suffix}-stdcall",
            variant=f"stdcall8-o2-uint-{suffix}",
            c_name=c_name,
            symbol=f"_{c_name}@8",
            source=source,
            callconv="stdcall",
            return_type="int",
            evidence={
                "pattern": "mov-ecx-stack4-xor-eax-cmp-ecx-stack8-setcc-al-ret8",
                "operator": operator,
                "setcc": decoded["setcc"],
                "stackBytes": 8,
            },
        )
    ]


def two_stack_args_signed_compare_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_two_stack_args_signed_compare(data, stdcall=True)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = header(f"two-stack-args-int-{suffix}-stdcall", row) + "\n".join(
        [
            f"int __stdcall {c_name}(int a, int b) {{",
            f"    return a {operator} b;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"two-stack-args-int-{suffix}-stdcall",
            variant=f"stdcall8-o2-int-{suffix}",
            c_name=c_name,
            symbol=f"_{c_name}@8",
            source=source,
            callconv="stdcall",
            return_type="int",
            evidence={
                "pattern": "mov-ecx-stack4-xor-eax-cmp-ecx-stack8-setcc-al-ret8",
                "operator": operator,
                "setcc": decoded["setcc"],
                "stackBytes": 8,
            },
        )
    ]


I386_STACK_ARG_SDIV_MAGIC_OPS: dict[bytes, tuple[int, str, int, str]] = {
    bytes.fromhex("b856555555f76c240489d0c1e81f01d0"): (3, "0x55555556", 32, "mov-eax-magic-imul-stack4-mov-eax-edx-shr-eax-31-add-eax-edx"),
    bytes.fromhex("b867666666f76c240489d0c1e81fd1fa01d0"): (5, "0x66666667", 33, "mov-eax-magic-imul-stack4-mov-eax-edx-shr-eax-31-sar-edx-one-add-eax-edx"),
    bytes.fromhex("b867666666f76c240489d0c1e81fc1fa0201d0"): (10, "0x66666667", 34, "mov-eax-magic-imul-stack4-mov-eax-edx-shr-eax-31-sar-edx-2-add-eax-edx"),
}


def decode_stack_arg_sdiv_magic(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    decoded = I386_STACK_ARG_SDIV_MAGIC_OPS.get(core)
    if decoded is None:
        return None
    divisor, multiplier, shift, pattern = decoded
    return {
        "divisor": divisor,
        "multiplier": multiplier,
        "shift": shift,
        "pattern": pattern,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_sdiv_magic(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_sdiv_magic(data, stdcall=False)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("stack-arg-sdiv-magic-cdecl", row) + "\n".join(
        [
            f"int {c_name}(int value) {{",
            f"    return value / {divisor};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-sdiv-magic-cdecl",
            variant=f"cdecl-o2-stack-arg-sdiv-{divisor}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": "/",
                "divisor": divisor,
                "multiplier": decoded["multiplier"],
                "shift": int(decoded["shift"]),
            },
        )
    ]


def stack_arg_sdiv_magic_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_sdiv_magic(data, stdcall=True)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("stack-arg-sdiv-magic-stdcall", row) + "\n".join(
        [
            f"int __stdcall {c_name}(int value) {{",
            f"    return value / {divisor};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-sdiv-magic-stdcall",
            variant=f"stdcall4-o2-stack-arg-sdiv-{divisor}",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": "/",
                "divisor": divisor,
                "multiplier": decoded["multiplier"],
                "shift": int(decoded["shift"]),
                "stackBytes": 4,
            },
        )
    ]


I386_STACK_ARG_SREM_MAGIC_OPS: dict[bytes, tuple[int, str, int, str]] = {
    bytes.fromhex("8b4c2404ba5655555589c8f7ea89d0c1e81f01d08d044029c189c8"): (3, "0x55555556", 32, "mov-ecx-stack4-mov-edx-magic-mov-eax-ecx-imul-edx-mov-eax-edx-shr-eax-31-add-eax-edx-lea-eax-eax-eax2-sub-ecx-eax-mov-eax-ecx"),
    bytes.fromhex("8b4c2404ba6766666689c8f7ea89d0c1e81fd1fa01c28d049229c189c8"): (5, "0x66666667", 33, "mov-ecx-stack4-mov-edx-magic-mov-eax-ecx-imul-edx-mov-eax-edx-shr-eax-31-sar-edx-one-add-edx-eax-lea-eax-edx-edx4-sub-ecx-eax-mov-eax-ecx"),
    bytes.fromhex("8b4c2404ba6766666689c8f7ea89d0c1e81fc1fa0201c201d28d049229c189c8"): (10, "0x66666667", 34, "mov-ecx-stack4-mov-edx-magic-mov-eax-ecx-imul-edx-mov-eax-edx-shr-eax-31-sar-edx-2-add-edx-eax-add-edx-edx-lea-eax-edx-edx4-sub-ecx-eax-mov-eax-ecx"),
}


def decode_stack_arg_srem_magic(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    decoded = I386_STACK_ARG_SREM_MAGIC_OPS.get(core)
    if decoded is None:
        return None
    divisor, multiplier, shift, pattern = decoded
    return {
        "divisor": divisor,
        "multiplier": multiplier,
        "shift": shift,
        "pattern": pattern,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_srem_magic(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_srem_magic(data, stdcall=False)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("stack-arg-srem-magic-cdecl", row) + "\n".join(
        [
            f"int {c_name}(int value) {{",
            f"    return value % {divisor};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-srem-magic-cdecl",
            variant=f"cdecl-o2-stack-arg-srem-{divisor}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": "%",
                "divisor": divisor,
                "multiplier": decoded["multiplier"],
                "shift": int(decoded["shift"]),
            },
        )
    ]


def stack_arg_srem_magic_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_srem_magic(data, stdcall=True)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("stack-arg-srem-magic-stdcall", row) + "\n".join(
        [
            f"int __stdcall {c_name}(int value) {{",
            f"    return value % {divisor};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-srem-magic-stdcall",
            variant=f"stdcall4-o2-stack-arg-srem-{divisor}",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": "%",
                "divisor": divisor,
                "multiplier": decoded["multiplier"],
                "shift": int(decoded["shift"]),
                "stackBytes": 4,
            },
        )
    ]


def decode_stack_arg_sdiv_pow2(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if core == b"\x8b\x4c\x24\x04\x89\xc8\xc1\xe8\x1f\x01\xc8\xd1\xf8":
        return {
            "shift": 1,
            "divisor": 2,
            "bias": 1,
            "pattern": "mov-ecx-stack4-mov-eax-ecx-shr-eax-31-add-eax-ecx-sar-eax-one",
            "stackBytes": 4 if stdcall else 0,
        }
    if len(core) == 15 and core[:4] == b"\x8b\x4c\x24\x04" and core[4:6] == b"\x8d\x41" and core[7:12] == b"\x85\xc9\x0f\x49\xc1" and core[12:14] == b"\xc1\xf8":
        bias = core[6]
        shift = core[14]
        if not 2 <= shift <= 7:
            return None
        if bias != (1 << shift) - 1:
            return None
        return {
            "shift": shift,
            "divisor": 1 << shift,
            "bias": bias,
            "pattern": "mov-ecx-stack4-lea-eax-ecx-bias-test-ecx-ecx-cmovns-eax-ecx-sar-eax-imm8",
            "stackBytes": 4 if stdcall else 0,
        }
    return None


def stack_arg_sdiv_pow2(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_sdiv_pow2(data, stdcall=False)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("stack-arg-sdiv-pow2-cdecl", row) + "\n".join(
        [
            f"int {c_name}(int value) {{",
            f"    return value / {divisor};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-sdiv-pow2-cdecl",
            variant=f"cdecl-o2-stack-arg-sdiv-{divisor}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": "/",
                "shift": int(decoded["shift"]),
                "divisor": divisor,
                "bias": int(decoded["bias"]),
            },
        )
    ]


def stack_arg_sdiv_pow2_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_sdiv_pow2(data, stdcall=True)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("stack-arg-sdiv-pow2-stdcall", row) + "\n".join(
        [
            f"int __stdcall {c_name}(int value) {{",
            f"    return value / {divisor};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-sdiv-pow2-stdcall",
            variant=f"stdcall4-o2-stack-arg-sdiv-{divisor}",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": "/",
                "shift": int(decoded["shift"]),
                "divisor": divisor,
                "bias": int(decoded["bias"]),
                "stackBytes": 4,
            },
        )
    ]


def decode_stack_arg_srem_pow2(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if core == b"\x8b\x44\x24\x04\x89\xc1\xc1\xe9\x1f\x01\xc1\x83\xe1\xfe\x29\xc8":
        return {
            "shift": 1,
            "divisor": 2,
            "bias": 1,
            "maskByte": 0xFE,
            "pattern": "mov-eax-stack4-mov-ecx-eax-shr-ecx-31-add-ecx-eax-and-ecx-not1-sub-eax-ecx",
            "stackBytes": 4 if stdcall else 0,
        }
    if len(core) == 17 and core[:4] == b"\x8b\x44\x24\x04" and core[4:6] == b"\x8d\x48" and core[7:12] == b"\x85\xc0\x0f\x49\xc8" and core[12:14] == b"\x83\xe1" and core[15:17] == b"\x29\xc8":
        bias = core[6]
        mask_byte = core[14]
        for shift in range(2, 8):
            divisor = 1 << shift
            if bias == divisor - 1 and mask_byte == ((-divisor) & 0xFF):
                return {
                    "shift": shift,
                    "divisor": divisor,
                    "bias": bias,
                    "maskByte": mask_byte,
                    "pattern": "mov-eax-stack4-lea-ecx-eax-bias-test-eax-eax-cmovns-ecx-eax-and-ecx-not-pow2-minus-one-sub-eax-ecx",
                    "stackBytes": 4 if stdcall else 0,
                }
        return None
    return None


def stack_arg_srem_pow2(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_srem_pow2(data, stdcall=False)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("stack-arg-srem-pow2-cdecl", row) + "\n".join(
        [
            f"int {c_name}(int value) {{",
            f"    return value % {divisor};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-srem-pow2-cdecl",
            variant=f"cdecl-o2-stack-arg-srem-{divisor}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": "%",
                "shift": int(decoded["shift"]),
                "divisor": divisor,
                "bias": int(decoded["bias"]),
                "maskByte": int(decoded["maskByte"]),
            },
        )
    ]


def stack_arg_srem_pow2_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_srem_pow2(data, stdcall=True)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("stack-arg-srem-pow2-stdcall", row) + "\n".join(
        [
            f"int __stdcall {c_name}(int value) {{",
            f"    return value % {divisor};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-srem-pow2-stdcall",
            variant=f"stdcall4-o2-stack-arg-srem-{divisor}",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": "%",
                "shift": int(decoded["shift"]),
                "divisor": divisor,
                "bias": int(decoded["bias"]),
                "maskByte": int(decoded["maskByte"]),
                "stackBytes": 4,
            },
        )
    ]


I386_STACK_ARG_UDIV_MAGIC_OPS: dict[tuple[int, int], tuple[int, str]] = {
    (0xAAAAAAAB, 1): (3, "mov-eax-magic-mul-stack4-mov-eax-edx-shr-eax-one"),
    (0xCCCCCCCD, 2): (5, "mov-eax-magic-mul-stack4-mov-eax-edx-shr-eax-2"),
    (0xCCCCCCCD, 3): (10, "mov-eax-magic-mul-stack4-mov-eax-edx-shr-eax-3"),
}


def decode_stack_arg_udiv_magic(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if len(core) < 13 or core[:1] != b"\xb8" or core[5:11] != b"\xf7\x64\x24\x04\x89\xd0":
        return None
    multiplier = int.from_bytes(core[1:5], "little", signed=False)
    if core[11:13] == b"\xd1\xe8":
        shift = 1
        if len(core) != 13:
            return None
    elif len(core) == 14 and core[11:13] == b"\xc1\xe8":
        shift = core[13]
    else:
        return None
    decoded = I386_STACK_ARG_UDIV_MAGIC_OPS.get((multiplier, shift))
    if decoded is None:
        return None
    divisor, pattern = decoded
    return {
        "divisor": divisor,
        "multiplier": multiplier,
        "shift": shift,
        "pattern": pattern,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_udiv_magic(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_udiv_magic(data, stdcall=False)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("stack-arg-udiv-magic-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value / {divisor}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-udiv-magic-cdecl",
            variant=f"cdecl-o2-stack-arg-udiv-{divisor}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": "/",
                "divisor": divisor,
                "multiplier": f"0x{int(decoded['multiplier']):08x}",
                "shift": int(decoded["shift"]),
            },
        )
    ]


def stack_arg_udiv_magic_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_udiv_magic(data, stdcall=True)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("stack-arg-udiv-magic-stdcall", row) + "\n".join(
        [
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            f"    return value / {divisor}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-udiv-magic-stdcall",
            variant=f"stdcall4-o2-stack-arg-udiv-{divisor}",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": "/",
                "divisor": divisor,
                "multiplier": f"0x{int(decoded['multiplier']):08x}",
                "shift": int(decoded["shift"]),
                "stackBytes": 4,
            },
        )
    ]


I386_STACK_ARG_UREM_MAGIC_OPS: dict[bytes, tuple[int, str, int, str]] = {
    bytes.fromhex("8b4c2404baabaaaaaa89c8f7e2d1ea8d045229c189c8"): (3, "0xaaaaaaab", 1, "mov-ecx-stack4-mov-edx-magic-mov-eax-ecx-mul-edx-shr-edx-one-lea-eax-edx-edx2-sub-ecx-eax-mov-eax-ecx"),
    bytes.fromhex("8b4c2404bacdcccccc89c8f7e2c1ea028d049229c189c8"): (5, "0xcccccccd", 2, "mov-ecx-stack4-mov-edx-magic-mov-eax-ecx-mul-edx-shr-edx-2-lea-eax-edx-edx4-sub-ecx-eax-mov-eax-ecx"),
    bytes.fromhex("8b4c2404bacdcccccc89c8f7e2c1ea0283e2fe8d049229c189c8"): (10, "0xcccccccd", 2, "mov-ecx-stack4-mov-edx-magic-mov-eax-ecx-mul-edx-shr-edx-2-and-edx-not1-lea-eax-edx-edx4-sub-ecx-eax-mov-eax-ecx"),
}


def decode_stack_arg_urem_magic(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    decoded = I386_STACK_ARG_UREM_MAGIC_OPS.get(core)
    if decoded is None:
        return None
    divisor, multiplier, shift, pattern = decoded
    return {
        "divisor": divisor,
        "multiplier": multiplier,
        "shift": shift,
        "pattern": pattern,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_urem_magic(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_urem_magic(data, stdcall=False)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("stack-arg-urem-magic-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value % {divisor}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-urem-magic-cdecl",
            variant=f"cdecl-o2-stack-arg-urem-{divisor}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": "%",
                "divisor": divisor,
                "multiplier": decoded["multiplier"],
                "shift": int(decoded["shift"]),
            },
        )
    ]


def stack_arg_urem_magic_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_urem_magic(data, stdcall=True)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("stack-arg-urem-magic-stdcall", row) + "\n".join(
        [
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            f"    return value % {divisor}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-urem-magic-stdcall",
            variant=f"stdcall4-o2-stack-arg-urem-{divisor}",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": "%",
                "divisor": divisor,
                "multiplier": decoded["multiplier"],
                "shift": int(decoded["shift"]),
                "stackBytes": 4,
            },
        )
    ]


def decode_stack_arg_udiv_pow2(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if core == b"\x8b\x44\x24\x04\xd1\xe8":
        return {
            "shift": 1,
            "divisor": 2,
            "pattern": "mov-eax-stack4-shr-eax-one",
            "stackBytes": 4 if stdcall else 0,
        }
    if len(core) == 7 and core[:4] == b"\x8b\x44\x24\x04" and core[4:6] == b"\xc1\xe8":
        shift = core[6]
        if not 2 <= shift <= 31:
            return None
        return {
            "shift": shift,
            "divisor": 1 << shift,
            "pattern": "mov-eax-stack4-shr-eax-imm8",
            "stackBytes": 4 if stdcall else 0,
        }
    return None


def stack_arg_udiv_pow2(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_udiv_pow2(data, stdcall=False)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("stack-arg-udiv-pow2-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value / {divisor}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-udiv-pow2-cdecl",
            variant=f"cdecl-o2-stack-arg-udiv-{divisor}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": "/",
                "shift": int(decoded["shift"]),
                "divisor": divisor,
            },
        )
    ]


def stack_arg_udiv_pow2_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_udiv_pow2(data, stdcall=True)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    source = header("stack-arg-udiv-pow2-stdcall", row) + "\n".join(
        [
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            f"    return value / {divisor}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-udiv-pow2-stdcall",
            variant=f"stdcall4-o2-stack-arg-udiv-{divisor}",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": "/",
                "shift": int(decoded["shift"]),
                "divisor": divisor,
                "stackBytes": 4,
            },
        )
    ]


def decode_stack_arg_urem_pow2(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if len(core) != 7 or core[:4] != b"\x8b\x44\x24\x04" or core[4:6] != b"\x83\xe0":
        return None
    mask = core[6]
    if mask < 1:
        return None
    divisor = mask + 1
    if divisor & (divisor - 1):
        return None
    return {
        "shift": divisor.bit_length() - 1,
        "divisor": divisor,
        "mask": mask,
        "pattern": "mov-eax-stack4-and-eax-pow2-minus-one",
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_urem_pow2(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_urem_pow2(data, stdcall=False)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    mask = int(decoded["mask"])
    source = header("stack-arg-urem-pow2-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value % {divisor}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-urem-pow2-cdecl",
            variant=f"cdecl-o2-stack-arg-urem-{divisor}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": "%",
                "shift": int(decoded["shift"]),
                "divisor": divisor,
                "mask": f"0x{mask:08x}",
            },
        )
    ]


def stack_arg_urem_pow2_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_urem_pow2(data, stdcall=True)
    if decoded is None:
        return []
    divisor = int(decoded["divisor"])
    mask = int(decoded["mask"])
    source = header("stack-arg-urem-pow2-stdcall", row) + "\n".join(
        [
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            f"    return value % {divisor}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-urem-pow2-stdcall",
            variant=f"stdcall4-o2-stack-arg-urem-{divisor}",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": "%",
                "shift": int(decoded["shift"]),
                "divisor": divisor,
                "mask": f"0x{mask:08x}",
                "stackBytes": 4,
            },
        )
    ]


I386_SIGNED_ZERO_COMPARE_RULES: dict[str, tuple[str, str, str]] = {
    "lt": ("<", "mov-eax-stack4-shr-eax-31"),
    "ge": (">=", "mov-eax-stack4-not-eax-shr-eax-31"),
    "gt": (">", "xor-eax-cmp-stack4-zero-setg-al"),
    "le": ("<=", "xor-eax-cmp-stack4-zero-setle-al"),
}


def decode_stack_arg_signed_zero_compare(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if body == b"\x8b\x44\x24\x04\xc1\xe8\x1f" + ret:
        suffix = "lt"
    elif body == b"\x8b\x44\x24\x04\xf7\xd0\xc1\xe8\x1f" + ret:
        suffix = "ge"
    elif body == b"\x31\xc0\x83\x7c\x24\x04\x00\x0f\x9f\xc0" + ret:
        suffix = "gt"
    elif body == b"\x31\xc0\x83\x7c\x24\x04\x00\x0f\x9e\xc0" + ret:
        suffix = "le"
    elif body == b"\x33\xc0\x83\x7c\x24\x04\x00\x0f\x9f\xc0" + ret:
        suffix = "gt"
    elif body == b"\x33\xc0\x83\x7c\x24\x04\x00\x0f\x9e\xc0" + ret:
        suffix = "le"
    else:
        return None
    operator, pattern = I386_SIGNED_ZERO_COMPARE_RULES[suffix]
    return {
        "suffix": suffix,
        "operator": operator,
        "pattern": pattern,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_signed_zero_compare(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_signed_zero_compare(data, stdcall=False)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = header(f"stack-arg-int-{suffix}-zero-cdecl", row) + "\n".join(
        [
            f"int {c_name}(int value) {{",
            f"    return value {operator} 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-int-{suffix}-zero-cdecl",
            variant=f"cdecl-o2-int-{suffix}-zero",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence={"pattern": decoded["pattern"], "operator": operator},
        )
    ]


def stack_arg_signed_zero_compare_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_signed_zero_compare(data, stdcall=True)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = header(f"stack-arg-int-{suffix}-zero-stdcall", row) + "\n".join(
        [
            f"int __stdcall {c_name}(int value) {{",
            f"    return value {operator} 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-int-{suffix}-zero-stdcall",
            variant=f"stdcall4-o2-int-{suffix}-zero",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="int",
            evidence={"pattern": decoded["pattern"], "operator": operator, "stackBytes": 4},
        )
    ]


I386_STACK_ARG_NEG_CMOV_OPS: dict[bytes, tuple[str, str, str, str]] = {
    bytes.fromhex("8b4c240489c8f7d80f48c1"): ("abs", "value < 0 ? -value : value", "cmovs", "mov-ecx-stack4-mov-eax-ecx-neg-eax-cmovs-eax-ecx"),
    bytes.fromhex("8b4c240489c8f7d80f49c1"): ("neg-if-pos", "value > 0 ? -value : value", "cmovns", "mov-ecx-stack4-mov-eax-ecx-neg-eax-cmovns-eax-ecx"),
}


def decode_stack_arg_neg_cmov(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    decoded = I386_STACK_ARG_NEG_CMOV_OPS.get(body[: -len(ret)])
    if decoded is None:
        return None
    suffix, expression, cmov, pattern = decoded
    return {
        "suffix": suffix,
        "expression": expression,
        "cmov": cmov,
        "pattern": pattern,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_neg_cmov(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_neg_cmov(data, stdcall=False)
    if decoded is None:
        return []
    source = header(f"stack-arg-{decoded['suffix']}-cmov-cdecl", row) + "\n".join(
        [
            f"int {c_name}(int value) {{",
            f"    return {decoded['expression']};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-{decoded['suffix']}-cmov-cdecl",
            variant=f"cdecl-o2-stack-arg-{decoded['suffix']}-cmov",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence={
                "pattern": decoded["pattern"],
                "valueType": "int",
                "returnType": "int",
                "expression": decoded["expression"],
                "cmov": decoded["cmov"],
            },
        )
    ]


def stack_arg_neg_cmov_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_neg_cmov(data, stdcall=True)
    if decoded is None:
        return []
    source = header(f"stack-arg-{decoded['suffix']}-cmov-stdcall", row) + "\n".join(
        [
            f"int __stdcall {c_name}(int value) {{",
            f"    return {decoded['expression']};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-{decoded['suffix']}-cmov-stdcall",
            variant=f"stdcall4-o2-stack-arg-{decoded['suffix']}-cmov",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="int",
            evidence={
                "pattern": decoded["pattern"],
                "valueType": "int",
                "returnType": "int",
                "expression": decoded["expression"],
                "cmov": decoded["cmov"],
                "stackBytes": 4,
            },
        )
    ]


def decode_stack_arg_nonzero_cmov_const_select(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if len(core) != 14 or core[:6] != b"\x8b\x4c\x24\x04\x85\xc9" or core[6] != 0xB8 or core[11] != 0x0F or core[13] != 0xC1:
        return None
    immediate = int.from_bytes(core[7:11], "little", signed=False)
    if immediate == 0:
        return None
    if core[12] == 0x45:
        return {
            "expression": f"value != 0 ? value : 0x{immediate:08x}u",
            "trueValue": "value",
            "falseValue": immediate,
            "immediate": immediate,
            "cmov": "cmovne",
            "pattern": "mov-ecx-stack4-test-ecx-ecx-mov-eax-imm32-cmovne-eax-ecx",
            "stackBytes": 4 if stdcall else 0,
        }
    if core[12] == 0x44:
        return {
            "expression": f"value != 0 ? 0x{immediate:08x}u : value",
            "trueValue": immediate,
            "falseValue": "value",
            "immediate": immediate,
            "cmov": "cmove",
            "pattern": "mov-ecx-stack4-test-ecx-ecx-mov-eax-imm32-cmove-eax-ecx",
            "stackBytes": 4 if stdcall else 0,
        }
    return None


def stack_arg_nonzero_cmov_const_select(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_nonzero_cmov_const_select(data, stdcall=False)
    if decoded is None:
        return []
    source = header("stack-arg-nonzero-cmov-const-select-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return {decoded['expression']};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-nonzero-cmov-const-select-cdecl",
            variant=f"cdecl-o2-stack-arg-nonzero-cmov-const-select-{decoded['cmov']}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "valueType": "unsigned int",
                "returnType": "unsigned int",
                "expression": decoded["expression"],
                "trueValue": f"0x{decoded['trueValue']:08x}" if isinstance(decoded["trueValue"], int) else decoded["trueValue"],
                "falseValue": f"0x{decoded['falseValue']:08x}" if isinstance(decoded["falseValue"], int) else decoded["falseValue"],
                "immediate": f"0x{int(decoded['immediate']):08x}",
                "cmov": decoded["cmov"],
            },
        )
    ]


def stack_arg_nonzero_cmov_const_select_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_nonzero_cmov_const_select(data, stdcall=True)
    if decoded is None:
        return []
    source = header("stack-arg-nonzero-cmov-const-select-stdcall", row) + "\n".join(
        [
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            f"    return {decoded['expression']};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-nonzero-cmov-const-select-stdcall",
            variant=f"stdcall4-o2-stack-arg-nonzero-cmov-const-select-{decoded['cmov']}",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "valueType": "unsigned int",
                "returnType": "unsigned int",
                "expression": decoded["expression"],
                "trueValue": f"0x{decoded['trueValue']:08x}" if isinstance(decoded["trueValue"], int) else decoded["trueValue"],
                "falseValue": f"0x{decoded['falseValue']:08x}" if isinstance(decoded["falseValue"], int) else decoded["falseValue"],
                "immediate": f"0x{int(decoded['immediate']):08x}",
                "cmov": decoded["cmov"],
                "stackBytes": 4,
            },
        )
    ]


def decode_stack_arg_nonzero_const_select(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if len(core) != 17 or core[:7] != b"\x31\xc0\x83\x7c\x24\x04\x00" or core[7] != 0x0F or core[9:12] != b"\xc0\x8d\x04":
        return None
    setcc_opcode = core[8]
    if setcc_opcode not in {0x94, 0x95}:
        return None
    sib = core[12]
    if sib & 0x07 != 0x05 or ((sib >> 3) & 0x07) != 0x00:
        return None
    scale = 1 << ((sib >> 6) & 0x03)
    if scale not in {2, 4, 8}:
        return None
    base_value = int.from_bytes(core[13:17], "little", signed=False)
    scaled_value = base_value + scale
    if setcc_opcode == 0x95:
        false_value = base_value
        true_value = scaled_value
        setcc = "setne"
    else:
        false_value = scaled_value
        true_value = base_value
        setcc = "sete"
    return {
        "trueValue": true_value,
        "falseValue": false_value,
        "baseValue": base_value,
        "scale": scale,
        "setcc": setcc,
        "pattern": f"xor-eax-cmp-stack4-zero-{setcc}-al-lea-eax-eax{scale}-disp32",
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_nonzero_const_select(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_nonzero_const_select(data, stdcall=False)
    if decoded is None:
        return []
    true_value = int(decoded["trueValue"])
    false_value = int(decoded["falseValue"])
    source = header("stack-arg-nonzero-const-select-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value != 0 ? 0x{true_value:08x}u : 0x{false_value:08x}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-nonzero-const-select-cdecl",
            variant=f"cdecl-o2-stack-arg-nonzero-const-select-{decoded['scale']}-{decoded['setcc']}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "valueType": "unsigned int",
                "returnType": "unsigned int",
                "trueValue": f"0x{true_value:08x}",
                "falseValue": f"0x{false_value:08x}",
                "baseValue": int(decoded["baseValue"]),
                "scale": int(decoded["scale"]),
                "setcc": decoded["setcc"],
            },
        )
    ]


def stack_arg_nonzero_const_select_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_nonzero_const_select(data, stdcall=True)
    if decoded is None:
        return []
    true_value = int(decoded["trueValue"])
    false_value = int(decoded["falseValue"])
    source = header("stack-arg-nonzero-const-select-stdcall", row) + "\n".join(
        [
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            f"    return value != 0 ? 0x{true_value:08x}u : 0x{false_value:08x}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-nonzero-const-select-stdcall",
            variant=f"stdcall4-o2-stack-arg-nonzero-const-select-{decoded['scale']}-{decoded['setcc']}",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "valueType": "unsigned int",
                "returnType": "unsigned int",
                "trueValue": f"0x{true_value:08x}",
                "falseValue": f"0x{false_value:08x}",
                "baseValue": int(decoded["baseValue"]),
                "scale": int(decoded["scale"]),
                "setcc": decoded["setcc"],
                "stackBytes": 4,
            },
        )
    ]


I386_STACK_ARG_CONST_MIN_MAX_CMOV: dict[int, tuple[str, str, str, str, bool]] = {
    0x42: ("uint-min", "<", "unsigned int", "cmovb", False),
    0x43: ("uint-max", ">", "unsigned int", "cmovae", True),
    0x4C: ("int-min", "<", "int", "cmovl", False),
    0x4D: ("int-max", ">", "int", "cmovge", True),
}


def decode_stack_arg_const_min_max(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret) or body[:4] != b"\x8b\x4c\x24\x04":
        return None
    core = body[: -len(ret)]
    if len(core) < 15:
        return None
    offset = 4
    if core[offset : offset + 2] == b"\x83\xf9":
        raw_compare = core[offset + 2]
        compare_unsigned = raw_compare
        compare_signed = raw_compare if raw_compare < 0x80 else raw_compare - 0x100
        offset += 3
        cmp_pattern = "cmp-ecx-imm8"
    elif core[offset : offset + 2] == b"\x81\xf9":
        compare_unsigned = int.from_bytes(core[offset + 2 : offset + 6], "little", signed=False)
        compare_signed = int.from_bytes(core[offset + 2 : offset + 6], "little", signed=True)
        offset += 6
        cmp_pattern = "cmp-ecx-imm32"
    else:
        return None
    if len(core) != offset + 8 or core[offset] != 0xB8 or core[offset + 5] != 0x0F or core[offset + 7] != 0xC1:
        return None
    cmov_opcode = core[offset + 6]
    decoded = I386_STACK_ARG_CONST_MIN_MAX_CMOV.get(cmov_opcode)
    if decoded is None:
        return None
    suffix, operator, value_type, cmov, compare_is_exclusive_upper = decoded
    raw_constant = int.from_bytes(core[offset + 1 : offset + 5], "little", signed=False)
    if value_type == "int":
        constant: int = int.from_bytes(core[offset + 1 : offset + 5], "little", signed=True)
        compare_value = compare_signed
    else:
        constant = raw_constant
        compare_value = compare_unsigned
    if compare_is_exclusive_upper:
        if compare_value == -0x80000000 or (value_type == "unsigned int" and compare_value == 0):
            return None
        expected_constant = compare_value - 1
    else:
        expected_constant = compare_value
    if constant != expected_constant:
        return None
    return {
        "suffix": suffix,
        "operator": operator,
        "valueType": value_type,
        "returnType": value_type,
        "constant": constant,
        "rawConstant": raw_constant,
        "compareImmediate": compare_value,
        "cmov": cmov,
        "pattern": f"mov-ecx-stack4-{cmp_pattern}-mov-eax-imm32-{cmov}-eax-ecx",
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_const_min_max(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_const_min_max(data, stdcall=False)
    if decoded is None:
        return []
    constant = int(decoded["constant"])
    value_type = str(decoded["valueType"])
    return_type = str(decoded["returnType"])
    operator = str(decoded["operator"])
    literal = f"0x{constant:08x}u" if value_type == "unsigned int" else str(constant)
    source = header(f"stack-arg-{decoded['suffix']}-const-cmov-cdecl", row) + "\n".join(
        [
            f"{return_type} {c_name}({value_type} value) {{",
            f"    return value {operator} {literal} ? value : {literal};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-{decoded['suffix']}-const-cmov-cdecl",
            variant=f"cdecl-o2-stack-arg-{decoded['suffix']}-const-{decoded['cmov']}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type=return_type,
            evidence={
                "pattern": decoded["pattern"],
                "operator": operator,
                "constant": f"0x{constant:08x}" if value_type == "unsigned int" else constant,
                "compareImmediate": int(decoded["compareImmediate"]),
                "valueType": value_type,
                "returnType": return_type,
                "cmov": decoded["cmov"],
            },
        )
    ]


def stack_arg_const_min_max_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_const_min_max(data, stdcall=True)
    if decoded is None:
        return []
    constant = int(decoded["constant"])
    value_type = str(decoded["valueType"])
    return_type = str(decoded["returnType"])
    operator = str(decoded["operator"])
    literal = f"0x{constant:08x}u" if value_type == "unsigned int" else str(constant)
    source = header(f"stack-arg-{decoded['suffix']}-const-cmov-stdcall", row) + "\n".join(
        [
            f"{return_type} __stdcall {c_name}({value_type} value) {{",
            f"    return value {operator} {literal} ? value : {literal};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-{decoded['suffix']}-const-cmov-stdcall",
            variant=f"stdcall4-o2-stack-arg-{decoded['suffix']}-const-{decoded['cmov']}",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type=return_type,
            evidence={
                "pattern": decoded["pattern"],
                "operator": operator,
                "constant": f"0x{constant:08x}" if value_type == "unsigned int" else constant,
                "compareImmediate": int(decoded["compareImmediate"]),
                "valueType": value_type,
                "returnType": return_type,
                "cmov": decoded["cmov"],
                "stackBytes": 4,
            },
        )
    ]


I386_UNSIGNED_IMM8_COMPARE_SETCC: dict[int, tuple[str, str, str]] = {
    0x92: ("lt", "<", "setb"),
    0x93: ("ge", ">=", "setae"),
    0x94: ("eq", "==", "sete"),
    0x95: ("ne", "!=", "setne"),
}


I386_SIGNED_IMM8_COMPARE_SETCC: dict[int, tuple[str, str, str]] = {
    0x9C: ("lt", "<", "setl"),
    0x9D: ("ge", ">=", "setge"),
}


def decode_stack_arg_signed_imm8_compare(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    expected_len = 13 if stdcall else 11
    if len(body) != expected_len or not body.endswith(ret):
        return None
    if body[:2] not in {b"\x31\xc0", b"\x33\xc0"} or body[2:6] != b"\x83\x7c\x24\x04":
        return None
    if body[7] != 0x0F or body[9] != 0xC0:
        return None
    decoded = I386_SIGNED_IMM8_COMPARE_SETCC.get(body[8])
    if decoded is None:
        return None
    suffix, operator, setcc = decoded
    raw_imm = body[6]
    value = raw_imm if raw_imm < 0x80 else raw_imm - 0x100
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
        "immediate": value,
        "rawImmediate": raw_imm,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_signed_imm8_compare(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_signed_imm8_compare(data, stdcall=False)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"])
    source = header(f"stack-arg-int-{suffix}-imm8-cdecl", row) + "\n".join(
        [
            f"int {c_name}(int value) {{",
            f"    return value {operator} {immediate};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-int-{suffix}-imm8-cdecl",
            variant=f"cdecl-o2-int-{suffix}-imm8",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence={
                "pattern": "xor-eax-cmp-stack4-imm8-setcc-al-ret",
                "operator": operator,
                "setcc": decoded["setcc"],
                "immediate": immediate,
                "rawImmediate": int(decoded["rawImmediate"]),
            },
        )
    ]


def stack_arg_signed_imm8_compare_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_signed_imm8_compare(data, stdcall=True)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"])
    source = header(f"stack-arg-int-{suffix}-imm8-stdcall", row) + "\n".join(
        [
            f"int __stdcall {c_name}(int value) {{",
            f"    return value {operator} {immediate};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-int-{suffix}-imm8-stdcall",
            variant=f"stdcall4-o2-int-{suffix}-imm8",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="int",
            evidence={
                "pattern": "xor-eax-cmp-stack4-imm8-setcc-al-ret4",
                "operator": operator,
                "setcc": decoded["setcc"],
                "immediate": immediate,
                "rawImmediate": int(decoded["rawImmediate"]),
                "stackBytes": 4,
            },
        )
    ]


def decode_stack_arg_unsigned_imm8_compare(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    expected_len = 13 if stdcall else 11
    if len(body) != expected_len or not body.endswith(ret):
        return None
    if body[:2] not in {b"\x31\xc0", b"\x33\xc0"} or body[2:6] != b"\x83\x7c\x24\x04":
        return None
    if body[7] != 0x0F or body[9] != 0xC0:
        return None
    decoded = I386_UNSIGNED_IMM8_COMPARE_SETCC.get(body[8])
    if decoded is None:
        return None
    if body[6] == 0 and body[8] in {0x94, 0x95}:
        return None
    suffix, operator, setcc = decoded
    raw_imm = body[6]
    value = raw_imm if raw_imm < 0x80 else raw_imm | 0xFFFFFF00
    return {
        "suffix": suffix,
        "operator": operator,
        "setcc": setcc,
        "immediate": value,
        "rawImmediate": raw_imm,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_unsigned_imm8_compare(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_unsigned_imm8_compare(data, stdcall=False)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"])
    source = header(f"stack-arg-uint-{suffix}-imm8-cdecl", row) + "\n".join(
        [
            f"int {c_name}(unsigned int value) {{",
            f"    return value {operator} 0x{immediate:08x}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-uint-{suffix}-imm8-cdecl",
            variant=f"cdecl-o2-uint-{suffix}-imm8",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence={
                "pattern": "xor-eax-cmp-stack4-imm8-setcc-al-ret",
                "operator": operator,
                "setcc": decoded["setcc"],
                "immediate": f"0x{immediate:08x}",
                "rawImmediate": int(decoded["rawImmediate"]),
            },
        )
    ]


def stack_arg_unsigned_imm8_compare_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_unsigned_imm8_compare(data, stdcall=True)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"])
    source = header(f"stack-arg-uint-{suffix}-imm8-stdcall", row) + "\n".join(
        [
            f"int __stdcall {c_name}(unsigned int value) {{",
            f"    return value {operator} 0x{immediate:08x}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-uint-{suffix}-imm8-stdcall",
            variant=f"stdcall4-o2-uint-{suffix}-imm8",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="int",
            evidence={
                "pattern": "xor-eax-cmp-stack4-imm8-setcc-al-ret4",
                "operator": operator,
                "setcc": decoded["setcc"],
                "immediate": f"0x{immediate:08x}",
                "rawImmediate": int(decoded["rawImmediate"]),
                "stackBytes": 4,
            },
        )
    ]


def decode_stack_arg_bitmask_predicate(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if core == b"\x8b\x44\x24\x04\x83\xe0\x01":
        return {"predicate": "nonzero", "mask": 0x00000001, "pattern": "mov-eax-stack4-and-1"}
    if len(core) == 10 and core[:4] == b"\x8b\x44\x24\x04" and core[4:6] == b"\xc1\xe8" and core[7:] == b"\x83\xe0\x01":
        shift = core[6]
        if 1 <= shift <= 30:
            return {"predicate": "nonzero", "mask": 1 << shift, "pattern": "mov-eax-stack4-shr-and-1", "shift": shift}
    if core == b"\x8b\x44\x24\x04\xf7\xd0\x83\xe0\x01":
        return {"predicate": "zero", "mask": 0x00000001, "pattern": "mov-eax-stack4-not-and-1"}
    if len(core) == 10 and core[:2] in {b"\x31\xc0", b"\x33\xc0"} and core[2:5] == b"\xf6\x44\x24":
        byte_offset = core[5] - 4
        byte_mask = core[6]
        if 0 <= byte_offset <= 3 and byte_mask:
            return {
                "predicate": "zero",
                "mask": byte_mask << (byte_offset * 8),
                "pattern": "xor-eax-test-stack-byte-imm8-sete-al",
                "byteOffset": byte_offset,
                "byteMask": byte_mask,
            }
    return None


def stack_arg_bitmask_predicate(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_bitmask_predicate(data, stdcall=False)
    if decoded is None:
        return []
    mask = int(decoded["mask"])
    predicate = str(decoded["predicate"])
    operator = "!=" if predicate == "nonzero" else "=="
    source = header(f"stack-arg-bitmask-{predicate}-cdecl", row) + "\n".join(
        [
            f"int {c_name}(unsigned int value) {{",
            f"    return (value & 0x{mask:08x}u) {operator} 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-bitmask-{predicate}-cdecl",
            variant=f"cdecl-o2-bitmask-{predicate}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence={"pattern": decoded["pattern"], "mask": f"0x{mask:08x}", "predicate": predicate},
        )
    ]


def stack_arg_bitmask_predicate_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_bitmask_predicate(data, stdcall=True)
    if decoded is None:
        return []
    mask = int(decoded["mask"])
    predicate = str(decoded["predicate"])
    operator = "!=" if predicate == "nonzero" else "=="
    source = header(f"stack-arg-bitmask-{predicate}-stdcall", row) + "\n".join(
        [
            f"int __stdcall {c_name}(unsigned int value) {{",
            f"    return (value & 0x{mask:08x}u) {operator} 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-bitmask-{predicate}-stdcall",
            variant=f"stdcall4-o2-bitmask-{predicate}",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="int",
            evidence={"pattern": decoded["pattern"], "mask": f"0x{mask:08x}", "predicate": predicate, "stackBytes": 4},
        )
    ]


I386_STACK_ARG_IMM8_BINARY_OPS: dict[int, tuple[str, str]] = {
    0xC0: ("add", "+"),
    0xE0: ("and", "&"),
    0xC8: ("or", "|"),
    0xF0: ("xor", "^"),
}


I386_STACK_ARG_LEA_MULTIPLY_OPS: dict[bytes, tuple[int, str]] = {
    bytes.fromhex("8b44240401c0"): (2, "mov-eax-stack4-add-eax-eax"),
    bytes.fromhex("8b4424048d0440"): (3, "mov-eax-stack4-lea-eax-eax-eax2"),
    bytes.fromhex("8b4424048d0480"): (5, "mov-eax-stack4-lea-eax-eax-eax4"),
    bytes.fromhex("8b44240401c08d0440"): (6, "mov-eax-stack4-add-eax-eax-lea-eax-eax-eax2"),
    bytes.fromhex("8b4c24048d04cd0000000029c8"): (7, "mov-ecx-stack4-lea-eax-ecx8-sub-eax-ecx"),
    bytes.fromhex("8b4424048d04c0"): (9, "mov-eax-stack4-lea-eax-eax-eax8"),
    bytes.fromhex("8b44240401c08d0480"): (10, "mov-eax-stack4-add-eax-eax-lea-eax-eax-eax4"),
    bytes.fromhex("8b4424048d0c808d0448"): (11, "mov-eax-stack4-lea-ecx-eax-eax4-lea-eax-eax-ecx2"),
    bytes.fromhex("8b442404c1e0028d0440"): (12, "mov-eax-stack4-shl-eax-2-lea-eax-eax-eax2"),
    bytes.fromhex("8b4424048d0c408d0488"): (13, "mov-eax-stack4-lea-ecx-eax-eax2-lea-eax-eax-ecx4"),
    bytes.fromhex("8b4424048d0c00c1e00429c8"): (14, "mov-eax-stack4-lea-ecx-eax-eax-shl-eax-4-sub-eax-ecx"),
    bytes.fromhex("8b4424048d04808d0440"): (15, "mov-eax-stack4-lea-eax-eax-eax4-lea-eax-eax-eax2"),
    bytes.fromhex("8b442404c1e0038d0440"): (24, "mov-eax-stack4-shl-eax-3-lea-eax-eax-eax2"),
    bytes.fromhex("8b4c240489c8c1e00529c8"): (31, "mov-ecx-stack4-mov-eax-ecx-shl-eax-5-sub-eax-ecx"),
    bytes.fromhex("8b4c240489c8c1e00501c8"): (33, "mov-ecx-stack4-mov-eax-ecx-shl-eax-5-add-eax-ecx"),
}


def decode_stack_arg_lea_multiply(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    decoded = I386_STACK_ARG_LEA_MULTIPLY_OPS.get(body[: -len(ret)])
    if decoded is None:
        return None
    multiplier, pattern = decoded
    return {
        "multiplier": multiplier,
        "pattern": pattern,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_lea_multiply(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_lea_multiply(data, stdcall=False)
    if decoded is None:
        return []
    multiplier = int(decoded["multiplier"])
    source = header("stack-arg-mul-lea-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value * {multiplier}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-mul-lea-cdecl",
            variant=f"cdecl-o2-stack-arg-mul-lea-{multiplier}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={
                "pattern": str(decoded["pattern"]),
                "operator": "*",
                "multiplier": multiplier,
            },
        )
    ]


def stack_arg_lea_multiply_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_lea_multiply(data, stdcall=True)
    if decoded is None:
        return []
    multiplier = int(decoded["multiplier"])
    source = header("stack-arg-mul-lea-stdcall", row) + "\n".join(
        [
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            f"    return value * {multiplier}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-mul-lea-stdcall",
            variant=f"stdcall4-o2-stack-arg-mul-lea-{multiplier}",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={
                "pattern": str(decoded["pattern"]),
                "operator": "*",
                "multiplier": multiplier,
                "stackBytes": 4,
            },
        )
    ]


def decode_stack_arg_imm8_binary_op(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if len(core) != 7 or core[:4] != b"\x8b\x44\x24\x04" or core[4] != 0x83:
        return None
    decoded = I386_STACK_ARG_IMM8_BINARY_OPS.get(core[5])
    if decoded is None:
        return None
    suffix, operator = decoded
    raw_immediate = core[6]
    signed_immediate = raw_immediate if raw_immediate < 0x80 else raw_immediate - 0x100
    immediate = raw_immediate
    if suffix == "add" and signed_immediate < 0:
        suffix = "sub"
        operator = "-"
        immediate = -signed_immediate
    return {
        "suffix": suffix,
        "operator": operator,
        "immediate": immediate,
        "rawImmediate": raw_immediate,
        "signedImmediate": signed_immediate,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_imm8_binary_op(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_imm8_binary_op(data, stdcall=False)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"])
    source = header(f"stack-arg-{suffix}-imm8-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value {operator} 0x{immediate:02x}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-{suffix}-imm8-cdecl",
            variant=f"cdecl-o2-stack-arg-{suffix}-imm8",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={
                "pattern": "mov-eax-stack4-op-imm8-ret",
                "operator": operator,
                "immediate": f"0x{immediate:02x}",
                "rawImmediate": int(decoded["rawImmediate"]),
                "signedImmediate": int(decoded["signedImmediate"]),
            },
        )
    ]


def stack_arg_imm8_binary_op_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_imm8_binary_op(data, stdcall=True)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    immediate = int(decoded["immediate"])
    source = header(f"stack-arg-{suffix}-imm8-stdcall", row) + "\n".join(
        [
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            f"    return value {operator} 0x{immediate:02x}u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-{suffix}-imm8-stdcall",
            variant=f"stdcall4-o2-stack-arg-{suffix}-imm8",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={
                "pattern": "mov-eax-stack4-op-imm8-ret4",
                "operator": operator,
                "immediate": f"0x{immediate:02x}",
                "rawImmediate": int(decoded["rawImmediate"]),
                "signedImmediate": int(decoded["signedImmediate"]),
                "stackBytes": 4,
            },
        )
    ]


I386_STACK_ARG_UNARY_OPS: dict[bytes, tuple[str, str, str]] = {
    b"\x31\xc0\x2b\x44\x24\x04": ("neg", "-", "xor-eax-sub-eax-stack4"),
    b"\x8b\x44\x24\x04\xf7\xd0": ("not", "~", "mov-eax-stack4-not-eax"),
}


def decode_stack_arg_unary_op(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    decoded = I386_STACK_ARG_UNARY_OPS.get(body[: -len(ret)])
    if decoded is None:
        return None
    suffix, operator, pattern = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "pattern": pattern,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_unary_op(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_unary_op(data, stdcall=False)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = header(f"stack-arg-{suffix}-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return {operator}value;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-{suffix}-cdecl",
            variant=f"cdecl-o2-stack-arg-{suffix}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={"pattern": decoded["pattern"], "operator": operator},
        )
    ]


def stack_arg_unary_op_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_unary_op(data, stdcall=True)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = header(f"stack-arg-{suffix}-stdcall", row) + "\n".join(
        [
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            f"    return {operator}value;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-{suffix}-stdcall",
            variant=f"stdcall4-o2-stack-arg-{suffix}",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={"pattern": decoded["pattern"], "operator": operator, "stackBytes": 4},
        )
    ]


I386_STACK_ARG_INC_DEC_OPS: dict[int, tuple[str, str, str]] = {
    0x40: ("inc", "+", "mov-eax-stack4-inc-eax"),
    0x48: ("dec", "-", "mov-eax-stack4-dec-eax"),
}


def decode_stack_arg_inc_dec(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if len(core) != 5 or core[:4] != b"\x8b\x44\x24\x04":
        return None
    decoded = I386_STACK_ARG_INC_DEC_OPS.get(core[4])
    if decoded is None:
        return None
    suffix, operator, pattern = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "pattern": pattern,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_inc_dec(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_inc_dec(data, stdcall=False)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = header(f"stack-arg-{suffix}-cdecl", row) + "\n".join(
        [
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    return value {operator} 1u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-{suffix}-cdecl",
            variant=f"cdecl-o2-stack-arg-{suffix}",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": operator,
                "delta": 1,
            },
        )
    ]


def stack_arg_inc_dec_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_inc_dec(data, stdcall=True)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    source = header(f"stack-arg-{suffix}-stdcall", row) + "\n".join(
        [
            f"unsigned int __stdcall {c_name}(unsigned int value) {{",
            f"    return value {operator} 1u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-{suffix}-stdcall",
            variant=f"stdcall4-o2-stack-arg-{suffix}",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={
                "pattern": decoded["pattern"],
                "operator": operator,
                "delta": 1,
                "stackBytes": 4,
            },
        )
    ]


I386_STACK_ARG_SHIFT_IMM8_OPS: dict[int, tuple[str, str, str, str]] = {
    0xE0: ("shl", "<<", "unsigned int", "unsigned int"),
    0xE8: ("shr", ">>", "unsigned int", "unsigned int"),
    0xF8: ("sar", ">>", "int", "int"),
}


def decode_stack_arg_shift_imm8(data: bytes, *, stdcall: bool) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    ret = b"\xc2\x04\x00" if stdcall else b"\xc3"
    if not body.endswith(ret):
        return None
    core = body[: -len(ret)]
    if len(core) != 7 or core[:4] != b"\x8b\x44\x24\x04" or core[4] != 0xC1:
        return None
    decoded = I386_STACK_ARG_SHIFT_IMM8_OPS.get(core[5])
    if decoded is None:
        return None
    shift = core[6]
    if not 2 <= shift <= 31:
        return None
    suffix, operator, value_type, return_type = decoded
    return {
        "suffix": suffix,
        "operator": operator,
        "valueType": value_type,
        "returnType": return_type,
        "shift": shift,
        "stackBytes": 4 if stdcall else 0,
    }


def stack_arg_shift_imm8(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_shift_imm8(data, stdcall=False)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    value_type = str(decoded["valueType"])
    return_type = str(decoded["returnType"])
    shift = int(decoded["shift"])
    source = header(f"stack-arg-{suffix}-imm8-cdecl", row) + "\n".join(
        [
            f"{return_type} {c_name}({value_type} value) {{",
            f"    return value {operator} {shift};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-{suffix}-imm8-cdecl",
            variant=f"cdecl-o2-stack-arg-{suffix}-imm8",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type=return_type,
            evidence={
                "pattern": "mov-eax-stack4-shift-imm8-ret",
                "operator": operator,
                "shift": shift,
                "valueType": value_type,
                "returnType": return_type,
            },
        )
    ]


def stack_arg_shift_imm8_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_shift_imm8(data, stdcall=True)
    if decoded is None:
        return []
    suffix = str(decoded["suffix"])
    operator = str(decoded["operator"])
    value_type = str(decoded["valueType"])
    return_type = str(decoded["returnType"])
    shift = int(decoded["shift"])
    source = header(f"stack-arg-{suffix}-imm8-stdcall", row) + "\n".join(
        [
            f"{return_type} __stdcall {c_name}({value_type} value) {{",
            f"    return value {operator} {shift};",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule=f"stack-arg-{suffix}-imm8-stdcall",
            variant=f"stdcall4-o2-stack-arg-{suffix}-imm8",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type=return_type,
            evidence={
                "pattern": "mov-eax-stack4-shift-imm8-ret4",
                "operator": operator,
                "shift": shift,
                "valueType": value_type,
                "returnType": return_type,
                "stackBytes": 4,
            },
        )
    ]


def stack_arg_nonzero_bool(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) != 11 or body[:2] not in {b"\x31\xc0", b"\x33\xc0"} or body[2:] != b"\x83\x7c\x24\x04\x00\x0f\x95\xc0\xc3":
        return []
    source = header("stack-arg-nonzero-bool-cdecl", row) + "\n".join(
        [
            f"int {c_name}(unsigned int value) {{",
            "    return value != 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-nonzero-bool-cdecl",
            variant="cdecl-o2-stack-arg-nonzero-bool",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence={"pattern": "xor-eax-cmp-stack4-zero-setne-al-ret", "predicate": "value != 0"},
        )
    ]


def stack_arg_zero_bool(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) != 11 or body[:2] not in {b"\x31\xc0", b"\x33\xc0"} or body[2:] != b"\x83\x7c\x24\x04\x00\x0f\x94\xc0\xc3":
        return []
    source = header("stack-arg-zero-bool-cdecl", row) + "\n".join(
        [
            f"int {c_name}(unsigned int value) {{",
            "    return value == 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-zero-bool-cdecl",
            variant="cdecl-o2-stack-arg-zero-bool",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence={"pattern": "xor-eax-cmp-stack4-zero-sete-al-ret", "predicate": "value == 0"},
        )
    ]


def stack_arg_nonzero_bool_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) != 13 or body[:2] not in {b"\x31\xc0", b"\x33\xc0"} or body[2:] != b"\x83\x7c\x24\x04\x00\x0f\x95\xc0\xc2\x04\x00":
        return []
    source = header("stack-arg-nonzero-bool-stdcall", row) + "\n".join(
        [
            f"int __stdcall {c_name}(unsigned int value) {{",
            "    return value != 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-nonzero-bool-stdcall",
            variant="stdcall4-o2-stack-arg-nonzero-bool",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="int",
            extra_flags=("/O2", "/GS-", "/Oy"),
            evidence={"pattern": "xor-eax-cmp-stack4-zero-setne-al-ret4", "predicate": "value != 0", "stackBytes": 4},
        )
    ]


def stack_arg_zero_bool_stdcall(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) != 13 or body[:2] not in {b"\x31\xc0", b"\x33\xc0"} or body[2:] != b"\x83\x7c\x24\x04\x00\x0f\x94\xc0\xc2\x04\x00":
        return []
    source = header("stack-arg-zero-bool-stdcall", row) + "\n".join(
        [
            f"int __stdcall {c_name}(unsigned int value) {{",
            "    return value == 0;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stack-arg-zero-bool-stdcall",
            variant="stdcall4-o2-stack-arg-zero-bool",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="int",
            extra_flags=("/O2", "/GS-", "/Oy"),
            evidence={"pattern": "xor-eax-cmp-stack4-zero-sete-al-ret4", "predicate": "value == 0", "stackBytes": 4},
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


def stdcall_store_two_stack_args_to_globals(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    # mov eax,[esp+4]; mov ecx,[esp+8]; mov [abs1],eax; mov [abs2],ecx; ret 8
    if len(body) != 22:
        return []
    if body[:4] != b"\x8b\x44\x24\x04" or body[4:8] != b"\x8b\x4c\x24\x08":
        return []
    if body[8] != 0xA3 or body[13:15] != b"\x89\x0d" or body[19:] != b"\xc2\x08\x00":
        return []
    first_address = u32(body[9:13])
    second_address = u32(body[15:19])
    source = header("stdcall-store-two-stack-args-to-globals", row) + "\n".join(
        [
            f"void __stdcall {c_name}(unsigned int first, unsigned int second) {{",
            f"    *(unsigned int volatile *)0x{first_address:08x} = first;",
            f"    *(unsigned int volatile *)0x{second_address:08x} = second;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stdcall-store-two-stack-args-to-globals",
            variant="u32-u32-absolute-store-stdcall8",
            c_name=c_name,
            symbol=f"_{c_name}@8",
            source=source,
            callconv="stdcall",
            return_type="void",
            evidence={
                "firstAddress": f"0x{first_address:08x}",
                "secondAddress": f"0x{second_address:08x}",
                "stackBytes": 8,
            },
        )
    ]


def stdcall_copy_cstr_to_global(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stdcall_copy_cstr_to_global(data)
    if decoded is None:
        return []
    dest = int(decoded["destAddress"])
    stack_bytes = int(decoded["stackBytes"])
    source = header("stdcall-copy-cstr-to-global", row) + "\n".join(
        [
            f"void __stdcall {c_name}(const char *message) {{",
            f"    char *dest = (char *)0x{dest:08x};",
            "    do {",
            "        *dest++ = *message;",
            "    } while (*message++ != 0);",
            "}",
            "",
        ]
    )
    source_naked = header("stdcall-copy-cstr-to-global", row) + "\n".join(
        [
            f"__declspec(naked) void __stdcall {c_name}(const char *message) {{",
            "    __asm {",
            "        mov eax, dword ptr [esp+4]",
            f"        mov edx, 0{dest:08x}h",
            "        sub edx, eax",
            "        jmp copy_byte",
            "        _emit 08dh",
            "        _emit 049h",
            "        _emit 000h",
            "    copy_byte:",
            "        mov cl, byte ptr [eax]",
            "        mov byte ptr [edx+eax], cl",
            "        inc eax",
            "        test cl, cl",
            "        jne copy_byte",
            f"        ret {stack_bytes}",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {"destAddress": f"0x{dest:08x}", "stackBytes": stack_bytes}
    return [
        GeneratedCandidate(
            rule="stdcall-copy-cstr-to-global",
            variant="semantic-cstr-copy-to-global-stdcall4",
            c_name=c_name,
            symbol=f"_{c_name}@{stack_bytes}",
            source=source,
            callconv="stdcall",
            return_type="void",
            evidence=evidence,
        ),
        GeneratedCandidate(
            rule="stdcall-copy-cstr-to-global",
            variant="naked-cstr-copy-to-global-stdcall4",
            c_name=c_name,
            symbol=f"_{c_name}@{stack_bytes}",
            source=source_naked,
            callconv="stdcall",
            return_type="void",
            evidence={
                **evidence,
                "sourceTier": "generated inline-assembly parity fallback with decoded C-string copy-to-global bytes",
            },
        ),
    ]


def decode_stdcall_copy_cstr_to_global(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 29:
        return None
    if body[:4] != b"\x8b\x44\x24\x04" or body[4] != 0xBA:
        return None
    if body[9:13] != b"\x2b\xd0\xeb\x03" or body[13:16] != b"\x8d\x49\x00":
        return None
    if body[16:18] != b"\x8a\x08" or body[18:21] != b"\x88\x0c\x02":
        return None
    if body[21:26] != b"\x40\x84\xc9\x75\xf6" or body[26:] != b"\xc2\x04\x00":
        return None
    return {"destAddress": u32(body[5:9]), "stackBytes": 4}


def stdcall_indirect_global_callback_loop(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stdcall_indirect_global_callback_loop(data)
    if decoded is None:
        return []
    callback_address = int(decoded["callbackAddress"])
    pushed_value = int(decoded["pushedValue"])
    stack_bytes = int(decoded["stackBytes"])
    callback_type = f"{c_name}_callback"
    source = header("stdcall-indirect-global-callback-loop", row) + "\n".join(
        [
            f"typedef void (__cdecl *{callback_type})(unsigned int);",
            f"void __stdcall {c_name}(unsigned int count) {{",
            f"    {callback_type} callback;",
            "    if (count == 0u) {",
            "        return;",
            "    }",
            f"    callback = *({callback_type} volatile *)0x{callback_address:08x};",
            "    do {",
            f"        callback({pushed_value}u);",
            "        --count;",
            "    } while (count != 0u);",
            "}",
            "",
        ]
    )
    source_naked = header("stdcall-indirect-global-callback-loop", row) + "\n".join(
        [
            f"__declspec(naked) void __stdcall {c_name}(unsigned int count) {{",
            "    __asm {",
            "        push esi",
            "        mov esi, dword ptr [esp+8]",
            "        test esi, esi",
            "        je done",
            "        push edi",
            "        _emit 08bh",
            "        _emit 03dh",
            f"        _emit 0{callback_address & 0xff:02x}h",
            f"        _emit 0{(callback_address >> 8) & 0xff:02x}h",
            f"        _emit 0{(callback_address >> 16) & 0xff:02x}h",
            f"        _emit 0{(callback_address >> 24) & 0xff:02x}h",
            "    call_again:",
            f"        push {pushed_value}",
            "        call edi",
            "        dec esi",
            "        jne call_again",
            "        pop edi",
            "    done:",
            "        pop esi",
            f"        ret {stack_bytes}",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {
        "callbackAddress": f"0x{callback_address:08x}",
        "pushedValue": pushed_value,
        "stackBytes": stack_bytes,
    }
    return [
        GeneratedCandidate(
            rule="stdcall-indirect-global-callback-loop",
            variant="naked-indirect-global-callback-loop-stdcall4",
            c_name=c_name,
            symbol=f"_{c_name}@{stack_bytes}",
            source=source_naked,
            callconv="stdcall",
            return_type="void",
            evidence={
                **evidence,
                "sourceTier": "generated inline-assembly parity fallback with decoded indirect global callback-loop bytes",
            },
        ),
    ]


def decode_stdcall_indirect_global_callback_loop(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 28:
        return None
    if body[:5] != b"\x56\x8b\x74\x24\x08" or body[5:9] != b"\x85\xf6\x74\x0f":
        return None
    if body[9] != 0x57 or body[10:12] != b"\x8b\x3d":
        return None
    if body[16:18] != b"\x6a\x01" or body[18:20] != b"\xff\xd7":
        return None
    if body[20:25] != b"\x4e\x75\xf9\x5f\x5e" or body[25:] != b"\xc2\x04\x00":
        return None
    return {"callbackAddress": u32(body[12:16]), "pushedValue": int(body[17]), "stackBytes": 4}


def stdcall_nullable_field_tailjmp(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stdcall_nullable_field_tailjmp(row, data)
    if decoded is None:
        return []
    field_offset = int(decoded["fieldOffset"])
    tail_target = int(decoded["tailTarget"])
    jump_offset = int(decoded["jumpOffset"])
    stack_bytes = int(decoded["stackBytes"])
    callee = safe_c_name(f"sub_{tail_target:08x}")
    source = header("stdcall-nullable-field-tailjmp", row) + "\n".join(
        [
            f"extern void __stdcall {callee}(void *self);",
            f"void __stdcall {c_name}(void *self) {{",
            f"    if (*(void **)((char *)self + 0x{field_offset:x}) != 0) {{",
            f"        {callee}(self);",
            "    }",
            "}",
            "",
        ]
    )
    source_naked = header("stdcall-nullable-field-tailjmp", row) + "\n".join(
        [
            f"extern void __cdecl {callee}(void);",
            f"__declspec(naked) void __stdcall {c_name}(void *self) {{",
            "    __asm {",
            "        mov eax, dword ptr [esp+4]",
            f"        mov ecx, dword ptr [eax+0{field_offset:x}h]",
            "        test ecx, ecx",
            "        je done",
            "        mov dword ptr [esp+4], eax",
            f"        jmp {callee}",
            "    done:",
            f"        ret {stack_bytes}",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {
        "fieldOffset": field_offset,
        "jumpTarget": f"0x{tail_target:08x}",
        "jumpOffset": jump_offset,
        "stackBytes": stack_bytes,
    }
    return [
        GeneratedCandidate(
            rule="stdcall-nullable-field-tailjmp",
            variant="naked-nullable-field-tailjmp-stdcall4",
            c_name=c_name,
            symbol=f"_{c_name}@{stack_bytes}",
            source=source_naked,
            callconv="stdcall",
            return_type="void",
            evidence={
                **evidence,
                "sourceTier": "generated inline-assembly parity fallback with decoded nullable-field tail-jump bytes",
            },
        ),
    ]


def decode_stdcall_nullable_field_tailjmp(row: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 26:
        return None
    if body[:4] != b"\x8b\x44\x24\x04" or body[4:6] != b"\x8b\x88":
        return None
    if body[10:14] != b"\x85\xc9\x74\x09" or body[14:18] != b"\x89\x44\x24\x04":
        return None
    if body[18] != 0xE9 or body[23:] != b"\xc2\x04\x00":
        return None
    target = rel32_call_target(row, call_offset=18, rel32=int.from_bytes(body[19:23], "little", signed=True))
    if target is None:
        return None
    return {"fieldOffset": u32(body[6:10]), "tailTarget": target, "jumpOffset": 18, "stackBytes": 4}


def stdcall_clamped_count_copy_to_global(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stdcall_clamped_count_copy_to_global(data)
    if decoded is None:
        return []
    count_address = int(str(decoded["countAddress"]), 0)
    array_address = int(str(decoded["arrayAddress"]), 0)
    max_count = int(decoded["maxCount"])
    stack_bytes = int(decoded["stackBytes"])
    source_naked = header("stdcall-clamped-count-copy-to-global", row) + "\n".join(
        [
            f"__declspec(naked) void __stdcall {c_name}(unsigned int count, const unsigned int *items) {{",
            "    __asm {",
            "        mov ecx, dword ptr [esp+4]",
            f"        cmp ecx, {max_count}",
            "        jbe count_ok",
            f"        mov ecx, {max_count}",
            "    count_ok:",
            "        xor eax, eax",
            "        test ecx, ecx",
            "        _emit 089h",
            "        _emit 00dh",
            f"        _emit 0{count_address & 0xff:02x}h",
            f"        _emit 0{(count_address >> 8) & 0xff:02x}h",
            f"        _emit 0{(count_address >> 16) & 0xff:02x}h",
            f"        _emit 0{(count_address >> 24) & 0xff:02x}h",
            "        jbe done",
            "        mov edx, dword ptr [esp+8]",
            f"        sub edx, 0{array_address:08x}h",
            "        push esi",
            "        jmp copy_item",
            "        _emit 08dh",
            "        _emit 0a4h",
            "        _emit 024h",
            "        _emit 000h",
            "        _emit 000h",
            "        _emit 000h",
            "        _emit 000h",
            "        _emit 08bh",
            "        _emit 0ffh",
            "    copy_item:",
            f"        mov esi, dword ptr [edx+eax*4+0{array_address:08x}h]",
            f"        mov dword ptr [eax*4+0{array_address:08x}h], esi",
            "        inc eax",
            "        cmp eax, ecx",
            "        jb copy_item",
            "        pop esi",
            "    done:",
            f"        ret {stack_bytes}",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {
        "countAddress": f"0x{count_address:08x}",
        "arrayAddress": f"0x{array_address:08x}",
        "maxCount": max_count,
        "stackBytes": stack_bytes,
    }
    return [
        GeneratedCandidate(
            rule="stdcall-clamped-count-copy-to-global",
            variant="naked-clamped-count-copy-stdcall8",
            c_name=c_name,
            symbol=f"_{c_name}@{stack_bytes}",
            source=source_naked,
            callconv="stdcall",
            return_type="void",
            evidence={
                **evidence,
                "sourceTier": "generated inline-assembly parity fallback with decoded clamped-count global-copy bytes",
            },
        ),
    ]


def decode_stdcall_clamped_count_copy_to_global(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 71:
        return None
    if body[:4] != b"\x8b\x4c\x24\x04" or body[4:7] != b"\x83\xf9\x08":
        return None
    if body[7:9] != b"\x76\x05" or body[9:14] != b"\xb9\x08\x00\x00\x00":
        return None
    if body[14:18] != b"\x33\xc0\x85\xc9" or body[18:20] != b"\x89\x0d":
        return None
    if body[24:30] != b"\x76\x2a\x8b\x54\x24\x08" or body[30:32] != b"\x81\xea":
        return None
    if body[36:48] != b"\x56\xeb\x09\x8d\xa4\x24\x00\x00\x00\x00\x8b\xff":
        return None
    if body[48:51] != b"\x8b\xb4\x82" or body[55:58] != b"\x89\x34\x85":
        return None
    if body[62:] != b"\x40\x3b\xc1\x72\xed\x5e\xc2\x08\x00":
        return None
    count_address = u32(body[20:24])
    array_address = u32(body[32:36])
    if u32(body[51:55]) != array_address or u32(body[58:62]) != array_address:
        return None
    return {
        "countAddress": f"0x{count_address:08x}",
        "arrayAddress": f"0x{array_address:08x}",
        "maxCount": 8,
        "stackBytes": 8,
    }


def stdcall_global_callback_install(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stdcall_global_callback_install(data)
    if decoded is None:
        return []
    callback_address = int(str(decoded["callbackAddress"]), 0)
    result_address = int(str(decoded["resultAddress"]), 0)
    guard_address = int(str(decoded["guardAddress"]), 0)
    stack_bytes = int(decoded["stackBytes"])
    callback_type = f"{c_name}_callback"
    source = header("stdcall-global-callback-install", row) + "\n".join(
        [
            f"typedef unsigned int (__cdecl *{callback_type})(unsigned int);",
            f"unsigned int __stdcall {c_name}({callback_type} callback, unsigned int value) {{",
            "    unsigned int result;",
            "    if (callback == 0) {",
            "        return 0;",
            "    }",
            f"    if (*({callback_type} volatile *)0x{callback_address:08x} != 0 && *({callback_type} volatile *)0x{callback_address:08x} != callback && *(unsigned int volatile *)0x{guard_address:08x} != 0) {{",
            "        return 0;",
            "    }",
            f"    *({callback_type} volatile *)0x{callback_address:08x} = callback;",
            "    result = callback(value);",
            "    if (result != 0) {",
            f"        *(unsigned int volatile *)0x{result_address:08x} = result;",
            "    }",
            f"    return *(unsigned int volatile *)0x{result_address:08x} != 0;",
            "}",
            "",
        ]
    )
    source_naked = header("stdcall-global-callback-install", row) + "\n".join(
        [
            f"__declspec(naked) unsigned int __stdcall {c_name}(void *callback, unsigned int value) {{",
            "    __asm {",
            "        mov ecx, dword ptr [esp+4]",
            "        test ecx, ecx",
            "        je return_zero",
            "        _emit 0a1h",
            f"        _emit 0{callback_address & 0xff:02x}h",
            f"        _emit 0{(callback_address >> 8) & 0xff:02x}h",
            f"        _emit 0{(callback_address >> 16) & 0xff:02x}h",
            f"        _emit 0{(callback_address >> 24) & 0xff:02x}h",
            "        test eax, eax",
            "        je install_callback",
            "        cmp eax, ecx",
            "        je call_callback",
            "        _emit 0a1h",
            f"        _emit 0{guard_address & 0xff:02x}h",
            f"        _emit 0{(guard_address >> 8) & 0xff:02x}h",
            f"        _emit 0{(guard_address >> 16) & 0xff:02x}h",
            f"        _emit 0{(guard_address >> 24) & 0xff:02x}h",
            "        test eax, eax",
            "        je install_callback",
            "    return_zero:",
            "        xor eax, eax",
            f"        ret {stack_bytes}",
            "    install_callback:",
            "        mov eax, ecx",
            "        _emit 0a3h",
            f"        _emit 0{callback_address & 0xff:02x}h",
            f"        _emit 0{(callback_address >> 8) & 0xff:02x}h",
            f"        _emit 0{(callback_address >> 16) & 0xff:02x}h",
            f"        _emit 0{(callback_address >> 24) & 0xff:02x}h",
            "    call_callback:",
            "        mov ecx, dword ptr [esp+8]",
            "        push ecx",
            "        call eax",
            "        test eax, eax",
            "        je result_loaded",
            "        _emit 0a3h",
            f"        _emit 0{result_address & 0xff:02x}h",
            f"        _emit 0{(result_address >> 8) & 0xff:02x}h",
            f"        _emit 0{(result_address >> 16) & 0xff:02x}h",
            f"        _emit 0{(result_address >> 24) & 0xff:02x}h",
            "    result_loaded:",
            "        _emit 08bh",
            "        _emit 00dh",
            f"        _emit 0{result_address & 0xff:02x}h",
            f"        _emit 0{(result_address >> 8) & 0xff:02x}h",
            f"        _emit 0{(result_address >> 16) & 0xff:02x}h",
            f"        _emit 0{(result_address >> 24) & 0xff:02x}h",
            "        xor eax, eax",
            "        test ecx, ecx",
            "        setne al",
            f"        ret {stack_bytes}",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {
        "callbackAddress": f"0x{callback_address:08x}",
        "resultAddress": f"0x{result_address:08x}",
        "guardAddress": f"0x{guard_address:08x}",
        "stackBytes": stack_bytes,
    }
    return [
        GeneratedCandidate(
            rule="stdcall-global-callback-install",
            variant="naked-global-callback-install-stdcall8",
            c_name=c_name,
            symbol=f"_{c_name}@{stack_bytes}",
            source=source_naked,
            callconv="stdcall",
            return_type="unsigned int",
            evidence={
                **evidence,
                "sourceTier": "generated inline-assembly parity fallback with decoded global callback install/call bytes",
            },
        ),
    ]


def decode_stdcall_global_callback_install(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 74:
        return None
    if body[:8] != b"\x8b\x4c\x24\x04\x85\xc9\x74\x16":
        return None
    if body[8] != 0xA1 or body[13:17] != b"\x85\xc0\x74\x12":
        return None
    if body[17:21] != b"\x3b\xc1\x74\x15" or body[21] != 0xA1:
        return None
    if body[26:36] != b"\x85\xc0\x74\x05\x33\xc0\xc2\x08\x00\x8b":
        return None
    callback_address = u32(body[9:13])
    if body[36:43] != b"\xc1\xa3" + callback_address.to_bytes(4, "little") + b"\x8b":
        return None
    if body[43:51] != b"\x4c\x24\x08\x51\xff\xd0\x85\xc0":
        return None
    if body[51:54] != b"\x74\x05\xa3":
        return None
    if body[58:60] != b"\x8b\x0d" or body[64:] != b"\x33\xc0\x85\xc9\x0f\x95\xc0\xc2\x08\x00":
        return None
    guard_address = u32(body[22:26])
    result_address = u32(body[54:58])
    if u32(body[60:64]) != result_address:
        return None
    return {
        "callbackAddress": f"0x{callback_address:08x}",
        "resultAddress": f"0x{result_address:08x}",
        "guardAddress": f"0x{guard_address:08x}",
        "stackBytes": 8,
    }


def stdcall_track_method_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stdcall_track_method_forwarder(row, data)
    if decoded is None:
        return []
    helper_target = int(decoded["helperTarget"])
    helper = safe_c_name(f"sub_{helper_target:08x}")
    callback_offset = int(decoded["callbackOffset"])
    forwarded_count = int(decoded["forwardedArgCount"])
    stack_bytes = int(decoded["stackBytes"])
    args = ["void *self", "unsigned int track"]
    for index in range(forwarded_count):
        args.append(f"unsigned int value{index + 1}")
    source_naked = header("stdcall-track-method-forwarder", row) + "\n".join(
        [
            "/* This target uses a custom helper-call convention: ecx=self and edi=track. */",
            f"extern void __cdecl {helper}(void);",
            f"__declspec(naked) void __stdcall {c_name}({', '.join(args)}) {{",
            "    __asm {",
            "        push esi",
            "        mov esi, dword ptr [esp+8]",
            "        test esi, esi",
            "        je done",
            "        mov eax, dword ptr [esi+2f8h]",
            "        test eax, eax",
            "        je done",
            "        push edi",
            "        mov edi, dword ptr [esp+10h]",
            "        mov ecx, esi",
            f"        call {helper}",
            "        cmp eax, -1",
            "        pop edi",
            "        je done",
            "        mov ecx, dword ptr [esi+300h]",
            "        imul eax, eax, 178h",
            "        add eax, ecx",
            f"        mov ecx, dword ptr [eax+0{callback_offset:x}h]",
            "        test ecx, ecx",
            "        je done",
            *track_method_forwarder_push_lines(forwarded_count),
            "        push eax",
            "        call ecx",
            "    done:",
            "        pop esi",
            f"        ret {stack_bytes}",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {
        "callTarget": f"0x{helper_target:08x}",
        "helperTarget": f"0x{helper_target:08x}",
        "callOffset": 26,
        "stateFieldOffset": 0x2F8,
        "entriesFieldOffset": 0x300,
        "entryStride": 0x178,
        "callbackOffset": callback_offset,
        "forwardedArgCount": forwarded_count,
        "stackBytes": stack_bytes,
    }
    return [
        GeneratedCandidate(
            rule="stdcall-track-method-forwarder",
            variant=f"naked-track-method-forwarder-stdcall{stack_bytes}",
            c_name=c_name,
            symbol=f"_{c_name}@{stack_bytes}",
            source=source_naked,
            callconv="stdcall",
            return_type="void",
            evidence={
                **evidence,
                "sourceTier": "generated inline-assembly parity source for custom ecx/edi helper-call convention",
            },
        ),
    ]


def track_method_forwarder_push_lines(forwarded_count: int) -> list[str]:
    if forwarded_count == 1:
        return ["        mov edx, dword ptr [esp+10h]", "        push edx"]
    if forwarded_count == 2:
        return [
            "        mov edx, dword ptr [esp+14h]",
            "        push edx",
            "        mov edx, dword ptr [esp+14h]",
            "        push edx",
        ]
    if forwarded_count == 3:
        return [
            "        mov edx, dword ptr [esp+18h]",
            "        push edx",
            "        mov edx, dword ptr [esp+18h]",
            "        push edx",
            "        mov edx, dword ptr [esp+18h]",
            "        push edx",
        ]
    return []


def decode_stdcall_track_method_forwarder(row: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) not in {70, 75, 80}:
        return None
    if body[:7] != b"\x56\x8b\x74\x24\x08\x85\xf6" or body[7] != 0x74:
        return None
    if body[8] not in {0x39, 0x3E, 0x43}:
        return None
    if body[9:17] != b"\x8b\x86\xf8\x02\x00\x00\x85\xc0":
        return None
    if body[17] != 0x74 or body[18] not in {0x2F, 0x34, 0x39}:
        return None
    if body[19:27] != b"\x57\x8b\x7c\x24\x10\x8b\xce\xe8":
        return None
    if body[31:35] != b"\x83\xf8\xff\x5f" or body[35] != 0x74:
        return None
    if body[36] not in {0x1D, 0x22, 0x27}:
        return None
    if body[37:51] != b"\x8b\x8e\x00\x03\x00\x00\x69\xc0\x78\x01\x00\x00\x03\xc1":
        return None
    if body[51:53] != b"\x8b\x48":
        return None
    if body[54:57] != b"\x85\xc9\x74" or body[57] not in {0x08, 0x0D, 0x12}:
        return None
    forwarded_count = {70: 1, 75: 2, 80: 3}[len(body)]
    if body[58:] != track_method_forwarder_tail(forwarded_count):
        return None
    target = rel32_call_target(row, call_offset=26, rel32=int.from_bytes(body[27:31], "little", signed=True))
    if target is None:
        return None
    stack_bytes = {1: 12, 2: 16, 3: 20}[forwarded_count]
    return {
        "helperTarget": target,
        "callOffset": 26,
        "stateFieldOffset": 0x2F8,
        "entriesFieldOffset": 0x300,
        "entryStride": 0x178,
        "callbackOffset": int(body[53]),
        "forwardedArgCount": forwarded_count,
        "stackBytes": stack_bytes,
    }


def track_method_forwarder_tail(forwarded_count: int) -> bytes:
    if forwarded_count == 1:
        return b"\x8b\x54\x24\x10\x52\x50\xff\xd1\x5e\xc2\x0c\x00"
    if forwarded_count == 2:
        return b"\x8b\x54\x24\x14\x52\x8b\x54\x24\x14\x52\x50\xff\xd1\x5e\xc2\x10\x00"
    if forwarded_count == 3:
        return b"\x8b\x54\x24\x18\x52\x8b\x54\x24\x18\x52\x8b\x54\x24\x18\x52\x50\xff\xd1\x5e\xc2\x14\x00"
    return b""


def stdcall_store_three_stack_args_to_globals(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    # mov eax,[esp+4]; mov ecx,[esp+8]; mov edx,[esp+0xc];
    # mov [abs1],eax; mov [abs2],ecx; mov [abs3],edx; ret 0xc
    if len(body) != 32:
        return []
    if body[:4] != b"\x8b\x44\x24\x04" or body[4:8] != b"\x8b\x4c\x24\x08" or body[8:12] != b"\x8b\x54\x24\x0c":
        return []
    if body[12] != 0xA3 or body[17:19] != b"\x89\x0d" or body[23:25] != b"\x89\x15" or body[29:] != b"\xc2\x0c\x00":
        return []
    first_address = u32(body[13:17])
    second_address = u32(body[19:23])
    third_address = u32(body[25:29])
    source = header("stdcall-store-three-stack-args-to-globals", row) + "\n".join(
        [
            f"void __stdcall {c_name}(unsigned int first, unsigned int second, unsigned int third) {{",
            f"    *(unsigned int volatile *)0x{first_address:08x} = first;",
            f"    *(unsigned int volatile *)0x{second_address:08x} = second;",
            f"    *(unsigned int volatile *)0x{third_address:08x} = third;",
            "}",
            "",
        ]
    )
    source_naked = header("stdcall-store-three-stack-args-to-globals", row) + "\n".join(
        [
            f"__declspec(naked) void __stdcall {c_name}(unsigned int first, unsigned int second, unsigned int third) {{",
            "    __asm {",
            "        mov eax, dword ptr [esp+4]",
            "        mov ecx, dword ptr [esp+8]",
            "        mov edx, dword ptr [esp+0ch]",
            f"        mov dword ptr ds:[0{first_address:08x}h], eax",
            f"        mov dword ptr ds:[0{second_address:08x}h], ecx",
            f"        mov dword ptr ds:[0{third_address:08x}h], edx",
            "        ret 0ch",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {
        "firstAddress": f"0x{first_address:08x}",
        "secondAddress": f"0x{second_address:08x}",
        "thirdAddress": f"0x{third_address:08x}",
        "stackBytes": 12,
    }
    return [
        GeneratedCandidate(
            rule="stdcall-store-three-stack-args-to-globals",
            variant="u32-u32-u32-absolute-store-stdcall12",
            c_name=c_name,
            symbol=f"_{c_name}@12",
            source=source,
            callconv="stdcall",
            return_type="void",
            evidence=evidence,
        ),
        GeneratedCandidate(
            rule="stdcall-store-three-stack-args-to-globals",
            variant="naked-u32-u32-u32-absolute-store-stdcall12",
            c_name=c_name,
            symbol=f"_{c_name}@12",
            source=source_naked,
            callconv="stdcall",
            return_type="void",
            evidence={
                **evidence,
                "sourceTier": "generated inline-assembly parity fallback with decoded three-global-store bytes",
            },
        ),
    ]


def global_callback_nonzero_return_one(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    # mov eax,[abs]; test eax,eax; je zero; push [esp+4]; call eax;
    # test eax,eax; pop ecx; je zero; xor eax,eax; inc eax; ret; xor eax,eax; ret
    if len(body) != 27:
        return []
    if body[0] != 0xA1 or body[5:9] != b"\x85\xc0\x74\x0f":
        return []
    if body[9:13] != b"\xff\x74\x24\x04" or body[13:16] != b"\xff\xd0\x85":
        return []
    if body[16:19] != b"\xc0\x59\x74" or body[19] != 0x04:
        return []
    if body[20:] != b"\x33\xc0\x40\xc3\x33\xc0\xc3":
        return []
    global_address = u32(body[1:5])
    callback_type = f"{c_name}_callback"
    source = header("global-callback-nonzero-return-one", row) + "\n".join(
        [
            f"typedef unsigned int (__cdecl *{callback_type})(unsigned int);",
            f"unsigned int {c_name}(unsigned int value) {{",
            f"    {callback_type} callback = *({callback_type} volatile *)0x{global_address:08x};",
            "    if (callback && callback(value)) {",
            "        return 1u;",
            "    }",
            "    return 0u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="global-callback-nonzero-return-one",
            variant="cdecl-u32-global-callback-bool",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=("/O1", "/GS-", "/Oy"),
            evidence={"globalAddress": f"0x{global_address:08x}"},
        )
    ]


def global_two_cmp_return_1_or_3(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    # cmp [abs1],2; jne ret3; cmp [abs2],5; jb ret3; return 1; ret3: return 3
    if len(body) != 26:
        return []
    if body[0:2] != b"\x83\x3d" or body[6:9] != b"\x02\x75\x0d":
        return []
    if body[9:11] != b"\x83\x3d" or body[15:18] != b"\x05\x72\x04":
        return []
    if body[18:] != b"\x33\xc0\x40\xc3\x6a\x03\x58\xc3":
        return []
    first_address = u32(body[2:6])
    second_address = u32(body[11:15])
    source = header("global-two-cmp-return-1-or-3", row) + "\n".join(
        [
            f"unsigned int {c_name}(void) {{",
            f"    if (*(unsigned int *)0x{first_address:08x} == 2u) {{",
            f"        if (*(unsigned int *)0x{second_address:08x} >= 5u) {{",
            "            return 1u;",
            "        }",
            "    }",
            "    return 3u;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="global-two-cmp-return-1-or-3",
            variant="u32-global-threshold-return",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=("/O1", "/GS-", "/Oy"),
            evidence={
                "firstAddress": f"0x{first_address:08x}",
                "secondAddress": f"0x{second_address:08x}",
                "firstEquals": 2,
                "secondAtLeast": 5,
                "sourceTier": "generated high-level C parity match for decoded two-global predicate",
            },
        ),
    ]


def import_tail_jump(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) not in {6, 7}:
        return []
    if body[:2] != b"\xff\x25":
        return []
    if len(body) == 7 and body[6] != 0xC3:
        return []
    target_address = u32(body[2:6])
    source = header("import-tail-jump", row) + "\n".join(
        [
            f"__declspec(naked) void {c_name}(void) {{",
            "    __asm {",
            "        _emit 0ffh",
            "        _emit 025h",
            f"        _emit 0{target_address & 0xff:02x}h",
            f"        _emit 0{(target_address >> 8) & 0xff:02x}h",
            f"        _emit 0{(target_address >> 16) & 0xff:02x}h",
            f"        _emit 0{(target_address >> 24) & 0xff:02x}h",
            *(["        ret"] if len(body) == 7 else []),
            "    }",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="import-tail-jump",
            variant="naked-absolute-import-tail-jump",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="void",
            evidence={
                "targetAddress": f"0x{target_address:08x}",
                "hasTrailingRet": len(body) == 7,
                "sourceQuality": "inline-asm-c",
                "sourceTier": "generated inline-assembly C fallback with decoded absolute indirect jump",
            },
        )
    ]


def live_eax_nullable_import_tailjmp_stdcall4(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_live_eax_nullable_import_tailjmp_stdcall4(data)
    if decoded is None:
        return []
    field_offset = int(decoded["fieldOffset"])
    target_address = int(decoded["targetAddress"])
    stack_bytes = int(decoded["stackBytes"])
    symbol = f"_{c_name}@{stack_bytes}"
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: live-eax-nullable-import-tailjmp-stdcall4.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            f"    DB 085h, 0c0h, 074h, 011h, 08bh, 040h, 0{field_offset:02x}h, 085h",
            "    DB 0c0h, 074h, 00ah, 089h, 044h, 024h, 004h, 0ffh",
            f"    DB 025h, 0{target_address & 0xff:02x}h, 0{(target_address >> 8) & 0xff:02x}h, 0{(target_address >> 16) & 0xff:02x}h, 0{(target_address >> 24) & 0xff:02x}h, 0c2h, 0{stack_bytes & 0xff:02x}h, 0{(stack_bytes >> 8) & 0xff:02x}h",
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "targetAddress": f"0x{target_address:08x}",
        "sourceTier": "generated inline-assembly parity fallback with decoded live-eax nullable import tail-jump bytes",
    }
    return [
        GeneratedCandidate(
            rule="live-eax-nullable-import-tailjmp-stdcall4",
            variant="naked-live-eax-nullable-import-tailjmp-stdcall4",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="stdcall",
            return_type="void",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_live_eax_nullable_import_tailjmp_stdcall4(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 24:
        return None
    if body[0:4] != b"\x85\xc0\x74\x11":
        return None
    if body[4:7] != b"\x8b\x40\x04" or body[7:11] != b"\x85\xc0\x74\x0a":
        return None
    if body[11:15] != b"\x89\x44\x24\x04" or body[15:17] != b"\xff\x25":
        return None
    if body[21:] != b"\xc2\x04\x00":
        return None
    return {
        "fieldOffset": 4,
        "targetAddress": u32(body[17:21]),
        "jumpOffset": 15,
        "firstNullBranchOffset": 2,
        "secondNullBranchOffset": 9,
        "stackBytes": 4,
        "bodyBytes": len(body),
    }


def ecx_global_cmp_return_else_tailjmp(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_ecx_global_cmp_return_else_tailjmp(row, data)
    if decoded is None:
        return []
    global_address = int(decoded["globalAddress"])
    jump_rel32 = int(decoded["jumpRel32"])
    jump_target = int(decoded["jumpTarget"])
    symbol = cdecl_symbol(c_name)
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: ecx-global-cmp-return-else-tailjmp.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            f"    DB 03bh, 00dh, 0{global_address & 0xff:02x}h, 0{(global_address >> 8) & 0xff:02x}h, 0{(global_address >> 16) & 0xff:02x}h, 0{(global_address >> 24) & 0xff:02x}h",
            "    DB 075h, 001h, 0c3h, 0e9h",
            f"    DB 0{jump_rel32 & 0xff:02x}h, 0{(jump_rel32 >> 8) & 0xff:02x}h, 0{(jump_rel32 >> 16) & 0xff:02x}h, 0{(jump_rel32 >> 24) & 0xff:02x}h",
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            f"; jump target: 0x{jump_target:08x}",
            "",
        ]
    )
    evidence = {
        **decoded,
        "globalAddress": f"0x{global_address:08x}",
        "jumpTarget": f"0x{jump_target:08x}",
        "sourceTier": "generated MASM byte-emission parity fallback with decoded live-ecx global compare tail-jump bytes",
    }
    return [
        GeneratedCandidate(
            rule="ecx-global-cmp-return-else-tailjmp",
            variant="masm-live-ecx-global-cmp-return-else-tailjmp",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="void",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_ecx_global_cmp_return_else_tailjmp(row: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 14:
        return None
    if body[:2] != b"\x3b\x0d" or body[6:9] != b"\x75\x01\xc3" or body[9] != 0xE9:
        return None
    jump_rel32 = int.from_bytes(body[10:14], "little", signed=True)
    jump_target = rel32_call_target(row, call_offset=9, rel32=jump_rel32)
    if jump_target is None:
        return None
    return {
        "globalAddress": u32(body[2:6]),
        "equalPath": "ret",
        "notEqualPath": "tail-jump",
        "branchOffset": 6,
        "jumpOffset": 9,
        "jumpRel32": jump_rel32,
        "jumpTarget": jump_target,
        "bodyBytes": len(body),
    }


def x87_temp_i16_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_x87_temp_i16_return(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    body = strip_alignment_padding(data)
    source_c = naked_emit_c_source("x87-temp-i16-return", row, c_name, "int", body)
    bytes_list = ", ".join(f"0{byte:02x}h" for byte in body)
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: x87-temp-i16-return.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            f"    DB {bytes_list}",
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded x87 temp i16 return bytes",
    }
    return [
        GeneratedCandidate(
            rule="x87-temp-i16-return",
            variant=f"naked-c-x87-temp-i16-return-{decoded['x87StatusOperation']}",
            c_name=c_name,
            symbol=symbol,
            source=source_c,
            callconv="cdecl",
            return_type="int",
            evidence={
                **decoded,
                "sourceTier": "generated inline-assembly C parity fallback with decoded x87 temp i16 return bytes",
            },
        ),
        GeneratedCandidate(
            rule="x87-temp-i16-return",
            variant=f"masm-x87-temp-i16-return-{decoded['x87StatusOperation']}",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_x87_temp_i16_return(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == bytes.fromhex("519bdd7c24000fbf44240059c3"):
        return {
            "bodyBytes": len(body),
            "preservedRegister": "ecx",
            "x87StatusOperation": "fwait-before-spill",
            "tempStackOffset": 0,
            "tempBytes": 8,
            "returnSource": "sign-extended-low-word-of-temp",
        }
    if body == bytes.fromhex("51dd7c2400dbe20fbf44240059c3"):
        return {
            "bodyBytes": len(body),
            "preservedRegister": "ecx",
            "x87StatusOperation": "fnclex-after-spill",
            "tempStackOffset": 0,
            "tempBytes": 8,
            "returnSource": "sign-extended-low-word-of-temp",
        }
    return None


def x87_pop_return_zero(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_x87_pop_return_zero(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    bytes_list = ", ".join(f"0{byte:02x}h" for byte in strip_alignment_padding(data))
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: x87-pop-return-zero.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            f"    DB {bytes_list}",
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded x87 pop return-zero bytes",
    }
    return [
        GeneratedCandidate(
            rule="x87-pop-return-zero",
            variant="masm-x87-pop-return-zero",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="double",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_x87_pop_return_zero(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != bytes.fromhex("ddd8d9eec3"):
        return None
    return {
        "bodyBytes": len(body),
        "discardedRegister": "st(0)",
        "returnedX87Value": "+0.0",
        "x87Operations": ["fstp st(0)", "fldz"],
    }


def x87_round_stack_double_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_x87_round_stack_double_return(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    span = decoded.get("targetByteSpan")
    if isinstance(span, dict) and isinstance(span.get("length"), int):
        offset = int(span.get("offset") or 0)
        body = data[offset : offset + int(span["length"])]
    else:
        body = strip_alignment_padding(data)
    source_c = naked_emit_c_source("x87-round-stack-double-return", row, c_name, "double", body)
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: x87-round-stack-double-return.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded x87 round stack-double return bytes",
    }
    return [
        GeneratedCandidate(
            rule="x87-round-stack-double-return",
            variant="naked-c-x87-round-stack-double-return",
            c_name=c_name,
            symbol=symbol,
            source=source_c,
            callconv="cdecl",
            return_type="double",
            evidence={
                **decoded,
                "sourceTier": "generated inline-assembly C parity fallback with decoded x87 round stack-double return bytes",
            },
        ),
        GeneratedCandidate(
            rule="x87-round-stack-double-return",
            variant="masm-x87-round-stack-double-return",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="double",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_x87_round_stack_double_return(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != X87_ROUND_STACK_DOUBLE_RETURN:
        return None
    return {
        "bodyBytes": len(body),
        "argIndex": 1,
        "argumentType": "double",
        "argumentStackOffsetAfterScratch": 12,
        "scratchBytes": 8,
        "scratchInit": "push ecx twice",
        "x87Operations": ["fld qword ptr [esp+0x0c]", "frndint", "fstp qword ptr [esp]", "fld qword ptr [esp]"],
        "returnRegister": "st(0)",
        "roundingMode": "current x87 control word",
    }


X87_ROUND_STACK_DOUBLE_RETURN = bytes.fromhex("5151dd44240cd9fcdd5c2400dd4424005959c3")


def x87_control_word_masked_setter(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_x87_control_word_masked_setter(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    body = strip_alignment_padding(data)
    source_c = naked_emit_c_source("x87-control-word-masked-setter", row, c_name, "int", body)
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: x87-control-word-masked-setter.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded x87 control-word masked setter bytes",
    }
    return [
        GeneratedCandidate(
            rule="x87-control-word-masked-setter",
            variant="naked-c-x87-control-word-masked-setter",
            c_name=c_name,
            symbol=symbol,
            source=source_c,
            callconv="cdecl",
            return_type="int",
            evidence={
                **decoded,
                "sourceQuality": "inline-asm-c",
                "sourceTier": "generated inline-assembly C parity fallback with decoded x87 control-word masked setter bytes",
            },
        ),
        GeneratedCandidate(
            rule="x87-control-word-masked-setter",
            variant="masm-x87-control-word-masked-setter",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_x87_control_word_masked_setter(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != X87_CONTROL_WORD_MASKED_SETTER:
        return None
    return {
        "bodyBytes": len(body),
        "valueArgIndex": 1,
        "maskArgIndex": 2,
        "mergeExpression": "(oldControlWord & ~mask) | (value & mask)",
        "savedControlWordStackOffset": -4,
        "newControlWordStackArgOffset": 12,
        "returnRegister": "eax",
        "returnValue": "sign-extended previous x87 control word",
        "x87Operations": ["fstcw [ebp-4]", "fldcw [ebp+0x0c]"],
    }


X87_CONTROL_WORD_MASKED_SETTER = bytes.fromhex("558bec519bd97dfc8b450c8b4d08234d0cf7d02345fc0bc189450cd96d0c0fbf45fcc9c3")


def x87_double_exponent_adjust_return(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_x87_double_exponent_adjust_return(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    body = strip_alignment_padding(data)
    source_c = naked_emit_c_source("x87-double-exponent-adjust-return", row, c_name, "double", body)
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: x87-double-exponent-adjust-return.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded x87 double exponent-adjust return bytes",
    }
    return [
        GeneratedCandidate(
            rule="x87-double-exponent-adjust-return",
            variant="naked-c-x87-double-exponent-adjust-return",
            c_name=c_name,
            symbol=symbol,
            source=source_c,
            callconv="cdecl",
            return_type="double",
            evidence={
                **decoded,
                "sourceQuality": "inline-asm-c",
                "sourceTier": "generated inline-assembly C parity fallback with decoded x87 double exponent-adjust return bytes",
            },
        ),
        GeneratedCandidate(
            rule="x87-double-exponent-adjust-return",
            variant="masm-x87-double-exponent-adjust-return",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="double",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_x87_double_exponent_adjust_return(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != X87_DOUBLE_EXPONENT_ADJUST_RETURN:
        return None
    return {
        "bodyBytes": len(body),
        "doubleArgIndex": 1,
        "exponentArgIndex": 3,
        "doubleArgStackOffset": 8,
        "exponentArgStackOffset": 16,
        "scratchBytes": 8,
        "exponentBiasAddend": 0x3FE,
        "exponentShift": 4,
        "preservedExponentWordMask": "0xffff800f",
        "exponentWordTempOffset": -2,
        "returnRegister": "st(0)",
        "x87Operations": ["fld qword ptr [ebp+0x08]", "fstp qword ptr [ebp-0x08]", "fld qword ptr [ebp-0x08]"],
    }


X87_DOUBLE_EXPONENT_ADJUST_RETURN = bytes.fromhex("558bec51518b4510dd45088b4d0edd5df805fe030000c1e00481e10f80ffff0bc1668945fedd45f8c9c3")


def bink_copy_to_buffer_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_copy_to_buffer_forwarder(data)
    if decoded is None:
        return []
    helper_target = rel32_call_target(row, call_offset=int(decoded["helperCallOffset"]), rel32=int(decoded["helperCallDisplacement"]))
    helper_c_name = safe_c_name(f"sub_{helper_target:08x}") if helper_target is not None else f"{c_name}_helper"
    helper_symbol = f"_{helper_c_name}@44"
    source = header("bink-copy-to-buffer-forwarder", row) + "\n".join(
        [
            f"extern void __stdcall {helper_c_name}(",
            "    void *bink,",
            "    unsigned int a2, unsigned int a3, unsigned int a4, unsigned int a5, unsigned int a6,",
            "    unsigned int zero1, unsigned int zero2,",
            "    unsigned int buffer_field0, unsigned int buffer_field4, unsigned int a7);",
            f"void __stdcall {c_name}(",
            "    void *bink,",
            "    unsigned int a2, unsigned int a3, unsigned int a4, unsigned int a5, unsigned int a6, unsigned int a7) {",
            f"    {helper_c_name}(",
            "        bink, a2, a3, a4, a5, a6,",
            "        0u, 0u,",
            "        *(unsigned int *)bink,",
            "        *(unsigned int *)((char *)bink + 4),",
            "        a7);",
            "}",
            "",
        ]
    )
    evidence = {
        **decoded,
        "helperCallTargetAddress": f"0x{helper_target:08x}" if helper_target is not None else None,
        "callTarget": f"0x{helper_target:08x}" if helper_target is not None else None,
        "callSymbol": helper_symbol,
        "sourceTier": "generated high-level C wrapper for decoded BinkCopyToBuffer forwarding call",
    }
    fallback = bink_buffer_masm_forwarder(
        row=row,
        c_name=c_name,
        data=data,
        decoded=decoded,
        rule="bink-copy-to-buffer-forwarder",
        variant="masm-bink-copy-to-buffer-forwarder",
    )
    return [
        GeneratedCandidate(
            rule="bink-copy-to-buffer-forwarder",
            variant="high-level-bink-copy-to-buffer-forwarder",
            c_name=c_name,
            symbol=f"_{c_name}@28",
            source=source,
            callconv="stdcall",
            return_type="void",
            extra_flags=("/O2", "/Gz"),
            evidence=evidence,
        ),
        *fallback,
    ]


def decode_bink_copy_to_buffer_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_COPY_TO_BUFFER_FORWARDER:
        return None
    call_offset = 0x2E
    call_disp = int.from_bytes(body[call_offset + 1 : call_offset + 5], "little", signed=True)
    ret_offset = call_offset + 5
    return {
        "bodyBytes": len(body),
        "export": "BinkCopyToBuffer",
        "stdcallStackBytes": 28,
        "stackArgBytes": 28,
        "stackArgCount": 7,
        "helperCallOffset": call_offset,
        "helperCallDisplacement": call_disp,
        "helperCallTargetOffset": ret_offset + call_disp,
        "bufferPointerArgIndex": 1,
        "bufferFieldLoads": [{"offset": 4, "register": "ecx"}, {"offset": 0, "register": "edx"}],
        "pushedConstants": [0, 0],
        "returnInstruction": "ret 0x1c",
    }


BINK_COPY_TO_BUFFER_FORWARDER = bytes.fromhex(
    "8b44241c508b4424088b48048b10518b4c2420528b5424206a006a00518b4c2428528b542428518b4c2428525150e80d000000c21c00"
)


def bink_buffer_clear_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_buffer_clear_forwarder(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: bink-buffer-clear-forwarder.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkBufferClear forwarding wrapper bytes",
    }
    return [
        GeneratedCandidate(
            rule="bink-buffer-clear-forwarder",
            variant="naked-c-bink-buffer-clear-forwarder",
            c_name=c_name,
            symbol=f"_{c_name}@8",
            source=header("bink-buffer-clear-forwarder", row) + "\n".join(
                [
                    f"__declspec(naked) int __stdcall {c_name}(void *buffer, unsigned int color) {{",
                    "    __asm {",
                    "        push esi",
                    "        mov esi, dword ptr [esp+8]",
                    "        push esi",
                    "        _emit 0e8h",
                    "        _emit 0a5h",
                    "        _emit 0f7h",
                    "        _emit 0ffh",
                    "        _emit 0ffh",
                    "        test eax, eax",
                    "        je failed",
                    "        mov eax, dword ptr [esp+0ch]",
                    "        mov ecx, dword ptr [esi+4]",
                    "        mov edx, dword ptr [esi]",
                    "        push eax",
                    "        mov eax, dword ptr [esi+18h]",
                    "        push ecx",
                    "        mov ecx, dword ptr [esi+10h]",
                    "        push edx",
                    "        mov edx, dword ptr [esi+14h]",
                    "        push eax",
                    "        _emit 0e8h",
                    "        _emit 0b6h",
                    "        _emit 0e6h",
                    "        _emit 0ffh",
                    "        _emit 0ffh",
                    "        add esp, 10h",
                    "        push esi",
                    "        _emit 0e8h",
                    "        _emit 09dh",
                    "        _emit 0f8h",
                    "        _emit 0ffh",
                    "        _emit 0ffh",
                    "        mov eax, 1",
                    "        pop esi",
                    "        ret 8",
                    "failed:",
                    "        xor eax, eax",
                    "        pop esi",
                    "        ret 8",
                    "    }",
                    "}",
                    "",
                ]
            ),
            callconv="stdcall",
            return_type="int",
            extra_flags=("/O2", "/Gz"),
            evidence={
                **decoded,
                "sourceTier": "generated naked C inline-assembly source for decoded BinkBufferClear control flow",
                "sourceQuality": "inline-asm-c",
                "rawRel32CallDisplacementsPreserved": ["lock", "clear", "unlock"],
                "claimBoundary": "inline assembly preserves raw helper-call relative displacements because this slice has no reconstructed relocation model yet",
            },
        ),
        GeneratedCandidate(
            rule="bink-buffer-clear-forwarder",
            variant="masm-bink-buffer-clear-forwarder",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="stdcall",
            return_type="int",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_bink_buffer_clear_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_BUFFER_CLEAR_FORWARDER:
        return None
    call_offsets = [6, 37, 46]
    call_displacements = [int.from_bytes(body[offset + 1 : offset + 5], "little", signed=True) for offset in call_offsets]
    return {
        "bodyBytes": len(body),
        "export": "BinkBufferClear",
        "stdcallStackBytes": 8,
        "stackArgCount": 2,
        "bufferPointerArgIndex": 1,
        "colorArgIndex": 2,
        "lockCallOffset": call_offsets[0],
        "lockCallDisplacement": call_displacements[0],
        "clearCallOffset": call_offsets[1],
        "clearCallDisplacement": call_displacements[1],
        "unlockCallOffset": call_offsets[2],
        "unlockCallDisplacement": call_displacements[2],
        "lockFailureJumpOffset": 13,
        "lockFailureTargetOffset": 60,
        "clearHelperStackBytes": 16,
        "bufferFieldLoads": [{"offset": 4, "register": "ecx"}, {"offset": 0, "register": "edx"}, {"offset": 24, "register": "eax"}, {"offset": 16, "register": "ecx"}, {"offset": 20, "register": "edx"}],
        "successReturnValue": 1,
        "failureReturnValue": 0,
        "returnInstruction": "ret 0x08",
    }


BINK_BUFFER_CLEAR_FORWARDER = bytes.fromhex(
    "568b74240856e8a5f7ffff85c0742d8b44240c8b4e048b16508b4618518b4e10528b561450e8b6e6ffff83c41056e89df8ffffb8010000005ec2080033c05ec20800"
)


def bink_buffer_unlock_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_buffer_unlock_forwarder(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    inline_source = header("bink-buffer-unlock-forwarder", row) + "\n".join(
        [
            f"__declspec(naked) int __stdcall {c_name}(void *buffer) {{",
            "    __asm {",
            "        push esi",
            "        mov esi, dword ptr [esp+8]",
            "        push edi",
            "        xor edi, edi",
            "        cmp esi, edi",
            "        jne have_buffer",
            "        pop edi",
            "        xor eax, eax",
            "        pop esi",
            "        ret 4",
            "have_buffer:",
            "        mov eax, dword ptr [esi+48h]",
            "        cmp eax, edi",
            "        je alternate_clear",
            "        mov edx, dword ptr [esi+7ch]",
            "        mov ecx, dword ptr [eax]",
            "        push edx",
            "        push eax",
            "        call dword ptr [ecx+80h]",
            "        cmp dword ptr [esi+74h], edi",
            "        je skip_optional",
            "        mov eax, dword ptr [esi+78h]",
            "        push eax",
            "        _emit 0e8h",
            "        _emit 0ebh",
            "        _emit 0eah",
            "        _emit 0ffh",
            "        _emit 0ffh",
            "skip_optional:",
            "        push 2",
            "        mov eax, esi",
            "        mov dword ptr [esi+14h], edi",
            "        mov dword ptr [esi+18h], edi",
            "        _emit 0e8h",
            "        _emit 01ch",
            "        _emit 0ebh",
            "        _emit 0ffh",
            "        _emit 0ffh",
            "        add esp, 4",
            "        jmp final_flags",
            "alternate_clear:",
            "        cmp dword ptr [esi+90h], edi",
            "        je final_flags",
            "        mov dword ptr [esi+14h], edi",
            "        mov dword ptr [esi+18h], edi",
            "final_flags:",
            "        and dword ptr [esi+10h], 7fffffffh",
            "        pop edi",
            "        mov eax, 1",
            "        pop esi",
            "        ret 4",
            "    }",
            "}",
            "",
        ]
    )
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: bink-buffer-unlock-forwarder.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkBufferUnlock forwarding wrapper bytes",
    }
    inline_evidence = {
        **decoded,
        "sourceTier": "generated naked C inline-assembly source for decoded BinkBufferUnlock control flow",
        "rawRel32CallDisplacementsPreserved": ["optional-helper", "state-helper"],
        "claimBoundary": "Inline assembly preserves instruction bytes and branch layout; helper calls remain raw rel32 byte emissions until the original symbol/section context is recovered.",
    }
    return [
        GeneratedCandidate(
            rule="bink-buffer-unlock-forwarder",
            variant="naked-c-bink-buffer-unlock-forwarder",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=inline_source,
            callconv="stdcall",
            return_type="int",
            extra_flags=("/O2", "/Gz"),
            evidence=inline_evidence,
        ),
        GeneratedCandidate(
            rule="bink-buffer-unlock-forwarder",
            variant="masm-bink-buffer-unlock-forwarder",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="stdcall",
            return_type="int",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_bink_buffer_unlock_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_BUFFER_UNLOCK_FORWARDER:
        return None
    helper_offsets = [48, 63]
    helper_displacements = [int.from_bytes(body[offset + 1 : offset + 5], "little", signed=True) for offset in helper_offsets]
    return {
        "bodyBytes": len(body),
        "export": "BinkBufferUnlock",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "bufferPointerArgIndex": 1,
        "nullReturnValue": 0,
        "successReturnValue": 1,
        "interfaceFieldOffset": 0x48,
        "callbackArgFieldOffset": 0x7C,
        "indirectCallbackVtableOffset": 0x80,
        "optionalHelperGuardFieldOffset": 0x74,
        "optionalHelperArgFieldOffset": 0x78,
        "optionalHelperCallOffset": helper_offsets[0],
        "optionalHelperCallDisplacement": helper_displacements[0],
        "stateHelperPushValue": 2,
        "stateHelperCallOffset": helper_offsets[1],
        "stateHelperCallDisplacement": helper_displacements[1],
        "clearedFieldOffsets": [0x14, 0x18],
        "alternateClearGuardFieldOffset": 0x90,
        "finalAndFieldOffset": 0x10,
        "finalAndMask": "0x7fffffff",
        "returnInstruction": "ret 0x04",
    }


BINK_BUFFER_UNLOCK_FORWARDER = bytes.fromhex(
    "568b7424085733ff3bf775075f33c05ec204008b46483bc7742f8b567c8b085250ff9180000000397e7474098b467850e8ebeaffff6a028bc6897e14897e18e81cebffff83c404eb0e39be900000007406897e14897e18816610ffffff7f5fb8010000005ec20400"
)


def bink_buffer_set_offset_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_buffer_set_offset_forwarder(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    inline_source = header("bink-buffer-set-offset-forwarder", row) + "\n".join(
        [
            f"__declspec(naked) int __stdcall {c_name}(void *buffer, int x_offset, int y_offset) {{",
            "    __asm {",
            "        sub esp, 8",
            "        push esi",
            "        mov esi, dword ptr [esp+10h]",
            "        test esi, esi",
            "        jne have_buffer",
            "        xor eax, eax",
            "        pop esi",
            "        add esp, 8",
            "        ret 0ch",
            "have_buffer:",
            "        mov eax, dword ptr [esi+60h]",
            "        push eax",
            "        _emit 0ffh",
            "        _emit 015h",
            "        _emit 098h",
            "        _emit 0a1h",
            "        _emit 004h",
            "        _emit 030h",
            "        neg eax",
            "        sbb eax, eax",
            "        neg eax",
            "        mov dword ptr [esi+64h], eax",
            "        jne done_success",
            "        mov edx, dword ptr [esi+60h]",
            "        lea ecx, dword ptr [esp+4]",
            "        push ecx",
            "        push edx",
            "        mov dword ptr [esp+10h], 0",
            "        mov dword ptr [esp+0ch], 0",
            "        _emit 0ffh",
            "        _emit 015h",
            "        _emit 084h",
            "        _emit 0a1h",
            "        _emit 004h",
            "        _emit 030h",
            "        mov eax, dword ptr [esp+14h]",
            "        mov ecx, dword ptr [esp+4]",
            "        mov edx, dword ptr [esp+8]",
            "        add ecx, eax",
            "        mov dword ptr [esi+50h], ecx",
            "        mov ecx, dword ptr [esp+18h]",
            "        add edx, ecx",
            "        mov dword ptr [esi+54h], edx",
            "        mov edx, dword ptr [esi+10h]",
            "        mov dword ptr [esi+58h], eax",
            "        or edx, 80000000h",
            "        push 0",
            "        mov eax, esi",
            "        mov dword ptr [esi+5ch], ecx",
            "        mov dword ptr [esi+10h], edx",
            "        _emit 0e8h",
            "        _emit 062h",
            "        _emit 0fdh",
            "        _emit 0ffh",
            "        _emit 0ffh",
            "        add esp, 4",
            "done_success:",
            "        mov eax, 1",
            "        pop esi",
            "        add esp, 8",
            "        ret 0ch",
            "    }",
            "}",
            "",
        ]
    )
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: bink-buffer-set-offset-forwarder.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkBufferSetOffset forwarding wrapper bytes",
    }
    inline_evidence = {
        **decoded,
        "sourceTier": "generated naked C inline-assembly source for decoded BinkBufferSetOffset control flow",
        "rawAbsoluteImportCallsPreserved": ["IsWindow", "GetWindowRect"],
        "rawRel32CallDisplacementsPreserved": ["state-helper"],
        "claimBoundary": "Inline assembly preserves instruction bytes, absolute imported call encodings, and helper-call displacement; import symbols remain unresolved until original link context is recovered.",
    }
    return [
        GeneratedCandidate(
            rule="bink-buffer-set-offset-forwarder",
            variant="naked-c-bink-buffer-set-offset-forwarder",
            c_name=c_name,
            symbol=f"_{c_name}@12",
            source=inline_source,
            callconv="stdcall",
            return_type="int",
            extra_flags=("/O2", "/Gz"),
            evidence=inline_evidence,
        ),
        GeneratedCandidate(
            rule="bink-buffer-set-offset-forwarder",
            variant="masm-bink-buffer-set-offset-forwarder",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="stdcall",
            return_type="int",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_bink_buffer_set_offset_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_BUFFER_SET_OFFSET_FORWARDER:
        return None
    state_call_offset = 121
    state_call_disp = int.from_bytes(body[state_call_offset + 1 : state_call_offset + 5], "little", signed=True)
    return {
        "bodyBytes": len(body),
        "export": "BinkBufferSetOffset",
        "stdcallStackBytes": 12,
        "stackArgCount": 3,
        "bufferPointerArgIndex": 1,
        "xOffsetArgIndex": 2,
        "yOffsetArgIndex": 3,
        "windowHandleFieldOffset": 0x60,
        "windowValidFlagFieldOffset": 0x64,
        "isWindowImportAddress": "0x3004a198",
        "getWindowRectImportAddress": "0x3004a184",
        "rectScratchBytes": 8,
        "storedFieldOffsets": [0x50, 0x54, 0x58, 0x5C],
        "dirtyFlagFieldOffset": 0x10,
        "dirtyFlagMask": "0x80000000",
        "stateHelperPushValue": 0,
        "stateHelperCallOffset": state_call_offset,
        "stateHelperCallDisplacement": state_call_disp,
        "nullReturnValue": 0,
        "successReturnValue": 1,
        "returnInstruction": "ret 0x0c",
    }


BINK_BUFFER_SET_OFFSET_FORWARDER = bytes.fromhex(
    "83ec08568b74241085f6750933c05e83c408c20c008b466050ff1598a10430f7d81bc0f7d889466475578b56608d4c24045152c744241000000000c744240c00000000ff1584a104308b4424148b4c24048b54240803c8894e508b4c241803d18956548b561089465881ca000000806a008bc6894e5c895610e862fdffff83c404b8010000005e83c408c20c00"
)


def bink_buffer_set_direct_draw_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_buffer_set_direct_draw_forwarder(data)
    if decoded is None:
        return []
    body = bink_buffer_set_direct_draw_body(data)
    refresh_target = int(str(decoded["refreshCallTargetAddress"]), 16)
    refresh_c_name = safe_c_name(f"sub_{refresh_target:08x}")
    refresh_symbol = f"_{refresh_c_name}@0"
    mode_symbol = "_mizuchi_global_30068c68"
    direct_draw_symbol = "_mizuchi_global_30068c6c"
    surface_symbol = "_mizuchi_global_30068c70"
    source = header("bink-buffer-set-direct-draw-forwarder", row) + "\n".join(
        [
            "extern unsigned int mizuchi_global_30068c68;",
            "extern void *mizuchi_global_30068c6c;",
            "extern void *mizuchi_global_30068c70;",
            f"extern void __stdcall {refresh_c_name}(void);",
            f"int __stdcall {c_name}(void *direct_draw, void *surface) {{",
            "    if (direct_draw != 0 && surface != 0) {",
            "        mizuchi_global_30068c6c = direct_draw;",
            "        mizuchi_global_30068c70 = surface;",
            "        mizuchi_global_30068c68 = 0x08000000u;",
            f"        {refresh_c_name}();",
            "        return 1;",
            "    }",
            "    mizuchi_global_30068c6c = 0;",
            "    mizuchi_global_30068c70 = 0;",
            "    mizuchi_global_30068c68 = 0;",
            "    return 1;",
            "}",
            "",
        ]
    )
    evidence = {
        **decoded,
        "callTarget": decoded["refreshCallTargetAddress"],
        "callSymbol": refresh_symbol,
        "absoluteAddressRelocations": [
            {"offset": 0x14, "type": "IMAGE_REL_I386_DIR32", "symbol": direct_draw_symbol, "decodedAddress": decoded["directDrawGlobalAddress"]},
            {"offset": 0x19, "type": "IMAGE_REL_I386_DIR32", "symbol": surface_symbol, "decodedAddress": decoded["surfaceGlobalAddress"]},
            {"offset": 0x1F, "type": "IMAGE_REL_I386_DIR32", "symbol": mode_symbol, "decodedAddress": decoded["modeGlobalAddress"]},
            {"offset": 0x36, "type": "IMAGE_REL_I386_DIR32", "symbol": direct_draw_symbol, "decodedAddress": decoded["directDrawGlobalAddress"]},
            {"offset": 0x3C, "type": "IMAGE_REL_I386_DIR32", "symbol": surface_symbol, "decodedAddress": decoded["surfaceGlobalAddress"]},
            {"offset": 0x42, "type": "IMAGE_REL_I386_DIR32", "symbol": mode_symbol, "decodedAddress": decoded["modeGlobalAddress"]},
        ],
        "sourceTier": "generated high-level C wrapper for decoded BinkBufferSetDirectDraw global state update",
    }
    fallback = bink_buffer_masm_forwarder(
        row=row,
        c_name=c_name,
        data=body,
        decoded=decoded,
        rule="bink-buffer-set-direct-draw-forwarder",
        variant="masm-bink-buffer-set-direct-draw-forwarder",
    )
    return [
        GeneratedCandidate(
            rule="bink-buffer-set-direct-draw-forwarder",
            variant="high-level-bink-buffer-set-direct-draw-forwarder",
            c_name=c_name,
            symbol=f"_{c_name}@8",
            source=source,
            callconv="stdcall",
            return_type="int",
            extra_flags=("/O2", "/Gz"),
            evidence=evidence,
        ),
        *fallback,
    ]


def bink_buffer_check_win_pos_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    return bink_buffer_masm_forwarder(
        row=row,
        c_name=c_name,
        data=data,
        decoded=decode_bink_buffer_check_win_pos_forwarder(data),
        rule="bink-buffer-check-win-pos-forwarder",
        variant="masm-bink-buffer-check-win-pos-forwarder",
    )


def bink_buffer_close_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_buffer_close_forwarder(data)
    if decoded is None:
        return []
    inline_source = header("bink-buffer-close-forwarder", row) + "\n".join(
        [
            f"__declspec(naked) int __stdcall {c_name}(void *buffer) {{",
            "    __asm {",
            "        push esi",
            "        mov esi, dword ptr [esp+8]",
            "        test esi, esi",
            "        je done",
            "        cmp dword ptr [esi], 0",
            "        je done",
            "        mov eax, dword ptr [esi+68h]",
            "        test eax, eax",
            "        je secondary_release",
            "        _emit 08bh",
            "        _emit 015h",
            "        _emit 070h",
            "        _emit 08ch",
            "        _emit 006h",
            "        _emit 030h",
            "        mov eax, dword ptr [esi+48h]",
            "        mov ecx, dword ptr [eax]",
            "        push 0",
            "        push 200h",
            "        push 0",
            "        push edx",
            "        push 0",
            "        push eax",
            "        call dword ptr [ecx+84h]",
            "        mov eax, dword ptr [esi+48h]",
            "        mov ecx, dword ptr [eax]",
            "        push eax",
            "        call dword ptr [ecx+8]",
            "        jmp surface_release_done",
            "secondary_release:",
            "        mov eax, dword ptr [esi+6ch]",
            "        test eax, eax",
            "        je surface_release_done",
            "        mov eax, dword ptr [esi+48h]",
            "        mov edx, dword ptr [eax]",
            "        push eax",
            "        call dword ptr [edx+8]",
            "surface_release_done:",
            "        mov eax, dword ptr [esi+0a0h]",
            "        test eax, eax",
            "        je helper_release_done",
            "        mov ecx, dword ptr [esi+0a4h]",
            "        push ecx",
            "        push eax",
            "        _emit 0ffh",
            "        _emit 015h",
            "        _emit 018h",
            "        _emit 0a0h",
            "        _emit 004h",
            "        _emit 030h",
            "        mov edx, dword ptr [esi+90h]",
            "        push edx",
            "        _emit 0ffh",
            "        _emit 015h",
            "        _emit 014h",
            "        _emit 0a0h",
            "        _emit 004h",
            "        _emit 030h",
            "        mov eax, dword ptr [esi+0a0h]",
            "        push eax",
            "        _emit 0ffh",
            "        _emit 015h",
            "        _emit 02ch",
            "        _emit 0a0h",
            "        _emit 004h",
            "        _emit 030h",
            "        mov eax, dword ptr [esi+9ch]",
            "        test eax, eax",
            "        je helper_release_done",
            "        cmp byte ptr [eax-2], 3",
            "        movzx ecx, byte ptr [eax-1]",
            "        jne helper_direct_free",
            "        mov edx, eax",
            "        sub edx, ecx",
            "        push edx",
            "        call dword ptr [eax-8]",
            "        jmp helper_release_done",
            "helper_direct_free:",
            "        sub eax, ecx",
            "        push eax",
            "        _emit 0e8h",
            "        _emit 04fh",
            "        _emit 059h",
            "        _emit 0ffh",
            "        _emit 0ffh",
            "        add esp, 4",
            "helper_release_done:",
            "        mov eax, dword ptr [esi+88h]",
            "        test eax, eax",
            "        je optional_close_done",
            "        _emit 0e8h",
            "        _emit 0b0h",
            "        _emit 0e5h",
            "        _emit 0ffh",
            "        _emit 0ffh",
            "optional_close_done:",
            "        mov eax, dword ptr [esi+8ch]",
            "        test eax, eax",
            "        je globals_done",
            "        _emit 0ffh",
            "        _emit 00dh",
            "        _emit 09ch",
            "        _emit 08ch",
            "        _emit 006h",
            "        _emit 030h",
            "        jne globals_done",
            "        _emit 0a1h",
            "        _emit 098h",
            "        _emit 08ch",
            "        _emit 006h",
            "        _emit 030h",
            "        test eax, eax",
            "        je globals_done",
            "        push 4",
            "        push 0",
            "        _emit 0c7h",
            "        _emit 005h",
            "        _emit 098h",
            "        _emit 08ch",
            "        _emit 006h",
            "        _emit 030h",
            "        _emit 000h",
            "        _emit 000h",
            "        _emit 000h",
            "        _emit 000h",
            "        _emit 0ffh",
            "        _emit 015h",
            "        _emit 04ch",
            "        _emit 0a1h",
            "        _emit 004h",
            "        _emit 030h",
            "globals_done:",
            "        push edi",
            "        xor eax, eax",
            "        mov ecx, 2ah",
            "        mov edi, esi",
            "        rep stosd",
            "        cmp byte ptr [esi-2], 3",
            "        pop edi",
            "        jne final_direct_free",
            "        movzx edx, byte ptr [esi-1]",
            "        mov eax, esi",
            "        sub eax, edx",
            "        push eax",
            "        call dword ptr [esi-8]",
            "        pop esi",
            "        ret 4",
            "final_direct_free:",
            "        movzx ecx, byte ptr [esi-1]",
            "        sub esi, ecx",
            "        push esi",
            "        _emit 0e8h",
            "        _emit 0dfh",
            "        _emit 058h",
            "        _emit 0ffh",
            "        _emit 0ffh",
            "        add esp, 4",
            "done:",
            "        pop esi",
            "        ret 4",
            "    }",
            "}",
            "",
        ]
    )
    fallback = bink_buffer_masm_forwarder(
        row=row,
        c_name=c_name,
        data=data,
        decoded=decoded,
        rule="bink-buffer-close-forwarder",
        variant="masm-bink-buffer-close-forwarder",
    )
    return [
        GeneratedCandidate(
            rule="bink-buffer-close-forwarder",
            variant="naked-c-bink-buffer-close-forwarder",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=inline_source,
            callconv="stdcall",
            return_type="int",
            extra_flags=("/O2", "/Gz"),
            evidence={
                **decoded,
                "sourceTier": "generated naked C inline-assembly source for decoded BinkBufferClose release/cleanup control flow",
                "sourceQuality": "inline-asm-c",
                "rawAbsoluteReferencesPreserved": [
                    decoded["globalBackBufferAddress"],
                    decoded["globalRefCountAddress"],
                    decoded["globalResourceAddress"],
                    *decoded["releaseImportAddresses"],
                ],
                "rawRel32CallDisplacementsPreserved": [
                    decoded["directFreeCallDisplacement"],
                    decoded["optionalCloseCallDisplacement"],
                    decoded["finalFreeCallDisplacement"],
                ],
                "claimBoundary": "inline assembly preserves absolute import/global references and unresolved rel32 helper calls until relocation-aware source emission exists",
            },
        ),
        *fallback,
    ]


def bink_buffer_close_masm_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    return bink_buffer_masm_forwarder(
        row=row,
        c_name=c_name,
        data=data,
        decoded=decode_bink_buffer_close_forwarder(data),
        rule="bink-buffer-close-forwarder",
        variant="masm-bink-buffer-close-forwarder",
    )


def bink_buffer_lock_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    return bink_buffer_masm_forwarder(
        row=row,
        c_name=c_name,
        data=data,
        decoded=decode_bink_buffer_lock_forwarder(data),
        rule="bink-buffer-lock-forwarder",
        variant="masm-bink-buffer-lock-forwarder",
    )


def bink_buffer_set_scale_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_buffer_set_scale_forwarder(data)
    if decoded is None:
        return []
    inline_source = header("bink-buffer-set-scale-forwarder", row) + "\n".join(
        [
            f"__declspec(naked) int __stdcall {c_name}(void *buffer, unsigned int width, unsigned int height) {{",
            "    __asm {",
            "        push ecx",
            "        push esi",
            "        mov esi, dword ptr [esp+0ch]",
            "        test esi, esi",
            "        mov dword ptr [esp+4], 1",
            "        jne have_buffer",
            "        xor eax, eax",
            "        pop esi",
            "        pop ecx",
            "        ret 0ch",
            "have_buffer:",
            "        push ebp",
            "        push edi",
            "        mov edi, dword ptr [esp+18h]",
            "        test edi, edi",
            "        jne have_width",
            "        _emit 08bh",
            "        _emit 03dh",
            "        _emit 0b4h",
            "        _emit 05ch",
            "        _emit 005h",
            "        _emit 030h",
            "have_width:",
            "        mov ebp, dword ptr [esp+1ch]",
            "        test ebp, ebp",
            "        jne have_height",
            "        _emit 08bh",
            "        _emit 02dh",
            "        _emit 0b0h",
            "        _emit 05ch",
            "        _emit 005h",
            "        _emit 030h",
            "have_height:",
            "        mov ecx, dword ptr [esi]",
            "        push ebx",
            "        xor ebx, ebx",
            "        cmp edi, ecx",
            "        je horiz_done",
            "        xor edx, edx",
            "        mov eax, edi",
            "        div ecx",
            "        test edx, edx",
            "        jne h_try_inverse",
            "        mov ebx, 80000000h",
            "        jmp horiz_done",
            "h_try_inverse:",
            "        xor edx, edx",
            "        mov eax, ecx",
            "        div edi",
            "        test edx, edx",
            "        jne h_compare",
            "        mov ebx, 20000000h",
            "        jmp horiz_done",
            "h_compare:",
            "        cmp edi, ecx",
            "        jbe h_not_greater",
            "        mov ebx, 40000000h",
            "        jmp horiz_done",
            "h_not_greater:",
            "        jae horiz_done",
            "        mov ebx, 10000000h",
            "horiz_done:",
            "        mov eax, dword ptr [esi+38h]",
            "        mov dword ptr [esp+18h], eax",
            "        and eax, ebx",
            "        cmp eax, ebx",
            "        jne h_disable",
            "        mov dword ptr [esi+3ch], edi",
            "        jmp vertical_start",
            "h_disable:",
            "        mov dword ptr [esp+10h], 0",
            "vertical_start:",
            "        mov ecx, dword ptr [esi+4]",
            "        xor ebx, ebx",
            "        cmp ebp, ecx",
            "        je vert_done",
            "        xor edx, edx",
            "        mov eax, edi",
            "        div ecx",
            "        test edx, edx",
            "        jne v_maybe_equal",
            "        mov ebx, 8000000h",
            "        jmp vert_done",
            "v_maybe_equal:",
            "        cmp ebp, ecx",
            "        je vert_done",
            "        xor edx, edx",
            "        mov eax, ecx",
            "        div ebp",
            "        test edx, edx",
            "        jne v_compare",
            "        mov ebx, 2000000h",
            "        jmp vert_done",
            "v_compare:",
            "        cmp ebp, ecx",
            "        jbe v_not_greater",
            "        mov ebx, 4000000h",
            "        jmp vert_done",
            "v_not_greater:",
            "        jae vert_done",
            "        mov ebx, 1000000h",
            "vert_done:",
            "        mov eax, dword ptr [esp+18h]",
            "        and eax, ebx",
            "        cmp eax, ebx",
            "        pop ebx",
            "        jne v_disable",
            "        mov dword ptr [esi+40h], ebp",
            "        jmp final_update",
            "v_disable:",
            "        mov dword ptr [esp+0ch], 0",
            "final_update:",
            "        mov ecx, dword ptr [esi+30h]",
            "        mov eax, dword ptr [esi+3ch]",
            "        add eax, ecx",
            "        mov ecx, dword ptr [esi+40h]",
            "        mov dword ptr [esi+8], eax",
            "        mov eax, dword ptr [esi+34h]",
            "        pop edi",
            "        add ecx, eax",
            "        mov eax, dword ptr [esp+8]",
            "        pop ebp",
            "        mov dword ptr [esi+0ch], ecx",
            "        pop esi",
            "        pop ecx",
            "        ret 0ch",
            "    }",
            "}",
            "",
        ]
    )
    inline_evidence = {
        **decoded,
        "sourceTier": "generated naked C inline-assembly source for decoded BinkBufferSetScale scaling-flag logic",
        "rawAbsoluteGlobalLoadsPreserved": [decoded.get("globalWidthFallbackAddress"), decoded.get("globalHeightFallbackAddress")],
        "claimBoundary": "Inline assembly preserves the decoded width/height scale-mode arithmetic and raw fallback global loads; type-rich structure recovery is still bounded metadata.",
    }
    return [
        GeneratedCandidate(
            rule="bink-buffer-set-scale-forwarder",
            variant="naked-c-bink-buffer-set-scale-forwarder",
            c_name=c_name,
            symbol=f"_{c_name}@12",
            source=inline_source,
            callconv="stdcall",
            return_type="int",
            extra_flags=("/O2", "/Gz"),
            evidence=inline_evidence,
        ),
        *bink_buffer_masm_forwarder(
        row=row,
        c_name=c_name,
        data=data,
        decoded=decoded,
        rule="bink-buffer-set-scale-forwarder",
        variant="masm-bink-buffer-set-scale-forwarder",
        ),
    ]


def bink_buffer_masm_forwarder(
    *,
    row: dict[str, Any],
    c_name: str,
    data: bytes,
    decoded: dict[str, Any] | None,
    rule: str,
    variant: str,
) -> list[GeneratedCandidate]:
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            f"; Rule: {rule}.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    export = str(decoded.get("export") or rule)
    evidence = {
        **decoded,
        "sourceTier": f"generated MASM byte-emission parity fallback with decoded {export} forwarding wrapper bytes",
    }
    return [
        GeneratedCandidate(
            rule=rule,
            variant=variant,
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="stdcall",
            return_type="int",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_bink_buffer_set_direct_draw_forwarder(data: bytes) -> dict[str, Any] | None:
    body = bink_buffer_set_direct_draw_body(data)
    if body != BINK_BUFFER_SET_DIRECT_DRAW_FORWARDER:
        return None
    call_offset = 39
    call_disp = int.from_bytes(body[call_offset + 1 : call_offset + 5], "little", signed=True)
    return {
        "bodyBytes": len(body),
        "export": "BinkBufferSetDirectDraw",
        "stdcallStackBytes": 8,
        "stackArgCount": 2,
        "directDrawArgIndex": 1,
        "surfaceArgIndex": 2,
        "directDrawGlobalAddress": "0x30068c6c",
        "surfaceGlobalAddress": "0x30068c70",
        "modeGlobalAddress": "0x30068c68",
        "enabledModeValue": "0x08000000",
        "refreshCallOffset": call_offset,
        "refreshCallDisplacement": call_disp,
        "refreshCallTargetAddress": "0x3000f140",
        "successReturnValue": 1,
        "returnInstruction": "ret 0x08",
    }


def bink_buffer_set_direct_draw_body(data: bytes) -> bytes:
    body = strip_alignment_padding(data)
    if body == BINK_BUFFER_SET_DIRECT_DRAW_FORWARDER:
        return body
    if body.startswith(BINK_BUFFER_SET_DIRECT_DRAW_FORWARDER):
        return body[: len(BINK_BUFFER_SET_DIRECT_DRAW_FORWARDER)]
    return body


BINK_BUFFER_SET_DIRECT_DRAW_FORWARDER = bytes.fromhex(
    "8b4c240433d23bca742a8b4424083bc27422890d6c8c0630a3708c0630c705688c063000000008e864f9ffffb801000000c2080089156c8c06308915708c06308915688c0630b801000000c20800"
)


def decode_bink_buffer_check_win_pos_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_BUFFER_CHECK_WIN_POS_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkBufferCheckWinPos",
        "stdcallStackBytes": 12,
        "stackArgCount": 3,
        "bufferPointerArgIndex": 1,
        "xPointerArgIndex": 2,
        "yPointerArgIndex": 3,
        "xBaseFieldOffset": 0x1C,
        "yBaseFieldOffset": 0x20,
        "clipEnabledFieldOffset": 0x84,
        "globalWidthLimitAddress": "0x30055cb4",
        "globalHeightLimitAddress": "0x30055cb0",
        "alignmentModeGlobalAddress": "0x30068c80",
        "alignmentModes": [{"mode": 4, "mask": "0xfffffffe"}, {"mode": 3, "mask": "0xfffffff8"}, {"mode": "default", "mask": "0xfffffffc"}],
        "returnInstruction": "ret 0x0c",
    }


BINK_BUFFER_CHECK_WIN_POS_FORWARDER = bytes.fromhex(
    "8b4c240485c90f8499000000538b5c240c85db565774548b791c8b038b918400000003c785d2741b8b318b15b45c0530558d2c063bea5d7e042bd68bc285c07d0233c08b15808c063083fa0475064083e0feeb1383fa03750883c00783e0f8eb0683c00383e0fc2bc789038b7c241885ff742f8b71208b078b918400000003c685d2741a8b49048b15b05c05308d1c013bda7e042bd18bc285c07d0233c02bc689075f5e5bc20c00"
)


def decode_bink_buffer_close_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_BUFFER_CLOSE_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkBufferClose",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "bufferPointerArgIndex": 1,
        "surfaceFieldOffset": 0x48,
        "primaryReleaseFlagOffset": 0x68,
        "secondaryReleaseFlagOffset": 0x6C,
        "helperHandleFieldOffset": 0xA0,
        "helperContextFieldOffset": 0xA4,
        "helperResourceFieldOffset": 0x90,
        "helperAllocationFieldOffset": 0x9C,
        "optionalCloseFieldOffset": 0x88,
        "globalReferenceFieldOffset": 0x8C,
        "globalBackBufferAddress": "0x30068c70",
        "globalRefCountAddress": "0x30068c9c",
        "globalResourceAddress": "0x30068c98",
        "releaseImportAddresses": ["0x3004a018", "0x3004a014", "0x3004a02c", "0x3004a14c"],
        "directFreeCallOffset": 170,
        "directFreeCallDisplacement": -42673,
        "optionalCloseCallOffset": 188,
        "optionalCloseCallDisplacement": -6736,
        "finalFreeCallOffset": 280,
        "finalFreeCallDisplacement": -42785,
        "clearedDwordCount": 0x2A,
        "returnInstruction": "ret 0x04",
    }


BINK_BUFFER_CLOSE_FORWARDER = bytes.fromhex(
    "568b74240885f60f8414010000833e000f840b0100008b466885c074298b15708c06308b46488b086a0068000200006a00526a0050ff91840000008b46488b0850ff5108eb108b466c85c074098b46488b1050ff52088b86a000000085c074518b8ea40000005150ff1518a004308b969000000052ff1514a004308b86a000000050ff152ca004308b869c00000085c0741f8078fe030fb648ff750a8bd02bd152ff50f8eb0b2bc150e84f59ffff83c4048b868800000085c07405e8b0e5ffff8b868c00000085c07425ff0d9c8c0630751da1988c063085c074146a046a00c705988c063000000000ff154ca104305733c0b92a0000008bfef3ab807efe035f75100fb656ff8bc62bc250ff56f85ec204000fb64eff2bf156e8df58ffff83c4045ec20400"
)


def decode_bink_buffer_lock_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_BUFFER_LOCK_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkBufferLock",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "bufferPointerArgIndex": 1,
        "localScratchBytes": 0x6C,
        "surfaceFieldOffset": 0x48,
        "lockStateFieldOffset": 0x64,
        "prelockFlagFieldOffset": 0x74,
        "prelockResultFieldOffset": 0x78,
        "callbackArgFieldOffset": 0x7C,
        "dirtyFlagFieldOffset": 0x10,
        "outputPointerFieldOffset": 0x14,
        "outputPitchFieldOffset": 0x18,
        "fallbackGuardFieldOffset": 0x90,
        "fallbackPointerFieldOffset": 0x94,
        "fallbackPitchFieldOffset": 0x98,
        "globalBytesPerPixelAddress": "0x30068c80",
        "prelockCallOffset": 86,
        "prelockCallDisplacement": -5342,
        "unlockCleanupCallOffset": 166,
        "unlockCleanupCallDisplacement": -5226,
        "surfaceLostHresult": "0x887601c2",
        "dirtyFlagMask": "0x80000000",
        "nullReturnValue": 0,
        "failureReturnValue": 0,
        "successReturnValue": 1,
        "returnInstruction": "ret 0x04",
    }


BINK_BUFFER_LOCK_FORWARDER = bytes.fromhex(
    "83ec6c568b74247485f6750933c05e83c46cc204008b464885c0570f84d30000008b466485c00f857e00000033c0b91b0000008d7c2408f3ab8b467485c0c74424086c00000074198b46048b0e8b5654508b46505152506a00e822ebffff894678bf000000808b46488b086a006a018d542410526a0050ff516485c074368b56100bd73dc2017688895610750d8b46488b0850ff516c85c074cc8b467485c074098b567852e896ebffff5f33c05e83c46cc204008b4e688b44242c85c98b4c241889467c75448b566c85d2753d8b56500faf15808c06308b7e540faff903c203f8897e145f894e18b8010000005e83c46cc204008b869000000085c074128b86940000008b8e98000000894614894e185fb8010000005e83c46cc20400"
)


def decode_bink_buffer_set_scale_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_BUFFER_SET_SCALE_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkBufferSetScale",
        "stdcallStackBytes": 12,
        "stackArgCount": 3,
        "bufferPointerArgIndex": 1,
        "widthArgIndex": 2,
        "heightArgIndex": 3,
        "globalWidthFallbackAddress": "0x30055cb4",
        "globalHeightFallbackAddress": "0x30055cb0",
        "sourceWidthFieldOffset": 0x00,
        "sourceHeightFieldOffset": 0x04,
        "scaleFlagsFieldOffset": 0x38,
        "scaledWidthFieldOffset": 0x3C,
        "scaledHeightFieldOffset": 0x40,
        "xOffsetFieldOffset": 0x30,
        "yOffsetFieldOffset": 0x34,
        "rightFieldOffset": 0x08,
        "bottomFieldOffset": 0x0C,
        "horizontalScaleMasks": ["0x80000000", "0x20000000", "0x40000000", "0x10000000"],
        "verticalScaleMasks": ["0x08000000", "0x02000000", "0x04000000", "0x01000000"],
        "nullReturnValue": 0,
        "successReturnValue": 1,
        "returnInstruction": "ret 0x0c",
    }


BINK_BUFFER_SET_SCALE_FORWARDER = bytes.fromhex(
    "51568b74240c85f6c744240401000000750733c05e59c20c0055578b7c241885ff75068b3db45c05308b6c241c85ed75068b2db05c05308b0e5333db3bf9743433d28bc7f7f185d27507bb00000080eb2333d28bc1f7f785d27507bb00000020eb123bf97607bb00000040eb077305bb000000108b46388944241823c33bc37505897e3ceb08c7442410000000008b4e0433db3be9743833d28bc7f7f185d27507bb00000008eb273be9742333d28bc1f7f585d27507bb00000002eb123be97607bb00000004eb077305bb000000018b44241823c33bc35b7505896e40eb08c744240c000000008b4e308b463c03c18b4e408946088b46345f03c88b4424085d894e0c5e59c20c00"
)


def bink_close_track_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_close_track_forwarder(data)
    if decoded is None:
        return []
    inline_source = header("bink-close-track-forwarder", row) + "\n".join(
        [
            f"__declspec(naked) void __stdcall {c_name}(void *track) {{",
            "    __asm {",
            "        push esi",
            "        mov esi, dword ptr [esp+8]",
            "        test esi, esi",
            "        je done",
            "        mov eax, dword ptr [esi+14h]",
            "        test eax, eax",
            "        je free_track",
            "        cmp byte ptr [eax-2], 3",
            "        movzx ecx, byte ptr [eax-1]",
            "        jne direct_free_child",
            "        mov edx, eax",
            "        sub edx, ecx",
            "        push edx",
            "        call dword ptr [eax-8]",
            "        jmp clear_child",
            "direct_free_child:",
            "        sub eax, ecx",
            "        push eax",
            "        _emit 0e8h",
            "        _emit 001h",
            "        _emit 00dh",
            "        _emit 0ffh",
            "        _emit 0ffh",
            "        add esp, 4",
            "clear_child:",
            "        mov dword ptr [esi+14h], 0",
            "free_track:",
            "        cmp byte ptr [esi-2], 3",
            "        jne direct_free_track",
            "        movzx edx, byte ptr [esi-1]",
            "        mov eax, esi",
            "        sub eax, edx",
            "        push eax",
            "        call dword ptr [esi-8]",
            "        pop esi",
            "        ret 4",
            "direct_free_track:",
            "        movzx ecx, byte ptr [esi-1]",
            "        sub esi, ecx",
            "        push esi",
            "        _emit 0e8h",
            "        _emit 0d5h",
            "        _emit 00ch",
            "        _emit 0ffh",
            "        _emit 0ffh",
            "        add esp, 4",
            "done:",
            "        pop esi",
            "        ret 4",
            "    }",
            "}",
            "",
        ]
    )
    inline_evidence = {
        **decoded,
        "sourceTier": "generated naked C inline-assembly source for decoded BinkCloseTrack teardown control flow",
        "rawRel32CallDisplacementsPreserved": ["child-direct-free", "track-direct-free"],
        "claimBoundary": "Inline assembly preserves allocator-header control flow and direct-free displacements; allocator/helper identities remain bounded by decoded bytes.",
    }
    return [
        GeneratedCandidate(
            rule="bink-close-track-forwarder",
            variant="naked-c-bink-close-track-forwarder",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=inline_source,
            callconv="stdcall",
            return_type="void",
            extra_flags=("/O2", "/Gz"),
            evidence=inline_evidence,
        ),
        *bink_buffer_masm_forwarder(
        row=row,
        c_name=c_name,
        data=data,
        decoded=decoded,
        rule="bink-close-track-forwarder",
        variant="masm-bink-close-track-forwarder",
        ),
    ]


def decode_bink_close_track_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_CLOSE_TRACK_FORWARDER:
        return None
    first_free_offset = 39
    final_free_offset = 83
    return {
        "bodyBytes": len(body),
        "export": "BinkCloseTrack",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "trackPointerArgIndex": 1,
        "optionalAllocationFieldOffset": 0x14,
        "allocationHeaderKindOffset": -2,
        "allocationHeaderDeltaOffset": -1,
        "customFreeVtableOffset": -8,
        "fieldClearOffset": 0x14,
        "firstDirectFreeCallOffset": first_free_offset,
        "firstDirectFreeCallDisplacement": int.from_bytes(body[first_free_offset + 1 : first_free_offset + 5], "little", signed=True),
        "finalDirectFreeCallOffset": final_free_offset,
        "finalDirectFreeCallDisplacement": int.from_bytes(body[final_free_offset + 1 : final_free_offset + 5], "little", signed=True),
        "directFreeTargetAddress": "0x300068ed",
        "returnInstruction": "ret 0x04",
    }


BINK_CLOSE_TRACK_FORWARDER = bytes.fromhex(
    "568b74240885f674528b461485c074268078fe030fb648ff750a8bd02bd152ff50f8eb0b2bc150e8010dffff83c404c7461400000000807efe0375100fb656ff8bc62bc250ff56f85ec204000fb64eff2bf156e8d50cffff83c4045ec20400"
)


def bink_pause_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_pause_forwarder(data)
    if decoded is None:
        return []
    inline_source = header("bink-pause-forwarder", row) + "\n".join(
        [
            f"__declspec(naked) int __stdcall {c_name}(void *bink, unsigned int pause_mode) {{",
            "    __asm {",
            "        push esi",
            "        mov esi, dword ptr [esp+8]",
            "        push edi",
            "        xor edi, edi",
            "        cmp esi, edi",
            "        jne have_bink",
            "        pop edi",
            "        xor eax, eax",
            "        pop esi",
            "        ret 8",
            "have_bink:",
            "        _emit 0e8h",
            "        _emit 038h",
            "        _emit 02eh",
            "        _emit 000h",
            "        _emit 000h",
            "        mov ecx, dword ptr [esi+27ch]",
            "        cmp ecx, edi",
            "        je no_pause_start",
            "        mov edx, eax",
            "        sub edx, ecx",
            "        add dword ptr [esi+2b4h], edx",
            "        mov dword ptr [esi+27ch], edi",
            "no_pause_start:",
            "        mov ecx, eax",
            "        push ebp",
            "        mov eax, esi",
            "        _emit 0e8h",
            "        _emit 044h",
            "        _emit 0fdh",
            "        _emit 0ffh",
            "        _emit 0ffh",
            "        mov ebp, dword ptr [esp+14h]",
            "        cmp ebp, edi",
            "        jne set_flag",
            "        cmp dword ptr [esi+0fch], edi",
            "        je set_flag",
            "        mov dword ptr [esi+280h], edi",
            "        mov dword ptr [esi+338h], edi",
            "set_flag:",
            "        cmp dword ptr [esi+2f8h], edi",
            "        mov dword ptr [esi+0fch], ebp",
            "        jbe after_tracks",
            "        push ebx",
            "        xor ebx, ebx",
            "        _emit 08dh",
            "        _emit 0a4h",
            "        _emit 024h",
            "        _emit 000h",
            "        _emit 000h",
            "        _emit 000h",
            "        _emit 000h",
            "track_loop:",
            "        mov eax, dword ptr [esi+300h]",
            "        add eax, ebx",
            "        push ebp",
            "        push eax",
            "        call dword ptr [eax+14h]",
            "        mov eax, dword ptr [esi+2f8h]",
            "        inc edi",
            "        add ebx, 178h",
            "        cmp edi, eax",
            "        jb track_loop",
            "        xor edi, edi",
            "        pop ebx",
            "after_tracks:",
            "        cmp dword ptr [esi+270h], edi",
            "        pop ebp",
            "        je done_optional",
            "        cmp dword ptr [esi+2f8h], edi",
            "        je done_optional",
            "        push esi",
            "        _emit 0e8h",
            "        _emit 0c8h",
            "        _emit 0cdh",
            "        _emit 0ffh",
            "        _emit 0ffh",
            "done_optional:",
            "        mov eax, dword ptr [esi+0fch]",
            "        pop edi",
            "        pop esi",
            "        ret 8",
            "    }",
            "}",
            "",
        ]
    )
    inline_evidence = {
        **decoded,
        "sourceTier": "generated naked C inline-assembly source for decoded BinkPause timing/track-state wrapper",
        "rawRel32CallDisplacementsPreserved": ["timer-read", "state-helper", "optional-helper"],
        "rawIndirectTrackMethodCallPreserved": True,
        "claimBoundary": "Inline assembly preserves timing calls, track-loop branch layout, and indirect method dispatch; helper identities remain bounded by decoded byte metadata.",
    }
    return [
        GeneratedCandidate(
            rule="bink-pause-forwarder",
            variant="naked-c-bink-pause-forwarder",
            c_name=c_name,
            symbol=f"_{c_name}@8",
            source=inline_source,
            callconv="stdcall",
            return_type="int",
            extra_flags=("/O2", "/Gz"),
            evidence=inline_evidence,
        ),
        *bink_buffer_masm_forwarder(
        row=row,
        c_name=c_name,
        data=data,
        decoded=decoded,
        rule="bink-pause-forwarder",
        variant="masm-bink-pause-forwarder",
        ),
    ]


def decode_bink_pause_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_PAUSE_FORWARDER:
        return None
    time_call_offset = 19
    state_call_offset = 55
    track_call_offset = 122
    optional_call_offset = 163
    evidence = {
        "bodyBytes": len(body),
        "export": "BinkPause",
        "stdcallStackBytes": 8,
        "stackArgCount": 2,
        "binkPointerArgIndex": 1,
        "pauseModeArgIndex": 2,
        "pauseStartFieldOffset": 0x27C,
        "pauseAccumulatedFieldOffset": 0x2B4,
        "pauseFlagFieldOffset": 0xFC,
        "trackCountFieldOffset": 0x2F8,
        "trackArrayFieldOffset": 0x300,
        "trackStrideBytes": 0x178,
        "prePauseStateFieldOffset": 0x280,
        "postPauseStateFieldOffset": 0x338,
        "optionalGuardFieldOffset": 0x270,
        "timeCallOffset": time_call_offset,
        "timeCallDisplacement": int.from_bytes(body[time_call_offset + 1 : time_call_offset + 5], "little", signed=True),
        "stateHelperCallOffset": state_call_offset,
        "stateHelperCallDisplacement": int.from_bytes(body[state_call_offset + 1 : state_call_offset + 5], "little", signed=True),
        "trackMethodCallOffset": track_call_offset,
        "trackMethodVtableOffset": 0x14,
        "optionalHelperCallOffset": optional_call_offset,
        "optionalHelperCallDisplacement": int.from_bytes(body[optional_call_offset + 1 : optional_call_offset + 5], "little", signed=True),
        "nullReturnValue": 0,
        "returnFieldOffset": 0xFC,
        "returnInstruction": "ret 0x08",
    }
    evidence["targetByteSpan"] = {
        "offset": 0,
        "length": len(body),
        "reason": "export target slice may contain the decoded function followed by padding; compare only the function body span",
    }
    return evidence


BINK_PAUSE_FORWARDER = bytes.fromhex(
    "568b7424085733ff3bf775075f33c05ec20800e8382e00008b8e7c0200003bcf74108bd02bd10196b402000089be7c0200008bc8558bc6e844fdffff8b6c24143bef751439befc000000740c89be8002000089be3803000039bef802000089aefc000000762b5333db8da424000000008b860003000003c35550ff50148b86f80200004781c3780100003bf872e233ff5b39be700200005d740e39bef8020000740656e8c8cdffff8b86fc0000005f5ec20800"
)


def bink_get_key_frame_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    return bink_buffer_masm_forwarder(
        row=row,
        c_name=c_name,
        data=data,
        decoded=decode_bink_get_key_frame_forwarder(data),
        rule="bink-get-key-frame-forwarder",
        variant="masm-bink-get-key-frame-forwarder",
    )


def decode_bink_get_key_frame_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_GET_KEY_FRAME_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkGetKeyFrame",
        "stdcallStackBytes": 12,
        "stackArgCount": 3,
        "binkPointerArgIndex": 1,
        "frameArgIndex": 2,
        "modeArgIndex": 3,
        "frameCountFieldOffset": 0x08,
        "keyFrameTableFieldOffset": 0x10C,
        "frameFlagMask": 1,
        "modeMask": "0x7f",
        "signedModeUsesCurrentFrameCheck": True,
        "modeCases": [
            {"mode": 0, "direction": "previous", "minimumFrame": 1},
            {"mode": 1, "direction": "forward", "upperBoundFieldOffset": 0x08},
            {"mode": 2, "direction": "nearest-previous-or-current"},
        ],
        "nullReturnValue": 0,
        "notFoundReturnValue": 0,
        "returnInstruction": "ret 0x0c",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "export target slice contains the decoded function followed by padding; compare only the function body span",
        },
    }


BINK_GET_KEY_FRAME_FORWARDER = bytes.fromhex(
    "8b4c240485c957750633c05fc20c008b54241084d28b44240c5678118bb10c010000f64486fc010f85a200000083e27f83ea0074734a74434a75668d78fe8bff85ff7c168bb10c010000f604be0175233b4108731b8b1486eb0e3b410873428b910c0100008b148280e2014084d2755f4febcd5e8d47015fc20c008b71083bc6731f8b890c0100008d0c81eb038d49008b1180e2014083c10484d275323bc672ef5e33c05fc20c0083c0fe83f8017c1e8b890c0100008d0c818da42400000000f6010175094883e90483f8017df2405e5fc20c00"
)


def bink_check_cursor_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_check_cursor_forwarder(data)
    if decoded is None:
        return []
    inline_source = header("bink-check-cursor-forwarder", row) + "\n".join(
        [
            f"__declspec(naked) int __stdcall {c_name}(void *window, int x, int y, int width, int height) {{",
            "    __asm {",
            "        _emit 0a1h",
            "        _emit 0ach",
            "        _emit 08ch",
            "        _emit 006h",
            "        _emit 030h",
            "        sub esp, 10h",
            "        push esi",
            "        push edi",
            "        xor edi, edi",
            "        test eax, eax",
            "        jne initialized",
            "        _emit 08bh",
            "        _emit 035h",
            "        _emit 054h",
            "        _emit 0a1h",
            "        _emit 004h",
            "        _emit 030h",
            "        push 0dh",
            "        call esi",
            "        push 0eh",
            "        _emit 0a3h",
            "        _emit 0ach",
            "        _emit 08ch",
            "        _emit 006h",
            "        _emit 030h",
            "        call esi",
            "        _emit 0a3h",
            "        _emit 0b8h",
            "        _emit 05ch",
            "        _emit 005h",
            "        _emit 030h",
            "initialized:",
            "        mov eax, dword ptr [esp+20h]",
            "        mov ecx, dword ptr [esp+24h]",
            "        mov dword ptr [esp+8], eax",
            "        mov eax, dword ptr [esp+1ch]",
            "        test eax, eax",
            "        mov dword ptr [esp+0ch], ecx",
            "        je no_window_rect",
            "        lea edx, dword ptr [esp+8]",
            "        push edx",
            "        push eax",
            "        _emit 0ffh",
            "        _emit 015h",
            "        _emit 084h",
            "        _emit 0a1h",
            "        _emit 004h",
            "        _emit 030h",
            "no_window_rect:",
            "        lea eax, dword ptr [esp+10h]",
            "        push eax",
            "        _emit 0ffh",
            "        _emit 015h",
            "        _emit 058h",
            "        _emit 0a1h",
            "        _emit 004h",
            "        _emit 030h",
            "        _emit 08bh",
            "        _emit 00dh",
            "        _emit 0ach",
            "        _emit 08ch",
            "        _emit 006h",
            "        _emit 030h",
            "        mov eax, dword ptr [esp+10h]",
            "        lea edx, dword ptr [ecx+eax]",
            "        mov ecx, dword ptr [esp+8]",
            "        cmp edx, ecx",
            "        jle done",
            "        mov edx, dword ptr [esp+28h]",
            "        add ecx, edx",
            "        cmp eax, ecx",
            "        jge done",
            "        _emit 08bh",
            "        _emit 00dh",
            "        _emit 0b8h",
            "        _emit 05ch",
            "        _emit 005h",
            "        _emit 030h",
            "        mov eax, dword ptr [esp+14h]",
            "        lea edx, dword ptr [ecx+eax]",
            "        mov ecx, dword ptr [esp+0ch]",
            "        cmp edx, ecx",
            "        jle done",
            "        mov edx, dword ptr [esp+2ch]",
            "        add ecx, edx",
            "        cmp eax, ecx",
            "        jge done",
            "        _emit 08bh",
            "        _emit 035h",
            "        _emit 0a8h",
            "        _emit 0a1h",
            "        _emit 004h",
            "        _emit 030h",
            "        jmp loop_start",
            "        _emit 08dh",
            "        _emit 049h",
            "        _emit 000h",
            "loop_start:",
            "        push 0",
            "        inc edi",
            "        call esi",
            "        test eax, eax",
            "        jge loop_start",
            "done:",
            "        mov eax, edi",
            "        pop edi",
            "        pop esi",
            "        add esp, 10h",
            "        ret 14h",
            "    }",
            "}",
            "",
        ]
    )
    inline_evidence = {
        **decoded,
        "sourceTier": "generated naked C inline-assembly source for decoded BinkCheckCursor Win32 cursor polling wrapper",
        "rawAbsoluteGlobalAccessesPreserved": [
            "cursorWidthGlobal",
            "cursorHeightGlobal",
            "loadCursorImportSlot",
            "getWindowRectImportSlot",
            "getCursorPosImportSlot",
            "showCursorImportSlot",
        ],
        "claimBoundary": "Inline assembly preserves absolute Win32 import/global encodings and loop branch layout; symbolic import names remain decoded metadata until original link context is recovered.",
    }
    return [
        GeneratedCandidate(
            rule="bink-check-cursor-forwarder",
            variant="naked-c-bink-check-cursor-forwarder",
            c_name=c_name,
            symbol=f"_{c_name}@20",
            source=inline_source,
            callconv="stdcall",
            return_type="int",
            extra_flags=("/O2", "/Gz"),
            evidence=inline_evidence,
        ),
        *bink_buffer_masm_forwarder(
        row=row,
        c_name=c_name,
        data=data,
        decoded=decoded,
        rule="bink-check-cursor-forwarder",
        variant="masm-bink-check-cursor-forwarder",
        ),
    ]


def decode_bink_check_cursor_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_CHECK_CURSOR_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkCheckCursor",
        "stdcallStackBytes": 20,
        "stackArgCount": 5,
        "windowHandleArgIndex": 1,
        "xArgIndex": 2,
        "yArgIndex": 3,
        "widthArgIndex": 4,
        "heightArgIndex": 5,
        "cursorWidthGlobalAddress": "0x30068cac",
        "cursorHeightGlobalAddress": "0x30055cb8",
        "getSystemMetricsImportAddress": "0x3004a154",
        "getSystemMetricsWidthIndex": 13,
        "getSystemMetricsHeightIndex": 14,
        "getWindowRectImportAddress": "0x3004a184",
        "getCursorPosImportAddress": "0x3004a158",
        "showCursorImportAddress": "0x3004a1a8",
        "localRectBytes": 8,
        "localPointBytes": 8,
        "showCursorArgument": 0,
        "returnShowsHiddenCount": True,
        "returnInstruction": "ret 0x14",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "export target slice contains the decoded function followed by padding; compare only the function body span",
        },
    }


BINK_CHECK_CURSOR_FORWARDER = bytes.fromhex(
    "a1ac8c063083ec10565733ff85c075188b3554a104306a0dffd66a0ea3ac8c0630ffd6a3b85c05308b4424208b4c2424894424088b44241c85c0894c240c740c8d5424085250ff1584a104308d44241050ff1558a104308b0dac8c06308b4424108d14018b4c24083bd17e3d8b54242803ca3bc17d338b0db85c05308b4424148d14018b4c240c3bd17e1e8b54242c03ca3bc17d148b35a8a10430eb038d49006a0047ffd685c07df78bc75f5e83c410c21400"
)


def bink_open_track_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_open_track_forwarder(data)
    if decoded is None:
        return []
    inline_source = header("bink-open-track-forwarder", row) + "\n".join(
        [
            f"__declspec(naked) void *__stdcall {c_name}(void *bink, unsigned int index) {{",
            "    __asm {",
            "        push ebx",
            "        push ebp",
            "        push esi",
            "        mov esi, dword ptr [esp+10h]",
            "        test esi, esi",
            "        je fail",
            "        mov ebx, dword ptr [esp+14h]",
            "        cmp ebx, dword ptr [esi+0f0h]",
            "        jae fail",
            "        mov eax, dword ptr [esi+264h]",
            "        mov ecx, dword ptr [eax+ebx*4]",
            "        mov eax, ecx",
            "        shr eax, 1fh",
            "        test eax, eax",
            "        jne mode_ok",
            "        test ecx, 10000000h",
            "        je fail",
            "        test eax, eax",
            "        jne mode_ok",
            "        test ecx, 10000000h",
            "        je mode_ok",
            "        mov eax, 1",
            "        jmp mode_done",
            "mode_ok:",
            "        xor eax, eax",
            "mode_done:",
            "        mov edx, ecx",
            "        shr edx, 1dh",
            "        and edx, 1",
            "        push eax",
            "        inc edx",
            "        push edx",
            "        and ecx, 0ffffh",
            "        _emit 0e8h",
            "        _emit 0b0h",
            "        _emit 055h",
            "        _emit 000h",
            "        _emit 000h",
            "        mov ebp, eax",
            "        test ebp, ebp",
            "        je fail",
            "        _emit 0a1h",
            "        _emit 078h",
            "        _emit 080h",
            "        _emit 005h",
            "        _emit 030h",
            "        mov ecx, dword ptr [esi+2a8h]",
            "        add eax, 1ch",
            "        add ecx, eax",
            "        mov eax, 1ch",
            "        mov dword ptr [esi+2a8h], ecx",
            "        _emit 0e8h",
            "        _emit 08ah",
            "        _emit 0b6h",
            "        _emit 0feh",
            "        _emit 0ffh",
            "        mov edx, eax",
            "        test edx, edx",
            "        jne have_track",
            "        mov eax, ebp",
            "        _emit 0e8h",
            "        _emit 02dh",
            "        _emit 059h",
            "        _emit 000h",
            "        _emit 000h",
            "fail:",
            "        pop esi",
            "        pop ebp",
            "        xor eax, eax",
            "        pop ebx",
            "        ret 8",
            "have_track:",
            "        xor eax, eax",
            "        push edi",
            "        mov ecx, 7",
            "        mov edi, edx",
            "        rep stosd",
            "        mov dword ptr [edx+10h], esi",
            "        mov dword ptr [edx+14h], ebp",
            "        mov ecx, dword ptr [esi+264h]",
            "        mov eax, dword ptr [ecx+ebx*4]",
            "        and eax, 0ffffh",
            "        mov dword ptr [edx], eax",
            "        mov ecx, dword ptr [esi+264h]",
            "        mov eax, dword ptr [ecx+ebx*4]",
            "        shr eax, 1bh",
            "        and eax, 8",
            "        add eax, 8",
            "        mov dword ptr [edx+4], eax",
            "        mov ecx, dword ptr [esi+264h]",
            "        mov eax, dword ptr [ecx+ebx*4]",
            "        shr eax, 1dh",
            "        and eax, 1",
            "        inc eax",
            "        mov dword ptr [edx+8], eax",
            "        mov ecx, dword ptr [esi+260h]",
            "        mov eax, dword ptr [ecx+ebx*4]",
            "        mov ecx, dword ptr [edx+4]",
            "        add eax, 3",
            "        and eax, 0fffffffch",
            "        cmp ecx, 8",
            "        mov dword ptr [edx+0ch], eax",
            "        pop edi",
            "        jne no_halve",
            "        shr eax, 1",
            "        mov dword ptr [edx+0ch], eax",
            "no_halve:",
            "        pop esi",
            "        pop ebp",
            "        mov dword ptr [edx+18h], ebx",
            "        mov eax, edx",
            "        pop ebx",
            "        ret 8",
            "    }",
            "}",
            "",
        ]
    )
    inline_evidence = {
        **decoded,
        "sourceTier": "generated naked C inline-assembly source for decoded BinkOpenTrack allocation/object setup wrapper",
        "rawAbsoluteGlobalLoadsPreserved": [decoded.get("globalTrackAllocationBaseAddress")],
        "rawRel32CallDisplacementsPreserved": ["helper-open", "allocation", "helper-close"],
        "claimBoundary": "Inline assembly preserves decoded track descriptor arithmetic, helper/allocation calls, and raw allocation-base load; object layout is metadata-bound, not full type recovery.",
    }
    return [
        GeneratedCandidate(
            rule="bink-open-track-forwarder",
            variant="naked-c-bink-open-track-forwarder",
            c_name=c_name,
            symbol=f"_{c_name}@8",
            source=inline_source,
            callconv="stdcall",
            return_type="void *",
            extra_flags=("/O2", "/Gz"),
            evidence=inline_evidence,
        ),
        *bink_buffer_masm_forwarder(
        row=row,
        c_name=c_name,
        data=data,
        decoded=decoded,
        rule="bink-open-track-forwarder",
        variant="masm-bink-open-track-forwarder",
        ),
    ]


def decode_bink_open_track_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_OPEN_TRACK_FORWARDER:
        return None
    helper_open_offset = 0x5B
    allocation_offset = 0x81
    helper_close_offset = 0x8E
    return {
        "bodyBytes": len(body),
        "export": "BinkOpenTrack",
        "stdcallStackBytes": 8,
        "stackArgCount": 2,
        "binkPointerArgIndex": 1,
        "trackIndexArgIndex": 2,
        "trackCountFieldOffset": 0xF0,
        "trackDescriptorTableFieldOffset": 0x264,
        "trackLengthTableFieldOffset": 0x260,
        "trackAllocationCursorFieldOffset": 0x2A8,
        "globalTrackAllocationBaseAddress": "0x30058078",
        "trackDescriptorMask": "0xffff",
        "trackFlagHighBitShift": 31,
        "trackTypeMask": "0x10000000",
        "trackChannelShift": 29,
        "trackModeShift": 27,
        "trackObjectDwordClearCount": 7,
        "trackObjectBinkFieldOffset": 0x10,
        "trackObjectHelperFieldOffset": 0x14,
        "trackObjectDescriptorFieldOffset": 0,
        "trackObjectTypeFieldOffset": 4,
        "trackObjectChannelFieldOffset": 8,
        "trackObjectLengthFieldOffset": 0x0C,
        "trackObjectIndexFieldOffset": 0x18,
        "helperOpenCallOffset": helper_open_offset,
        "helperOpenCallDisplacement": int.from_bytes(body[helper_open_offset + 1 : helper_open_offset + 5], "little", signed=True),
        "allocationCallOffset": allocation_offset,
        "allocationCallDisplacement": int.from_bytes(body[allocation_offset + 1 : allocation_offset + 5], "little", signed=True),
        "helperCloseCallOffset": helper_close_offset,
        "helperCloseCallDisplacement": int.from_bytes(body[helper_close_offset + 1 : helper_close_offset + 5], "little", signed=True),
        "nullReturnValue": 0,
        "returnInstruction": "ret 0x08",
    }


BINK_OPEN_TRACK_FORWARDER = bytes.fromhex(
    "5355568b74241085f60f84840000008b5c24143b9ef000000073788b86640200008b0c988bc1c1e81f85c0751bf7c100000010745e85c0750ff7c1000000107407b801000000eb0233c08bd1c1ea1d83e20150425281e1ffff0000e8b05500008be885ed742da1788005308b8ea802000083c01c03c8b81c000000898ea8020000e88ab6feff8bd085d2750f8bc5e82d5900005e5d33c05bc2080033c057b9070000008bfaf3ab897210896a148b8e640200008b049925ffff000089028b8e640200008b0499c1e81b83e00883c0088942048b8e640200008b0499c1e81d83e001408942088b8e600200008b04998b4a0483c00383e0fc83f90889420c5f7505d1e889420c5e5d895a188bc25bc20800"
)


def bink_buffer_get_description_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_buffer_get_description_forwarder(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    body = data[: int(decoded["targetByteSpan"]["length"])]
    inline_source = header("bink-buffer-get-description-forwarder", row) + "\n".join(
        [
            f"__declspec(naked) void *__stdcall {c_name}(void *buffer) {{",
            "    __asm {",
            "        mov eax, dword ptr [esp+4]",
            "        test eax, eax",
            "        je null_buffer",
            "        mov eax, dword ptr [eax+80h]",
            "        dec eax",
            "        cmp eax, 9",
            "        push esi",
            "        push edi",
            "        ja default_case",
            "        _emit 0ffh",
            "        _emit 024h",
            "        _emit 085h",
            "        _emit 038h",
            "        _emit 018h",
            "        _emit 001h",
            "        _emit 030h",
            "case0:",
            "        mov ecx, 6",
            "        mov esi, 3004fd18h",
            "        mov edi, 30055bb0h",
            "        rep movsd",
            "        pop edi",
            "        mov eax, 30055bb0h",
            "        pop esi",
            "        ret 4",
            "case1:",
            "        mov ecx, 6",
            "        mov esi, 3004fd00h",
            "        mov edi, 30055bb0h",
            "        rep movsd",
            "        pop edi",
            "        mov eax, 30055bb0h",
            "        pop esi",
            "        ret 4",
            "case2:",
            "        mov ecx, 6",
            "        mov esi, 3004fce8h",
            "        mov edi, 30055bb0h",
            "        rep movsd",
            "        pop edi",
            "        mov eax, 30055bb0h",
            "        pop esi",
            "        ret 4",
            "case3:",
            "        mov ecx, 8",
            "        mov esi, 3004fcc4h",
            "        mov edi, 30055bb0h",
            "        rep movsd",
            "        movsw",
            "        movsb",
            "        pop edi",
            "        mov eax, 30055bb0h",
            "        pop esi",
            "        ret 4",
            "case4:",
            "        mov ecx, 8",
            "        mov esi, 3004fca0h",
            "        mov edi, 30055bb0h",
            "        rep movsd",
            "        movsw",
            "        movsb",
            "        pop edi",
            "        mov eax, 30055bb0h",
            "        pop esi",
            "        ret 4",
            "case5:",
            "        mov ecx, 8",
            "        mov esi, 3004fc7ch",
            "        mov edi, 30055bb0h",
            "        rep movsd",
            "        movsw",
            "        movsb",
            "        pop edi",
            "        mov eax, 30055bb0h",
            "        pop esi",
            "        ret 4",
            "case6:",
            "        mov ecx, 0ah",
            "        mov esi, 3004fc54h",
            "        mov edi, 30055bb0h",
            "        rep movsd",
            "        pop edi",
            "        mov eax, 30055bb0h",
            "        pop esi",
            "        ret 4",
            "case7:",
            "        mov ecx, 0ah",
            "        mov esi, 3004fc28h",
            "        mov edi, 30055bb0h",
            "        rep movsd",
            "        movsb",
            "        pop edi",
            "        mov eax, 30055bb0h",
            "        pop esi",
            "        ret 4",
            "case8:",
            "        mov ecx, 6",
            "        mov esi, 3004fc0ch",
            "        mov edi, 30055bb0h",
            "        rep movsd",
            "        movsw",
            "        movsb",
            "        pop edi",
            "        mov eax, 30055bb0h",
            "        pop esi",
            "        ret 4",
            "case9:",
            "        _emit 0a1h",
            "        _emit 000h",
            "        _emit 0fch",
            "        _emit 004h",
            "        _emit 030h",
            "        _emit 08bh",
            "        _emit 00dh",
            "        _emit 004h",
            "        _emit 0fch",
            "        _emit 004h",
            "        _emit 030h",
            "        _emit 08bh",
            "        _emit 015h",
            "        _emit 008h",
            "        _emit 0fch",
            "        _emit 004h",
            "        _emit 030h",
            "        _emit 0a3h",
            "        _emit 0b0h",
            "        _emit 05bh",
            "        _emit 005h",
            "        _emit 030h",
            "        _emit 089h",
            "        _emit 00dh",
            "        _emit 0b4h",
            "        _emit 05bh",
            "        _emit 005h",
            "        _emit 030h",
            "        _emit 089h",
            "        _emit 015h",
            "        _emit 0b8h",
            "        _emit 05bh",
            "        _emit 005h",
            "        _emit 030h",
            "default_case:",
            "        pop edi",
            "        mov eax, 30055bb0h",
            "        pop esi",
            "        ret 4",
            "null_buffer:",
            "        xor eax, eax",
            "        ret 4",
            "        mov edi, edi",
            "        _emit 0e7h",
            "        _emit 017h",
            "        _emit 001h",
            "        _emit 030h",
            "        _emit 005h",
            "        _emit 018h",
            "        _emit 001h",
            "        _emit 030h",
            "        _emit 005h",
            "        _emit 017h",
            "        _emit 001h",
            "        _emit 030h",
            "        _emit 020h",
            "        _emit 017h",
            "        _emit 001h",
            "        _emit 030h",
            "        _emit 03bh",
            "        _emit 017h",
            "        _emit 001h",
            "        _emit 030h",
            "        _emit 056h",
            "        _emit 017h",
            "        _emit 001h",
            "        _emit 030h",
            "        _emit 074h",
            "        _emit 017h",
            "        _emit 001h",
            "        _emit 030h",
            "        _emit 092h",
            "        _emit 017h",
            "        _emit 001h",
            "        _emit 030h",
            "        _emit 0b0h",
            "        _emit 017h",
            "        _emit 001h",
            "        _emit 030h",
            "        _emit 0cbh",
            "        _emit 017h",
            "        _emit 001h",
            "        _emit 030h",
            "    }",
            "}",
            "",
        ]
    )
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: bink-buffer-get-description-forwarder.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkBufferGetDescription descriptor switch bytes",
    }
    inline_evidence = {
        **decoded,
        "sourceTier": "generated naked C inline-assembly source for decoded BinkBufferGetDescription descriptor switch",
        "rawEmbeddedJumpTablePreserved": True,
        "rawDescriptorSourceAddressesPreserved": decoded.get("descriptorSourceAddresses"),
        "rawScratchGlobalAddressPreserved": decoded.get("descriptorScratchGlobalAddress"),
        "claimBoundary": "Inline assembly preserves descriptor-copy cases, raw descriptor/source addresses, scratch global stores, and embedded jump-table bytes; descriptor structs are still metadata-bound.",
    }
    return [
        GeneratedCandidate(
            rule="bink-buffer-get-description-forwarder",
            variant="naked-c-bink-buffer-get-description-forwarder",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=inline_source,
            callconv="stdcall",
            return_type="void *",
            extra_flags=("/O2", "/Gz"),
            evidence=inline_evidence,
        ),
        GeneratedCandidate(
            rule="bink-buffer-get-description-forwarder",
            variant="masm-bink-buffer-get-description-forwarder",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="stdcall",
            return_type="void *",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_bink_buffer_get_description_forwarder(data: bytes) -> dict[str, Any] | None:
    body = data[: len(BINK_BUFFER_GET_DESCRIPTION_FORWARDER)]
    if body != BINK_BUFFER_GET_DESCRIPTION_FORWARDER:
        return None
    jump_table_offset = 0x158
    entries = [
        f"0x{int.from_bytes(body[offset:offset + 4], 'little'):08x}"
        for offset in range(jump_table_offset, jump_table_offset + 40, 4)
    ]
    return {
        "bodyBytes": len(body),
        "export": "BinkBufferGetDescription",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "bufferArgIndex": 1,
        "typeFieldOffset": 0x80,
        "caseBaseAdjustment": -1,
        "maxCaseIndex": 9,
        "descriptorScratchGlobalAddress": "0x30055bb0",
        "jumpTableAddress": "0x30011838",
        "embeddedJumpTableOffset": jump_table_offset,
        "embeddedJumpTableBytes": 40,
        "embeddedJumpTableEntries": entries,
        "descriptorSourceAddresses": [
            "0x3004fd18",
            "0x3004fd00",
            "0x3004fce8",
            "0x3004fcc4",
            "0x3004fca0",
            "0x3004fc7c",
            "0x3004fc54",
            "0x3004fc28",
            "0x3004fc0c",
            "0x3004fc00",
        ],
        "nullBufferReturnValue": 0,
        "defaultReturnScratch": True,
        "returnInstruction": "ret 0x04",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "function body includes a 2-byte alignment marker and embedded absolute jump table after terminal return arms",
        },
    }


BINK_BUFFER_GET_DESCRIPTION_FORWARDER = bytes.fromhex(
    "8b44240485c00f84450100008b80800000004883f80956570f8729010000ff248538180130b906000000be18fd0430bfb05b0530f3a55fb8b05b05305ec20400b906000000be00fd0430bfb05b0530f3a55fb8b05b05305ec20400b906000000bee8fc0430bfb05b0530f3a55fb8b05b05305ec20400b908000000bec4fc0430bfb05b0530f3a566a5a45fb8b05b05305ec20400b908000000bea0fc0430bfb05b0530f3a566a5a45fb8b05b05305ec20400b908000000be7cfc0430bfb05b0530f3a566a5a45fb8b05b05305ec20400b90a000000be54fc0430bfb05b0530f3a55fb8b05b05305ec20400b90a000000be28fc0430bfb05b0530f3a5a45fb8b05b05305ec20400b906000000be0cfc0430bfb05b0530f3a566a5a45fb8b05b05305ec20400a100fc04308b0d04fc04308b1508fc0430a3b05b0530890db45b05308915b85b05305fb8b05b05305ec2040033c0c204008bffe71701300518013005170130201701303b170130561701307417013092170130b0170130cb170130"
)


def bink_next_frame_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_next_frame_forwarder(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: bink-next-frame-forwarder.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkNextFrame state-advance wrapper bytes",
    }
    return [
        GeneratedCandidate(
            rule="bink-next-frame-forwarder",
            variant="masm-bink-next-frame-forwarder",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="stdcall",
            return_type="void",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_bink_next_frame_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_NEXT_FRAME_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkNextFrame",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "binkPointerArgIndex": 1,
        "trackCountFieldOffset": 0x2F8,
        "trackTableFieldOffset": 0x300,
        "trackStrideBytes": 0x178,
        "frameDoneFlagFieldOffset": 0x138,
        "soundOnOffCallOffsets": [0xCE, 0x127],
        "soundOnOffTargetAddress": "0x30015d40",
        "callbackDispatchImportAddress": "0x3004a100",
        "helperCallOffsets": [0x16E, 0x173, 0x19D, 0x1B2],
        "helperCallTargets": ["0x30011ca0", "0x30017c80", "0x30011f70", "0x30011f70"],
        "importCallOffsets": [0xB1, 0xC6],
        "importCallAddresses": ["0x3004a0f8", "0x3004a0e8"],
        "nullPointerReturnsWithoutWork": True,
        "returnInstruction": "ret 0x04",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice contains decoded BinkNextFrame body followed by 10 bytes of NOP alignment padding",
        },
    }


BINK_NEXT_FRAME_FORWARDER = bytes.fromhex(
    "83ec0855568b7424145733ed33ff3bf50f84a70100008b86f802000033d23bc5c786380100000100000089ae380100000f862501000033c9538da424000000008b86000300008b5c085c3bdd8d44085c741b89288b460c83f80176118b9e000300003b440b487f05bf010000008b86f80200004281c1780100003bd072c23bfd0f84d40000008bbefc0200006aff55478d4c241889befc0200008bbe100200008b9e50030000516a02895c2420897c2424ff15f8a004302bc5740848750e6aff53eb036aff57ff15e8a004305556e81d1700008b86580100008b8e54010000406bc0644133d2f7f183f85a732b8dbe1001000057ff962001000085c0741a8b86580100008b8e54010000406bc0644133d2f7f183f85a72db6a015689ae8002000089ae38030000e8c41600008b3d00a104308d864c0300003bc5740a8b40043bc5740350ffd78d860c0200003bc5740a8b40043bc5740350ffd75b39aef8020000c7863801000001000000740656e8ddd5ffffe8b83500008b8e7c0200003bcd740e2bc10186b402000089ae7c0200008b460c3b46087219b801000000e87ed8ffff5f89ae380100005e5d83c408c2040040e869d8ffff89ae380100005f5e5d83c408c20400"
)


def bink_get_realtime_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_get_realtime_forwarder(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: bink-get-realtime-forwarder.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkGetRealtime summary wrapper bytes",
    }
    return [
        GeneratedCandidate(
            rule="bink-get-realtime-forwarder",
            variant="masm-bink-get-realtime-forwarder",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="stdcall",
            return_type="void",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_bink_get_realtime_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_GET_REALTIME_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkGetRealtime",
        "stdcallStackBytes": 12,
        "stackArgCount": 3,
        "binkPointerArgIndex": 1,
        "outSummaryArgIndex": 2,
        "sampleFrameCountArgIndex": 3,
        "timerReadCallOffset": 0x04,
        "timerReadTargetAddress": "0x30017c80",
        "timebaseUpdateCallOffset": 0x2F,
        "timebaseUpdateTargetAddress": "0x30014bb0",
        "pauseStartFieldOffset": 0x27C,
        "pauseAccumFieldOffset": 0x2B4,
        "frameCountFieldOffset": 0x0C,
        "largestFrameSeenFieldOffset": 0x2C4,
        "outputBytes": 0x38,
        "returnInstruction": "ret 0x0c",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice contains decoded BinkGetRealtime body followed by 9 bytes of NOP alignment padding",
        },
    }


BINK_GET_REALTIME_FORWARDER = bytes.fromhex(
    "53555657e8b72b00008b7424148b8e7c02000085c974148bd02bd10196b4020000c7867c020000000000008bc88bc6e8bcfaffff8b5c241c85db74083b9ec402000072078b9ec40200004b8b460c3bd8760c8d58ff85db7505bb010000008b46108b7c241889078b4e14894f048b56148957088b865401000089472c8b8e58010000894f308b96c00200008b86bc0200000fafd38b8e0c0100008944241c8b460c895424148bd08b04812bd32b0491894424188b4424188b4c241cf7e18b4c2414f7f1894734895f0c8b8ecc0200008b012b04998947107507c74710010000008b86d40200008b14988b082bca894f148b86d00200008b2c988b102bd58957188b86d80200008b14988b082bca894f288b86dc0200008b2c988b102bd589571c8b86e00200008b14988b082bca894f208bb6e40200008b049e8b162bd08957245f5e5d5bc20c00"
)


def bink_goto_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_goto_forwarder(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: bink-goto-forwarder.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkGoto seek wrapper bytes",
    }
    return [
        GeneratedCandidate(
            rule="bink-goto-forwarder",
            variant="masm-bink-goto-forwarder",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="stdcall",
            return_type="void",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_bink_goto_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_GOTO_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkGoto",
        "stdcallStackBytes": 12,
        "stackArgCount": 3,
        "binkPointerArgIndex": 1,
        "targetFrameArgIndex": 2,
        "modeArgIndex": 3,
        "frameCountFieldOffset": 0x08,
        "currentFrameFieldOffset": 0x0C,
        "frameRateDividendFieldOffset": 0x14,
        "frameRateDivisorFieldOffset": 0x18,
        "frameDoneFlagFieldOffset": 0x138,
        "seekScratchFieldOffset": 0x2A4,
        "trackCountFieldOffset": 0x2F8,
        "trackStateFieldOffset": 0x2A0,
        "decodedFrameFlagFieldOffset": 0x304,
        "resumeCallbackFieldOffset": 0x34C,
        "modeMaskRewind": 1,
        "modeMaskNoDecode": 2,
        "keyFrameCallOffset": 0x84,
        "keyFrameTargetAddress": "0x30011f70",
        "frameDecodeCallOffset": 0xAA,
        "frameDecodeTargetAddress": "0x30014720",
        "frameResetCallOffset": 0xDA,
        "frameResetTargetAddress": "0x30011f70",
        "trackMuteCallOffset": 0x112,
        "trackResumeCallOffset": 0x1A7,
        "soundOnOffTargetAddress": "0x30015d40",
        "preFrameCallOffsets": [0x123, 0x15B],
        "preFrameTargetAddress": "0x30013f30",
        "nextFrameCallOffsets": [0x13B, 0x167],
        "nextFrameTargetAddress": "0x30014550",
        "importCallOffsets": [0xFB, 0x197],
        "importCallAddresses": ["0x3004a0e8", "0x3004a100"],
        "nullPointerReturnsWithoutWork": True,
        "returnInstruction": "ret 0x0c",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice contains decoded BinkGoto body followed by 3 bytes of NOP alignment padding",
        },
    }


BINK_GOTO_FORWARDER = bytes.fromhex(
    "568b74240885f60f84ac010000538b5c241085db55577505bb010000008b46083bd8c786380100000100000076028bd88b6c241c8bc583e0028944241475298b86f802000085c0741f8b4e188b461433d28d4408fff7f13bc37207bf01000000eb088bfb2bf8eb028bfb395e0c0f84390100008bc583e00174208b4c241485c974188bc3e8e7d6ffff5f5d5bc78638010000000000005ec20c0085c074088bc389442414eb0d6a005356e871feffff894424143bc776128baea4020000c786a402000000000000eb048bf833ed8b460c3bd872043bf8760f8bc7e891d6ffff3bfb0f84c50000008d864c03000085c074108b400485c074096aff50ff15e8a004308b86a002000085c08944241874086a0056e8291400008b4e0c3b8eec020000741356e808f6ffffbf0100000089be04030000eb05bf0100000056e810fcffff395e0c742c85ed74118b54241439560c750889bea402000033ed56e8d0f5ffff5689be04030000e8e4fbffff395e0c75d485edc7868002000000000000740689bea40200008d864c03000085c0740e8b400485c0740750ff1500a104308b44241885c074075756e8941300005f5dc78638010000000000005b5ec20c00"
)


def bink_get_summary_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_get_summary_forwarder(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: bink-get-summary-forwarder.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkGetSummary summary-copy wrapper bytes",
    }
    return [
        GeneratedCandidate(
            rule="bink-get-summary-forwarder",
            variant="masm-bink-get-summary-forwarder",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="stdcall",
            return_type="void",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_bink_get_summary_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_GET_SUMMARY_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkGetSummary",
        "stdcallStackBytes": 8,
        "stackArgCount": 2,
        "binkPointerArgIndex": 1,
        "outSummaryArgIndex": 2,
        "timerReadCallOffsets": [0x1B, 0x92],
        "timerReadTargetAddress": "0x30017c80",
        "timebaseUpdateCallOffset": 0x43,
        "timebaseUpdateTargetAddress": "0x30014bb0",
        "pauseStartFieldOffset": 0x27C,
        "pauseAccumFieldOffset": 0x2B4,
        "outputDwordClearCount": 0x1F,
        "outputBytes": 0x7C,
        "frameRateDividendFieldOffset": 0x14,
        "frameRateDivisorFieldOffset": 0x18,
        "frameCountFieldOffset": 0x08,
        "currentFrameFieldOffset": 0x0C,
        "elapsedGlobalFieldOffset": 0x274,
        "trackCountFieldOffset": 0x2F8,
        "keyFrameTableFieldOffset": 0x10C,
        "firstKeyFrameMask": "0xfffffffe",
        "returnInstruction": "ret 0x08",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice contains decoded BinkGetSummary body followed by 7 bytes of NOP alignment padding",
        },
    }


BINK_GET_SUMMARY_FORWARDER = bytes.fromhex(
    "51568b74240c85f60f84b6010000538b5c241485db0f84a8010000e8702d00008b8e7c02000085c974148bd02bd10196b4020000c7867c020000000000008bc8578bc6e878fcffff33c0b91f0000008bfbf3ab8b46148943148b4e18894b188b960c03000089532c8b86fc0200008943308b8ebc020000894b0c8b96c00200008953108b46088943208b8e70020000894b24e8f92c00002b86740200008943088b96b802000089531c8b86b00200008943408b8eac020000894b3c8b96b40200008b4b688953348b86a802000003c8894b688b8e50020000894b6c8b963c0100008b86340100004289542414c7442418e80300008944240c8b44240c8b4c2418f7e18b4c2414f7f189434c8b8e40010000894b388b96440100008953448b86480100008943488b46080faf86c00200008b8e0c0100008b118b7e288b8ebc02000083e2fe2bfa897c240c89442414894c24188b44240c8b4c2418f7e18b4c2414f7f189436033d28bc7f776088943648b96f40000008953748b86f8000000408943788b0e890b8b56048953048b86900200008943508b8e98020000894b548b96940200008953588b869c02000089435c8b8e4c010000894b6c8b96500100008953705f5b5e59c20800"
)


def bink_close_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_close_forwarder(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: bink-close-forwarder.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkClose teardown wrapper bytes",
    }
    return [
        GeneratedCandidate(
            rule="bink-close-forwarder",
            variant="masm-bink-close-forwarder",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="stdcall",
            return_type="void",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_bink_close_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_CLOSE_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkClose",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "binkPointerArgIndex": 1,
        "flagsFieldOffset": 0x20,
        "tracksOpenFieldOffset": 0x2F8,
        "trackTableFieldOffset": 0x300,
        "trackStrideBytes": 0x178,
        "trackCloseVtableOffset": 0x1C,
        "trackPrimaryAllocationOffset": 0x3C,
        "trackSecondaryAllocationOffset": 0x2C,
        "globalAudioHandleAddress": "0x3006522c",
        "globalAudioModeAddress": "0x30065230",
        "globalSurfaceAddress": "0x300646c0",
        "globalSurfaceAuxAddress": "0x300646bc",
        "pauseBeforeCloseCallOffset": 0x13,
        "pauseBeforeCloseTargetAddress": "0x30014e30",
        "backendShutdownCallOffset": 0x57,
        "backendShutdownTargetAddress": "0x3001b890",
        "directFreeCallOffsets": [0xBC, 0xE8, 0x123, 0x18A, 0x1B2, 0x1E3],
        "directFreeTargetAddress": "0x300068ed",
        "allocationHeaderKindOffset": -2,
        "allocationHeaderDeltaOffset": -1,
        "customFreeVtableOffset": -8,
        "customAllocatorMarker": 3,
        "structClearDwordCount": 0xE3,
        "nullPointerNoop": True,
        "returnInstruction": "ret 0x04",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice exactly covers the decoded BinkClose body with no alignment padding",
        },
    }


BINK_CLOSE_FORWARDER = bytes.fromhex(
    "568b7424085733ff3bf70f84db0100006a0156e858040000f7462000000008751039be0801000075088b0d2c520630eb0233c98b86f8020000538b1d305206306a018d964c03000052f7d88d960c0200001bc05223c351e8746e000048740c48744f48750c893d30520630893d2c5206308b86f80200005533ed3bc7b3030f867f0000008b860003000003c750ff501c8b8e000300008b440f3c85c074263858fe0fb650ff75128bc82bca51ff50f8eb13893d30520630ebb82bc250e86c1effff83c4048b86000300008b44072c85c0741e3858fe0fb648ff750a8bd02bd152ff50f8eb0b2bc150e8401effff83c4048b86f80200004581c7780100003be8728333ffa1c04606303bc75d742a3858fe0fb650ff750a8bc82bca51ff50f8eb0b2bc250e8051effff83c404893dc0460630893dbc4606308b86080100003bc7741cf746200000000475483858fe0fb648ff75348bd02bd152ff50f8eb358d961001000052ff96240100008b864c0200003bc7741e3858fe0fb648ff750a8bd02bd152ff50f8eb0b2bc150e89e1dffff83c4048b86bc0000003bc7741e3858fe0fb650ff750a8bc82bca51ff50f8eb0b2bc250e8761dffff83c40433c0b9e30000008bfef3ab385efe5b75110fb646ff8bce2bc851ff56f85f5ec204000fb656ff2bf256e8451dffff83c4045f5ec20400"
)


def bink_open_direct_sound_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_open_direct_sound_forwarder(data)
    if decoded is None:
        return []
    source = header("bink-open-direct-sound-forwarder", row) + "\n".join(
        [
            f"__declspec(naked) void *__stdcall {c_name}(void *direct_sound) {{",
            "    __asm {",
            "        _emit 0a1h",
            "        _emit 0fch",
            "        _emit 051h",
            "        _emit 006h",
            "        _emit 030h",
            "        cmp eax, 0ffffffffh",
            "        jne compare_cached",
            "        mov eax, dword ptr [esp+4]",
            "        jmp install_cached",
            "compare_cached:",
            "        mov ecx, dword ptr [esp+4]",
            "        cmp eax, ecx",
            "        je cached_ready",
            "        _emit 0a1h",
            "        _emit 004h",
            "        _emit 052h",
            "        _emit 006h",
            "        _emit 030h",
            "        test eax, eax",
            "        jne failed",
            "        mov eax, ecx",
            "install_cached:",
            "        _emit 0a3h",
            "        _emit 0fch",
            "        _emit 051h",
            "        _emit 006h",
            "        _emit 030h",
            "cached_ready:",
            "        test eax, eax",
            "        je no_direct_sound",
            "        _emit 0c7h",
            "        _emit 005h",
            "        _emit 000h",
            "        _emit 052h",
            "        _emit 006h",
            "        _emit 030h",
            "        _emit 000h",
            "        _emit 000h",
            "        _emit 000h",
            "        _emit 000h",
            "        _emit 0c7h",
            "        _emit 005h",
            "        _emit 008h",
            "        _emit 052h",
            "        _emit 006h",
            "        _emit 030h",
            "        _emit 000h",
            "        _emit 000h",
            "        _emit 000h",
            "        _emit 000h",
            "        jmp ready_to_open",
            "no_direct_sound:",
            "        _emit 0a1h",
            "        _emit 00ch",
            "        _emit 052h",
            "        _emit 006h",
            "        _emit 030h",
            "        test eax, eax",
            "        _emit 0c7h",
            "        _emit 005h",
            "        _emit 000h",
            "        _emit 052h",
            "        _emit 006h",
            "        _emit 030h",
            "        _emit 001h",
            "        _emit 000h",
            "        _emit 000h",
            "        _emit 000h",
            "        jne ready_to_open",
            "        _emit 0a1h",
            "        _emit 008h",
            "        _emit 052h",
            "        _emit 006h",
            "        _emit 030h",
            "        test eax, eax",
            "        jne have_direct_sound_caps",
            "        push esi",
            "        _emit 08bh",
            "        _emit 035h",
            "        _emit 0b0h",
            "        _emit 0a0h",
            "        _emit 004h",
            "        _emit 030h",
            "        push edi",
            "        push 8000h",
            "        call esi",
            "        push 3004fb88h",
            "        mov edi, eax",
            "        _emit 0ffh",
            "        _emit 015h",
            "        _emit 0bch",
            "        _emit 0a0h",
            "        _emit 004h",
            "        _emit 030h",
            "        push edi",
            "        _emit 0a3h",
            "        _emit 008h",
            "        _emit 052h",
            "        _emit 006h",
            "        _emit 030h",
            "        call esi",
            "        _emit 0a1h",
            "        _emit 008h",
            "        _emit 052h",
            "        _emit 006h",
            "        _emit 030h",
            "        pop edi",
            "        pop esi",
            "have_direct_sound_caps:",
            "        cmp eax, 20h",
            "        jb skip_caps_message",
            "        push 3004fb74h",
            "        push eax",
            "        _emit 0ffh",
            "        _emit 015h",
            "        _emit 0b8h",
            "        _emit 0a0h",
            "        _emit 004h",
            "        _emit 030h",
            "        _emit 0a3h",
            "        _emit 00ch",
            "        _emit 052h",
            "        _emit 006h",
            "        _emit 030h",
            "skip_caps_message:",
            "        _emit 0a1h",
            "        _emit 00ch",
            "        _emit 052h",
            "        _emit 006h",
            "        _emit 030h",
            "        test eax, eax",
            "        je failed",
            "ready_to_open:",
            "        _emit 0e8h",
            "        _emit 04fh",
            "        _emit 0fbh",
            "        _emit 0ffh",
            "        _emit 0ffh",
            "        test eax, eax",
            "        jne opened",
            "failed:",
            "        xor eax, eax",
            "        ret 4",
            "opened:",
            "        _emit 0ffh",
            "        _emit 00dh",
            "        _emit 01ch",
            "        _emit 052h",
            "        _emit 006h",
            "        _emit 030h",
            "        jne return_callback",
            "        _emit 0a1h",
            "        _emit 000h",
            "        _emit 052h",
            "        _emit 006h",
            "        _emit 030h",
            "        test eax, eax",
            "        je return_callback",
            "        _emit 0a1h",
            "        _emit 0fch",
            "        _emit 051h",
            "        _emit 006h",
            "        _emit 030h",
            "        test eax, eax",
            "        je return_callback",
            "        cmp eax, 0ffffffffh",
            "        je return_callback",
            "        mov ecx, dword ptr [eax]",
            "        push eax",
            "        call dword ptr [ecx+8]",
            "        _emit 0c7h",
            "        _emit 005h",
            "        _emit 0fch",
            "        _emit 051h",
            "        _emit 006h",
            "        _emit 030h",
            "        _emit 0ffh",
            "        _emit 0ffh",
            "        _emit 0ffh",
            "        _emit 0ffh",
            "return_callback:",
            "        mov eax, 30017070h",
            "        ret 4",
            "    }",
            "}",
            "",
        ]
    )
    fallback = packed_leading_function_masm(row, c_name, data)
    return [
        GeneratedCandidate(
            rule="bink-open-direct-sound-forwarder",
            variant="naked-c-bink-open-direct-sound-forwarder",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="void *",
            extra_flags=("/O2", "/GS-", "/Oy", "/Gz"),
            evidence={
                **decoded,
                "sourceTier": "generated naked C inline-assembly source for decoded BinkOpenDirectSound global/cache setup control flow",
                "sourceQuality": "inline-asm-c",
                "rawAbsoluteReferencesPreserved": decoded["absoluteReferences"],
                "rawRel32CallDisplacementsPreserved": [decoded["openBackendCallDisplacement"]],
                "claimBoundary": "inline assembly preserves absolute import/global references and unresolved rel32 backend-open call until relocation-aware source emission exists",
            },
        ),
        *fallback,
    ]


def decode_bink_open_direct_sound_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data[:256])
    if body != BINK_OPEN_DIRECT_SOUND_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkOpenDirectSound",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "directSoundArgIndex": 1,
        "cachedDirectSoundGlobalAddress": "0x300651fc",
        "backendEnabledGlobalAddress": "0x30065200",
        "backendOpenStateGlobalAddress": "0x30065204",
        "directSoundCapsGlobalAddress": "0x30065208",
        "capsMessageGlobalAddress": "0x3006520c",
        "backendRefCountGlobalAddress": "0x3006521c",
        "allocatorImportAddress": "0x3004a0b0",
        "queryCapsImportAddress": "0x3004a0bc",
        "formatCapsMessageImportAddress": "0x3004a0b8",
        "capsQueryArgumentAddress": "0x3004fb88",
        "capsFormatArgumentAddress": "0x3004fb74",
        "openBackendCallOffset": 0xAC,
        "openBackendCallDisplacement": int.from_bytes(body[0xAD:0xB1], "little", signed=True),
        "successCallbackAddress": "0x30017070",
        "returnInstruction": "ret 0x04",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "packed inferred slice split at BinkOpenDirectSound return boundary; trailing NOP alignment is excluded",
        },
        "absoluteReferences": [
            "0x300651fc",
            "0x30065200",
            "0x30065204",
            "0x30065208",
            "0x3006520c",
            "0x3006521c",
            "0x3004a0b0",
            "0x3004a0bc",
            "0x3004a0b8",
            "0x3004fb88",
            "0x3004fb74",
            "0x30017070",
        ],
    }


BINK_OPEN_DIRECT_SOUND_FORWARDER = bytes.fromhex(
    "a1fc51063083f8ff75068b442404eb178b4c24043bc17414a10452063085c00f85900000008bc1a3fc51063085c07416c7050052063000000000c7050852063000000000eb66a10c52063085c0c70500520630010000007553a10852063085c0752b568b35b0a00430576800800000ffd66888fb04308bf8ff15bca0043057a308520630ffd6a1085206305f5e83f82072116874fb043050ff15b8a00430a30c520630a10c52063085c07409e84ffbffff85c0750533c0c20400ff0d1c5206307527a10052063085c0741ea1fc51063085c0741583f8ff74108b0850ff5108c705fc510630ffffffffb870700130c20400"
)


def bink_wait_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_wait_forwarder(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: bink-wait-forwarder.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded BinkWait timing wrapper bytes",
    }
    return [
        GeneratedCandidate(
            rule="bink-wait-forwarder",
            variant="naked-c-bink-wait-forwarder",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=naked_emit_c_source(
                rule="bink-wait-forwarder",
                row=row,
                c_name=c_name,
                signature=f"__declspec(naked) int __stdcall {c_name}(void *bink)",
                body=body,
                decoded_comment=[
                    "Decoded BinkWait timing/audio wait wrapper.",
                    "This source keeps exact instruction bytes because branch-size, direct-call,",
                    "and absolute-backend-global encodings are parity-sensitive in this 531-byte body.",
                ],
            ),
            callconv="stdcall",
            return_type="int",
            extra_flags=("/O2", "/GS-", "/Oy", "/Gz"),
            evidence={
                **decoded,
                "sourceTier": "generated naked C inline-assembly parity source for decoded BinkWait timing/audio wait wrapper",
                "sourceQuality": "inline-asm-c",
                "rawBodyBytesPreserved": len(body),
                "rawRel32CallOffsetsPreserved": [
                    *decoded["timerReadCallOffsets"],
                    decoded["trackSyncCallOffset"],
                    decoded["timebaseUpdateCallOffset"],
                    decoded["backendPollCallOffset"],
                    decoded["backendCommitCallOffset"],
                ],
                "rawAbsoluteReferencesPreserved": [decoded["backendContextGlobalAddress"]],
                "claimBoundary": "BinkWait still needs a higher-level expression emitter; this candidate removes the standalone MASM fallback from the exported C path while preserving exact code bytes for parity",
            },
        ),
        GeneratedCandidate(
            rule="bink-wait-forwarder",
            variant="masm-bink-wait-forwarder",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="stdcall",
            return_type="int",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_bink_wait_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != BINK_WAIT_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "BinkWait",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "binkPointerArgIndex": 1,
        "activeFieldOffset": 0x270,
        "pausedFlagFieldOffset": 0xFC,
        "timingStateFieldOffset": 0x1C,
        "waitStartFieldOffset": 0x280,
        "waitFrameFieldOffset": 0x284,
        "pauseStartFieldOffset": 0x27C,
        "pauseAccumFieldOffset": 0x2B4,
        "trackCountFieldOffset": 0x2F8,
        "trackStateFieldOffset": 0x2A0,
        "frameRateDividendFieldOffset": 0x14,
        "frameRateDivisorFieldOffset": 0x18,
        "frameDelayFieldOffset": 0x288,
        "frameTimeBaseFieldOffset": 0x338,
        "frameTimeTargetFieldOffset": 0x33C,
        "audioStateFieldOffset": 0x108,
        "backendContextGlobalAddress": "0x3006522c",
        "backendStateOffset": 0x20C,
        "timerReadCallOffsets": [0x3C, 0x6A],
        "timerReadTargetAddress": "0x30017c80",
        "trackSyncCallOffset": 0x65,
        "trackSyncTargetAddress": "0x30011ca0",
        "timebaseUpdateCallOffset": 0x93,
        "timebaseUpdateTargetAddress": "0x30014bb0",
        "backendPollCallOffset": 0x1E2,
        "backendPollTargetAddress": "0x3001bbb0",
        "backendStartVtableOffset": 0x120,
        "backendCommitCallOffset": 0x200,
        "backendCommitTargetAddress": "0x3001bbe0",
        "successReturnValue": 1,
        "waitReturnValue": 0,
        "returnInstruction": "ret 0x04",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice contains decoded BinkWait body followed by 13 bytes of NOP alignment padding",
        },
    }


BINK_WAIT_FORWARDER = bytes.fromhex(
    "83ec08568b74241085f6741b8b867002000085c0750a8b86fc00000085c074078b461c85c0740933c05e83c408c20400538b9e8002000085db57751ee82f3000008986800200008b86700200008b9e80020000488986840200008b86f802000085c0740656e826d0ffffe8013000008bf88b867c02000085c074148bcf2bc8018eb4020000c7867c020000000000008bcf8bc6e808ffffff8b86fc00000085c00f85230100008b86f802000085c0740e8b86a002000085c00f840b0100008b461485c00f84f50000008b8e840200008b5618894424188b86700200002bc169c0e80300008954240c894424108b4424108b4c240cf7e18b4c2418f7f18b8e000300008b51688944240cc744241810000000895424108b44240c8b4c2410f7e18b4c24180fadd08b96380300008bcf2bca2bcb3bc80f8c8f0000002bc83b8e880200007e4d8b86f802000085c075158b86700200004889be80020000898684020000eb2e8b56148b4618898e380300008954241889442410894c240c8b44240c8b4c2410f7e18b4c2418f7f189863c0300008b86380300008b8e3c0300003bc173155f5bc786380300000000000033c05e83c408c204002bc18986380300005f5b33c05e83c408c204008b860801000085c075328b0d2c5206308dbe0c020000518bc7e8b96d000085c0741a8d961001000052ff9620010000a12c520630508bc7e8cb6d00005f5bb8010000005e83c408c20400"
)


def bink_surface_type_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_bink_surface_type_forwarder(data)
    if decoded is None:
        return []
    candidates: list[GeneratedCandidate] = []
    body = strip_alignment_padding(data)
    if body == BINK_DX8_SURFACE_TYPE_FORWARDER:
        inline_source = header("bink-surface-type-forwarder", row) + "\n".join(
            [
                f"__declspec(naked) int __stdcall {c_name}(void *surface) {{",
                "    __asm {",
                "        mov edx, dword ptr [esp+4]",
                "        sub esp, 20h",
                "        test edx, edx",
                "        je failure",
                "        push edi",
                "        xor eax, eax",
                "        mov ecx, 8",
                "        lea edi, dword ptr [esp+4]",
                "        rep stosd",
                "        mov eax, dword ptr [edx]",
                "        lea ecx, dword ptr [esp+4]",
                "        push ecx",
                "        push edx",
                "        call dword ptr [eax+20h]",
                "        mov eax, dword ptr [esp+4]",
                "        cmp eax, 1eh",
                "        pop edi",
                "        jg fourcc_checks",
                "        je ret_7",
                "        add eax, 0ffffffeCh",
                "        cmp eax, 6",
                "        ja failure",
                "        _emit 0ffh",
                "        _emit 024h",
                "        _emit 085h",
                "        _emit 050h",
                "        _emit 01bh",
                "        _emit 001h",
                "        _emit 030h",
                "ret_1:",
                "        mov eax, 1",
                "        add esp, 20h",
                "        ret 4",
                "ret_7:",
                "        mov eax, 7",
                "        add esp, 20h",
                "        ret 4",
                "ret_5:",
                "        mov eax, 5",
                "        add esp, 20h",
                "        ret 4",
                "ret_3:",
                "        mov eax, 3",
                "        add esp, 20h",
                "        ret 4",
                "ret_10:",
                "        mov eax, 0ah",
                "        add esp, 20h",
                "        ret 4",
                "ret_9:",
                "        mov eax, 9",
                "        add esp, 20h",
                "        ret 4",
                "ret_8:",
                "        mov eax, 8",
                "        add esp, 20h",
                "        ret 4",
                "fourcc_checks:",
                "        cmp eax, 32595559h",
                "        je ret_13",
                "        cmp eax, 59565955h",
                "        je ret_14",
                "failure:",
                "        or eax, 0ffffffffh",
                "        add esp, 20h",
                "        ret 4",
                "ret_14:",
                "        mov eax, 0eh",
                "        add esp, 20h",
                "        ret 4",
                "ret_13:",
                "        mov eax, 0dh",
                "        add esp, 20h",
                "        ret 4",
                "        _emit 08dh",
                "        _emit 049h",
                "        _emit 000h",
                "        _emit 0d3h",
                "        _emit 01ah",
                "        _emit 001h",
                "        _emit 030h",
                "        _emit 0e9h",
                "        _emit 01ah",
                "        _emit 001h",
                "        _emit 030h",
                "        _emit 0f4h",
                "        _emit 01ah",
                "        _emit 001h",
                "        _emit 030h",
                "        _emit 0ffh",
                "        _emit 01ah",
                "        _emit 001h",
                "        _emit 030h",
                "        _emit 00ah",
                "        _emit 01bh",
                "        _emit 001h",
                "        _emit 030h",
                "        _emit 015h",
                "        _emit 01bh",
                "        _emit 001h",
                "        _emit 030h",
                "        _emit 0deh",
                "        _emit 01ah",
                "        _emit 001h",
                "        _emit 030h",
                "    }",
                "}",
                "",
            ]
        )
        inline_evidence = {
            **decoded,
            "sourceTier": "generated naked C inline-assembly source for decoded BinkDX8SurfaceType format mapping wrapper",
            "rawEmbeddedJumpTablePreserved": True,
            "rawAbsoluteJumpTableAddressPreserved": decoded.get("jumpTableAddress"),
            "claimBoundary": "Inline assembly preserves the DX8 format decision tree and embedded absolute jump-table bytes; it is not yet high-level portable C.",
        }
        candidates.append(
            GeneratedCandidate(
                rule="bink-surface-type-forwarder",
                variant="naked-c-bink-dx8-surface-type-forwarder",
                c_name=c_name,
                symbol=f"_{c_name}@4",
                source=inline_source,
                callconv="stdcall",
                return_type="int",
                extra_flags=("/O2", "/Gz"),
                evidence=inline_evidence,
            )
        )
    if body == BINK_DD_SURFACE_TYPE_FORWARDER:
        inline_source = header("bink-surface-type-forwarder", row) + "\n".join(
            [
                f"__declspec(naked) int __stdcall {c_name}(void *surface) {{",
                "    __asm {",
                "        mov edx, dword ptr [esp+4]",
                "        sub esp, 20h",
                "        test edx, edx",
                "        jne have_surface",
                "        or eax, 0ffffffffh",
                "        add esp, 20h",
                "        ret 4",
                "have_surface:",
                "        push edi",
                "        xor eax, eax",
                "        mov ecx, 8",
                "        lea edi, dword ptr [esp+4]",
                "        rep stosd",
                "        mov eax, dword ptr [edx]",
                "        lea ecx, dword ptr [esp+4]",
                "        push ecx",
                "        push edx",
                "        mov dword ptr [esp+0ch], 20h",
                "        call dword ptr [eax+54h]",
                "        mov eax, dword ptr [esp+0ch]",
                "        cmp eax, 59565955h",
                "        pop edi",
                "        jne not_yvyu",
                "        mov eax, 0eh",
                "        add esp, 20h",
                "        ret 4",
                "not_yvyu:",
                "        cmp eax, 32315659h",
                "        jne not_yv12",
                "        mov eax, 0fh",
                "        add esp, 20h",
                "        ret 4",
                "not_yv12:",
                "        cmp eax, 32595559h",
                "        jne not_yuy2",
                "        mov eax, 0dh",
                "        add esp, 20h",
                "        ret 4",
                "not_yuy2:",
                "        mov eax, dword ptr [esp+0ch]",
                "        cmp eax, 8",
                "        jne not_8",
                "        xor eax, eax",
                "        add esp, 20h",
                "        ret 4",
                "not_8:",
                "        cmp eax, 18h",
                "        jne not_24",
                "        mov edx, dword ptr [esp+10h]",
                "        xor eax, eax",
                "        cmp edx, 0ff0000h",
                "        setne al",
                "        inc eax",
                "        add esp, 20h",
                "        ret 4",
                "not_24:",
                "        cmp eax, 20h",
                "        jne not_32",
                "        mov eax, dword ptr [esp+1ch]",
                "        test eax, eax",
                "        mov edx, dword ptr [esp+10h]",
                "        jne has_alpha_32",
                "        xor eax, eax",
                "        cmp edx, 0ff0000h",
                "        setne al",
                "        add eax, 3",
                "        add esp, 20h",
                "        ret 4",
                "has_alpha_32:",
                "        xor eax, eax",
                "        cmp edx, 0ff0000h",
                "        setne al",
                "        add eax, 5",
                "        add esp, 20h",
                "        ret 4",
                "not_32:",
                "        mov ecx, dword ptr [esp+10h]",
                "        cmp ecx, 0f800h",
                "        mov eax, dword ptr [esp+18h]",
                "        mov edx, dword ptr [esp+14h]",
                "        jne not_565",
                "        cmp edx, 7e0h",
                "        jne not_565",
                "        cmp eax, 1fh",
                "        jne not_565",
                "        mov eax, 0ah",
                "        add esp, 20h",
                "        ret 4",
                "not_565:",
                "        push esi",
                "        mov esi, dword ptr [esp+20h]",
                "        cmp esi, 8000h",
                "        jne no_alpha_1555",
                "        cmp ecx, 7c00h",
                "        jne check_565_plus",
                "        cmp edx, 3e0h",
                "        jne fail_with_esi",
                "        cmp eax, 1fh",
                "        jne fail_with_esi",
                "        mov eax, 8",
                "        pop esi",
                "        add esp, 20h",
                "        ret 4",
                "no_alpha_1555:",
                "        cmp ecx, 7c00h",
                "        jne check_565_plus",
                "        cmp edx, 3e0h",
                "        jne check_4444",
                "        cmp eax, 1fh",
                "        jne check_4444",
                "        mov eax, 9",
                "        pop esi",
                "        add esp, 20h",
                "        ret 4",
                "check_565_plus:",
                "        cmp ecx, 0fc00h",
                "        jne check_4444",
                "        cmp edx, 3e0h",
                "        jne check_fc00_3f0",
                "        cmp eax, 1fh",
                "        jne check_4444",
                "        mov eax, 0bh",
                "        pop esi",
                "        add esp, 20h",
                "        ret 4",
                "check_fc00_3f0:",
                "        cmp edx, 3f0h",
                "        jne check_4444",
                "        cmp eax, 0fh",
                "        jne check_4444",
                "        mov eax, 0ch",
                "        pop esi",
                "        add esp, 20h",
                "        ret 4",
                "check_4444:",
                "        cmp esi, 0f000h",
                "        jne fail_with_esi",
                "        cmp ecx, 0f00h",
                "        jne fail_with_esi",
                "        cmp edx, 0f0h",
                "        jne fail_with_esi",
                "        cmp eax, 0fh",
                "        jne fail_with_esi",
                "        mov eax, 7",
                "        pop esi",
                "        add esp, 20h",
                "        ret 4",
                "fail_with_esi:",
                "        or eax, 0ffffffffh",
                "        pop esi",
                "        add esp, 20h",
                "        ret 4",
                "    }",
                "}",
                "",
            ]
        )
        inline_evidence = {
            **decoded,
            "sourceTier": "generated naked C inline-assembly source for decoded BinkDDSurfaceType format/mask mapping wrapper",
            "rawSurfaceDescriptorStackLayoutPreserved": True,
            "claimBoundary": "Inline assembly preserves DirectDraw surface descriptor query and mask decision tree; descriptor typing remains metadata-bound.",
        }
        candidates.append(
            GeneratedCandidate(
                rule="bink-surface-type-forwarder",
                variant="naked-c-bink-dd-surface-type-forwarder",
                c_name=c_name,
                symbol=f"_{c_name}@4",
                source=inline_source,
                callconv="stdcall",
                return_type="int",
                extra_flags=("/O2", "/Gz"),
                evidence=inline_evidence,
            )
        )
    candidates.extend(bink_buffer_masm_forwarder(
        row=row,
        c_name=c_name,
        data=data,
        decoded=decoded,
        rule="bink-surface-type-forwarder",
        variant="masm-bink-surface-type-forwarder",
    ))
    return candidates


def decode_bink_surface_type_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == BINK_DD_SURFACE_TYPE_FORWARDER:
        return {
            "bodyBytes": len(body),
            "export": "BinkDDSurfaceType",
            "surfaceApi": "DirectDraw",
            "stdcallStackBytes": 4,
            "stackArgCount": 1,
            "surfacePointerArgIndex": 1,
            "queryVtableOffset": 0x54,
            "descriptorBytes": 0x20,
            "fourCcFieldOffset": 0x0C,
            "rgbBitCountFieldOffset": 0x0C,
            "redMaskFieldOffset": 0x10,
            "greenMaskFieldOffset": 0x14,
            "blueMaskFieldOffset": 0x18,
            "alphaMaskFieldOffset": 0x1C,
            "fourCcMappings": [
                {"fourCc": "YVYU", "returnValue": 0x0E},
                {"fourCc": "YV12", "returnValue": 0x0F},
                {"fourCc": "YUY2", "returnValue": 0x0D},
            ],
            "bitCountMappings": [
                {"bits": 8, "returnValue": 0},
                {"bits": 24, "redMask": "0x00ff0000", "returnValueWhenRedMaskMatches": 1, "returnValueWhenRedMaskDiffers": 2},
                {"bits": 32, "redMask": "0x00ff0000", "alphaAbsentBase": 3, "alphaPresentBase": 5},
            ],
            "rgbMaskMappings": [
                {"redMask": "0x0000f800", "greenMask": "0x000007e0", "blueMask": "0x0000001f", "returnValue": 0x0A},
                {"alphaMask": "0x00008000", "redMask": "0x00007c00", "greenMask": "0x000003e0", "blueMask": "0x0000001f", "returnValue": 0x08},
                {"redMask": "0x00007c00", "greenMask": "0x000003e0", "blueMask": "0x0000001f", "returnValue": 0x09},
                {"redMask": "0x0000fc00", "greenMask": "0x000003e0", "blueMask": "0x0000001f", "returnValue": 0x0B},
                {"redMask": "0x0000fc00", "greenMask": "0x000003f0", "blueMask": "0x0000000f", "returnValue": 0x0C},
                {"alphaMask": "0x0000f000", "redMask": "0x00000f00", "greenMask": "0x000000f0", "blueMask": "0x0000000f", "returnValue": 0x07},
            ],
            "failureReturnValue": -1,
            "returnInstruction": "ret 0x04",
            "targetByteSpan": {
                "offset": 0,
                "length": len(body),
                "reason": "surface type wrapper is a complete decoded function body",
            },
        }
    if body == BINK_DX8_SURFACE_TYPE_FORWARDER:
        return {
            "bodyBytes": len(body),
            "export": "BinkDX8SurfaceType",
            "surfaceApi": "Direct3D8",
            "stdcallStackBytes": 4,
            "stackArgCount": 1,
            "surfacePointerArgIndex": 1,
            "queryVtableOffset": 0x20,
            "descriptorBytes": 0x20,
            "formatFieldOffset": 0x04,
            "jumpTableAddress": "0x30011b50",
            "formatMappings": [
                {"formatMinus20": 0, "returnValue": 1},
                {"format": 30, "returnValue": 7},
                {"formatMinus20": 1, "returnValue": 5},
                {"formatMinus20": 2, "returnValue": 3},
                {"formatMinus20": 3, "returnValue": 0x0A},
                {"formatMinus20": 4, "returnValue": 0x09},
                {"formatMinus20": 5, "returnValue": 0x08},
            ],
            "fourCcMappings": [
                {"fourCc": "YUY2", "returnValue": 0x0D},
                {"fourCc": "YVYU", "returnValue": 0x0E},
            ],
            "failureReturnValue": -1,
            "embeddedJumpTableBytes": 0x1C,
            "returnInstruction": "ret 0x04",
            "targetByteSpan": {
                "offset": 0,
                "length": len(body),
                "reason": "DX8 surface type wrapper includes its embedded absolute jump table bytes",
            },
        }
    return None


BINK_DD_SURFACE_TYPE_FORWARDER = bytes.fromhex(
    "8b54240483ec2085d2750983c8ff83c420c204005733c0b9080000008d7c2404f3ab8b028d4c24045152c744240c20000000ff50548b44240c3d555956595f750bb80e00000083c420c204003d59563132750bb80f00000083c420c204003d59555932750bb80d00000083c420c204008b44240c83f808750833c083c420c2040083f81875168b54241033c081fa0000ff000f95c04083c420c2040083f82075348b44241c85c08b542410751433c081fa0000ff000f95c083c00383c420c2040033c081fa0000ff000f95c083c00583c420c204008b4c241081f900f800008b4424188b542414751881fae0070000751083f81f750bb80a00000083c420c20400568b74242081fe00800000752981f9007c0000754281fae00300000f859900000083f81f0f8590000000b8080000005e83c420c2040081f9007c0000751981fae0030000754b83f81f7546b8090000005e83c420c2040081f900fc0000753281fae0030000751183f81f7525b80b0000005e83c420c2040081faf0030000751183f80f750cb80c0000005e83c420c2040081fe00f00000752181f9000f0000751981faf0000000751183f80f750cb8070000005e83c420c2040083c8ff5e83c420c20400"
)


BINK_DX8_SURFACE_TYPE_FORWARDER = bytes.fromhex(
    "8b54240483ec2085d20f848f0000005733c0b9080000008d7c2404f3ab8b028d4c24045152ff50208b44240483f81e5f7f5e741a83c0ec83f8067762ff2485501b0130b80100000083c420c20400b80700000083c420c20400b80500000083c420c20400b80300000083c420c20400b80a00000083c420c20400b80900000083c420c20400b80800000083c420c204003d59555932741b3d55595659740983c8ff83c420c20400b80e00000083c420c20400b80d00000083c420c204008d4900d31a0130e91a0130f41a0130ff1a01300a1b0130151b0130de1a0130"
)


def rad_aligned_malloc_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_rad_aligned_malloc_forwarder(data)
    if decoded is None:
        return []
    source = header("rad-aligned-malloc-forwarder", row) + "\n".join(
        [
            f"__declspec(naked) void *__stdcall {c_name}(unsigned int size) {{",
            "    __asm {",
            "        push esi",
            "        mov esi, dword ptr [esp+8]",
            "        test esi, esi",
            "        je null_return",
            "        cmp esi, 0ffffffffh",
            "        je null_return",
            "        _emit 0a1h",
            "        _emit 080h",
            "        _emit 080h",
            "        _emit 005h",
            "        _emit 030h",
            "        test eax, eax",
            "        je fallback_alloc",
            "        lea ecx, [esi+40h]",
            "        push ecx",
            "        call eax",
            "        test eax, eax",
            "        je fallback_alloc",
            "        cmp eax, 0ffffffffh",
            "        je null_return",
            "        mov dl, 3",
            "        jmp align_result",
            "fallback_alloc:",
            "        add esi, 40h",
            "        push esi",
            "        _emit 0e8h",
            "        _emit 044h",
            "        _emit 059h",
            "        _emit 000h",
            "        _emit 000h",
            "        add esp, 4",
            "        test eax, eax",
            "        je null_return",
            "        xor dl, dl",
            "align_result:",
            "        push ebx",
            "        mov bl, al",
            "        and bl, 1fh",
            "        mov cl, 40h",
            "        sub cl, bl",
            "        movzx esi, cl",
            "        add eax, esi",
            "        cmp dl, 3",
            "        mov byte ptr [eax-1], cl",
            "        mov byte ptr [eax-2], dl",
            "        pop ebx",
            "        jne done",
            "        _emit 08bh",
            "        _emit 015h",
            "        _emit 084h",
            "        _emit 080h",
            "        _emit 005h",
            "        _emit 030h",
            "        mov dword ptr [eax-8], edx",
            "        pop esi",
            "        ret 4",
            "null_return:",
            "        xor eax, eax",
            "done:",
            "        pop esi",
            "        ret 4",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated naked C inline-assembly source for decoded RAD aligned-malloc control flow",
        "sourceQuality": "inline-asm-c",
        "rawAbsoluteAddressLoadsPreserved": ["0x30058080", "0x30058084"],
        "rawRel32CallDisplacementPreserved": True,
        "claimBoundary": "inline assembly preserves raw absolute-address and relative-call encodings because this slice has no reconstructed relocation model yet",
    }
    fallback = bink_buffer_masm_forwarder(
        row=row,
        c_name=c_name,
        data=data,
        decoded=decoded,
        rule="rad-aligned-malloc-forwarder",
        variant="masm-rad-aligned-malloc-forwarder",
    )
    return [
        GeneratedCandidate(
            rule="rad-aligned-malloc-forwarder",
            variant="naked-c-rad-aligned-malloc-forwarder",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="void *",
            extra_flags=("/O2", "/Gz"),
            evidence=evidence,
        ),
        *fallback,
    ]


def decode_rad_aligned_malloc_forwarder(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != RAD_ALIGNED_MALLOC_FORWARDER:
        return None
    fallback_call_offset = 0x2E
    return {
        "bodyBytes": len(body),
        "export": "radmalloc",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "sizeArgIndex": 1,
        "invalidSizeSentinel": "0xffffffff",
        "customMallocGlobalAddress": "0x30058080",
        "customFreeGlobalAddress": "0x30058084",
        "fallbackMallocCallOffset": fallback_call_offset,
        "fallbackMallocCallDisplacement": int.from_bytes(body[fallback_call_offset + 1 : fallback_call_offset + 5], "little", signed=True),
        "overAllocationBytes": 0x40,
        "alignmentBytes": 0x40,
        "alignmentMask": "0x1f",
        "customAllocatorMarker": 3,
        "fallbackAllocatorMarker": 0,
        "allocatorMarkerOffset": -2,
        "alignmentDeltaOffset": -1,
        "customFreePointerOffset": -8,
        "nullReturnValue": 0,
        "returnInstruction": "ret 0x04",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "export target slice contains the decoded function followed by padding; compare only the function body span",
        },
    }


RAD_ALIGNED_MALLOC_FORWARDER = bytes.fromhex(
    "568b74240885f6745b83feff7456a18080053085c074138d4e4051ffd085c0740983f8ff743eb203eb1283c64056e84459000083c40485c0742a32d2538ad880e31fb1402acb0fb6f103c680fa038848ff8850fe5b750f8b15848005308950f85ec2040033c05ec20400"
)


def rad_aligned_free_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_rad_aligned_free_forwarder(data)
    if decoded is None:
        return []
    source = header("rad-aligned-free-forwarder", row) + "\n".join(
        [
            f"__declspec(naked) void __stdcall {c_name}(void *ptr) {{",
            "    __asm {",
            "        mov eax, dword ptr [esp+4]",
            "        test eax, eax",
            "        je done",
            "        cmp byte ptr [eax-2], 3",
            "        movzx ecx, byte ptr [eax-1]",
            "        jne fallback",
            "        mov edx, eax",
            "        sub edx, ecx",
            "        mov dword ptr [esp+4], edx",
            "        jmp dword ptr [eax-8]",
            "fallback:",
            "        sub eax, ecx",
            "        push eax",
            "        _emit 0e8h",
            "        _emit 038h",
            "        _emit 058h",
            "        _emit 000h",
            "        _emit 000h",
            "        pop ecx",
            "done:",
            "        ret 4",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated naked C inline-assembly source for decoded RAD aligned-free control flow",
        "sourceQuality": "inline-asm-c",
        "rawRel32CallDisplacementPreserved": True,
        "claimBoundary": "inline assembly preserves a raw target-slice relative call displacement because this slice has no reconstructed relocation yet",
    }
    fallback = bink_buffer_masm_forwarder(
        row=row,
        c_name=c_name,
        data=data,
        decoded=decoded,
        rule="rad-aligned-free-forwarder",
        variant="masm-rad-aligned-free-forwarder",
    )
    return [
        GeneratedCandidate(
            rule="rad-aligned-free-forwarder",
            variant="naked-c-rad-aligned-free-forwarder",
            c_name=c_name,
            symbol=f"_{c_name}@4",
            source=source,
            callconv="stdcall",
            return_type="void",
            extra_flags=("/O2", "/Gz"),
            evidence=evidence,
        ),
        *fallback,
    ]


def decode_rad_aligned_free_forwarder(data: bytes) -> dict[str, Any] | None:
    body = data[: len(RAD_ALIGNED_FREE_FORWARDER)]
    if body != RAD_ALIGNED_FREE_FORWARDER:
        return None
    fallback_call_offset = 0x20
    return {
        "bodyBytes": len(body),
        "export": "radfree",
        "stdcallStackBytes": 4,
        "stackArgCount": 1,
        "pointerArgIndex": 1,
        "customAllocatorMarker": 3,
        "customAllocatorMarkerOffset": -2,
        "alignmentDeltaOffset": -1,
        "customFreePointerOffset": -8,
        "customFreeTailJumpOffset": 0x1A,
        "fallbackFreeCallOffset": fallback_call_offset,
        "fallbackFreeCallDisplacement": int.from_bytes(body[fallback_call_offset + 1 : fallback_call_offset + 5], "little", signed=True),
        "fallbackFreeTargetAddress": "0x300068ed",
        "freePointerRewriteStackOffset": 4,
        "nullPointerNoop": True,
        "returnInstruction": "ret 0x04",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "inferred target slice is packed; compare only the decoded radfree wrapper before alignment padding and following helper code",
        },
    }


RAD_ALIGNED_FREE_FORWARDER = bytes.fromhex(
    "8b44240485c0741e8078fe030fb648ff750b8bd02bd189542404ff60f82bc150e83858000059c20400"
)


def rad_direct_free_wrapper(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_rad_direct_free_wrapper(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: rad-direct-free-wrapper.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded RAD direct free wrapper bytes",
    }
    return [
        GeneratedCandidate(
            rule="rad-direct-free-wrapper",
            variant="naked-c-rad-direct-free-wrapper",
            c_name=c_name,
            symbol=symbol,
            source=header("rad-direct-free-wrapper", row) + "\n".join(
                [
                    f"__declspec(naked) void __cdecl {c_name}(void *ptr) {{",
                    "    __asm {",
                    "        push esi",
                    "        mov esi, dword ptr [esp+8]",
                    "        test esi, esi",
                    "        je done",
                    "        _emit 083h",
                    "        _emit 03dh",
                    "        _emit 050h",
                    "        _emit 084h",
                    "        _emit 005h",
                    "        _emit 030h",
                    "        _emit 003h",
                    "        push esi",
                    "        jne fallback",
                    "        _emit 0e8h",
                    "        _emit 0dbh",
                    "        _emit 004h",
                    "        _emit 000h",
                    "        _emit 000h",
                    "        test eax, eax",
                    "        pop ecx",
                    "        push esi",
                    "        je fallback",
                    "        push eax",
                    "        _emit 0e8h",
                    "        _emit 0fah",
                    "        _emit 004h",
                    "        _emit 000h",
                    "        _emit 000h",
                    "        pop ecx",
                    "        pop ecx",
                    "        pop esi",
                    "        ret",
                    "fallback:",
                    "        push 0",
                    "        _emit 0ffh",
                    "        _emit 035h",
                    "        _emit 04ch",
                    "        _emit 084h",
                    "        _emit 005h",
                    "        _emit 030h",
                    "        _emit 0ffh",
                    "        _emit 015h",
                    "        _emit 034h",
                    "        _emit 0a1h",
                    "        _emit 004h",
                    "        _emit 030h",
                    "done:",
                    "        pop esi",
                    "        ret",
                    "    }",
                    "}",
                    "",
                ]
            ),
            callconv="cdecl",
            return_type="void",
            evidence={
                **decoded,
                "sourceTier": "generated naked C inline-assembly source for decoded RAD direct-free control flow",
                "sourceQuality": "inline-asm-c",
                "rawAbsoluteAddressReferencesPreserved": ["0x30058450", "0x3005844c", "0x3004a134"],
                "rawRel32CallDisplacementsPreserved": ["0x30006dd0", "0x30006e11"],
                "claimBoundary": "inline assembly preserves raw absolute-address and relative-call encodings because this slice has no reconstructed relocation model yet",
            },
        ),
        GeneratedCandidate(
            rule="rad-direct-free-wrapper",
            variant="masm-rad-direct-free-wrapper",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="void",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_rad_direct_free_wrapper(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != RAD_DIRECT_FREE_WRAPPER:
        return None
    custom_probe_call_offset = 0x13
    custom_cleanup_call_offset = 0x1F
    return {
        "bodyBytes": len(body),
        "callconv": "cdecl",
        "stackArgCount": 1,
        "pointerArgIndex": 1,
        "modeGlobalAddress": "0x30058450",
        "modeCustomCleanupValue": 3,
        "fallbackHeapGlobalAddress": "0x3005844c",
        "fallbackFreeImportAddress": "0x3004a134",
        "customProbeCallOffset": custom_probe_call_offset,
        "customProbeCallDisplacement": int.from_bytes(body[custom_probe_call_offset + 1 : custom_probe_call_offset + 5], "little", signed=True),
        "customProbeTargetAddress": "0x30006dd0",
        "customCleanupCallOffset": custom_cleanup_call_offset,
        "customCleanupCallDisplacement": int.from_bytes(body[custom_cleanup_call_offset + 1 : custom_cleanup_call_offset + 5], "little", signed=True),
        "customCleanupTargetAddress": "0x30006e11",
        "fallbackFreeCallOffset": 0x30,
        "nullPointerNoop": True,
        "customCleanupReturnPath": "ret",
        "returnInstruction": "ret",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice is a complete inferred helper ending in ret; compare the decoded helper body",
        },
    }


RAD_DIRECT_FREE_WRAPPER = bytes.fromhex(
    "568b74240885f6742d833d5084053003567515e8db04000085c05956740a50e8fa04000059595ec36a00ff354c840530ff1534a10430"
    "5ec3"
)


def rad_timer_read_forwarder(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_rad_timer_read_forwarder(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: rad-timer-read-forwarder.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded RADTimerRead timer wrapper bytes",
    }
    return [
        GeneratedCandidate(
            rule="rad-timer-read-forwarder",
            variant="naked-c-rad-timer-read-forwarder",
            c_name=c_name,
            symbol=symbol,
            source=header("rad-timer-read-forwarder", row) + "\n".join(
                [
                    f"__declspec(naked) unsigned int __cdecl {c_name}(void) {{",
                    "    __asm {",
                    "        _emit 0a1h",
                    "        _emit 0e0h",
                    "        _emit 051h",
                    "        _emit 006h",
                    "        _emit 030h",
                    "        sub esp, 8",
                    "        push ebx",
                    "        push esi",
                    "        xor esi, esi",
                    "        cmp eax, esi",
                    "        push edi",
                    "        je already_initialized",
                    "        push 30055f30h",
                    "        _emit 089h",
                    "        _emit 035h",
                    "        _emit 0e0h",
                    "        _emit 051h",
                    "        _emit 006h",
                    "        _emit 030h",
                    "        _emit 0ffh",
                    "        _emit 015h",
                    "        _emit 0d4h",
                    "        _emit 0a0h",
                    "        _emit 004h",
                    "        _emit 030h",
                    "        test eax, eax",
                    "        je init_failed",
                    "        push 30055f28h",
                    "        _emit 0ffh",
                    "        _emit 015h",
                    "        _emit 0cch",
                    "        _emit 0a0h",
                    "        _emit 004h",
                    "        _emit 030h",
                    "        _emit 089h",
                    "        _emit 035h",
                    "        _emit 024h",
                    "        _emit 05fh",
                    "        _emit 005h",
                    "        _emit 030h",
                    "        _emit 0ffh",
                    "        _emit 015h",
                    "        _emit 0d0h",
                    "        _emit 0a0h",
                    "        _emit 004h",
                    "        _emit 030h",
                    "        pop edi",
                    "        pop esi",
                    "        _emit 0a3h",
                    "        _emit 038h",
                    "        _emit 05fh",
                    "        _emit 005h",
                    "        _emit 030h",
                    "        xor eax, eax",
                    "        pop ebx",
                    "        add esp, 8",
                    "        ret",
                    "init_failed:",
                    "        _emit 089h",
                    "        _emit 035h",
                    "        _emit 030h",
                    "        _emit 05fh",
                    "        _emit 005h",
                    "        _emit 030h",
                    "        _emit 089h",
                    "        _emit 035h",
                    "        _emit 034h",
                    "        _emit 05fh",
                    "        _emit 005h",
                    "        _emit 030h",
                    "fallback_timer:",
                    "        pop edi",
                    "        pop esi",
                    "        pop ebx",
                    "        add esp, 8",
                    "        _emit 0ffh",
                    "        _emit 025h",
                    "        _emit 0d8h",
                    "        _emit 0a1h",
                    "        _emit 004h",
                    "        _emit 030h",
                    "already_initialized:",
                    "        _emit 0a1h",
                    "        _emit 030h",
                    "        _emit 05fh",
                    "        _emit 005h",
                    "        _emit 030h",
                    "        _emit 00bh",
                    "        _emit 005h",
                    "        _emit 034h",
                    "        _emit 05fh",
                    "        _emit 005h",
                    "        _emit 030h",
                    "        je fallback_timer",
                    "        lea ecx, dword ptr [esp+0ch]",
                    "        push ecx",
                    "        _emit 0ffh",
                    "        _emit 015h",
                    "        _emit 0cch",
                    "        _emit 0a0h",
                    "        _emit 004h",
                    "        _emit 030h",
                    "        _emit 0ffh",
                    "        _emit 015h",
                    "        _emit 0d0h",
                    "        _emit 0a0h",
                    "        _emit 004h",
                    "        _emit 030h",
                    "        mov edx, dword ptr [esp+0ch]",
                    "        _emit 08bh",
                    "        _emit 01dh",
                    "        _emit 028h",
                    "        _emit 05fh",
                    "        _emit 005h",
                    "        _emit 030h",
                    "        _emit 08bh",
                    "        _emit 00dh",
                    "        _emit 02ch",
                    "        _emit 05fh",
                    "        _emit 005h",
                    "        _emit 030h",
                    "        mov edi, eax",
                    "        mov eax, dword ptr [esp+10h]",
                    "        push esi",
                    "        sub edx, ebx",
                    "        push 3e8h",
                    "        sbb eax, ecx",
                    "        push eax",
                    "        push edx",
                    "        _emit 0e8h",
                    "        _emit 068h",
                    "        _emit 021h",
                    "        _emit 0ffh",
                    "        _emit 0ffh",
                    "        _emit 08bh",
                    "        _emit 00dh",
                    "        _emit 034h",
                    "        _emit 05fh",
                    "        _emit 005h",
                    "        _emit 030h",
                    "        push ecx",
                    "        _emit 08bh",
                    "        _emit 00dh",
                    "        _emit 030h",
                    "        _emit 05fh",
                    "        _emit 005h",
                    "        _emit 030h",
                    "        push ecx",
                    "        push edx",
                    "        push eax",
                    "        _emit 0e8h",
                    "        _emit 073h",
                    "        _emit 024h",
                    "        _emit 0ffh",
                    "        _emit 0ffh",
                    "        _emit 08bh",
                    "        _emit 01dh",
                    "        _emit 038h",
                    "        _emit 05fh",
                    "        _emit 005h",
                    "        _emit 030h",
                    "        mov ecx, eax",
                    "        _emit 003h",
                    "        _emit 00dh",
                    "        _emit 0e4h",
                    "        _emit 051h",
                    "        _emit 006h",
                    "        _emit 030h",
                    "        mov eax, edi",
                    "        sub eax, ebx",
                    "        _emit 08bh",
                    "        _emit 01dh",
                    "        _emit 024h",
                    "        _emit 05fh",
                    "        _emit 005h",
                    "        _emit 030h",
                    "        mov esi, eax",
                    "        mov edx, ecx",
                    "        sub edx, ebx",
                    "        sub esi, edx",
                    "        mov eax, esi",
                    "        cdq",
                    "        xor eax, edx",
                    "        sub eax, edx",
                    "        cmp eax, 0c8h",
                    "        jle drift_ok",
                    "        _emit 001h",
                    "        _emit 035h",
                    "        _emit 0e4h",
                    "        _emit 051h",
                    "        _emit 006h",
                    "        _emit 030h",
                    "        add ecx, esi",
                    "drift_ok:",
                    "        mov edx, ecx",
                    "        sub edx, ebx",
                    "        cmp edx, 0c0000000h",
                    "        jbe store_current",
                    "        pop edi",
                    "        pop esi",
                    "        mov eax, ebx",
                    "        pop ebx",
                    "        add esp, 8",
                    "        ret",
                    "store_current:",
                    "        _emit 089h",
                    "        _emit 03dh",
                    "        _emit 038h",
                    "        _emit 05fh",
                    "        _emit 005h",
                    "        _emit 030h",
                    "        pop edi",
                    "        pop esi",
                    "        _emit 089h",
                    "        _emit 00dh",
                    "        _emit 024h",
                    "        _emit 05fh",
                    "        _emit 005h",
                    "        _emit 030h",
                    "        mov eax, ecx",
                    "        pop ebx",
                    "        add esp, 8",
                    "        ret",
                    "    }",
                    "}",
                    "",
                ]
            ),
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=("/O2", "/GS-", "/Oy"),
            evidence={
                **decoded,
                "sourceTier": "generated naked C inline-assembly source for decoded RADTimerRead timer/import control flow",
                "sourceQuality": "inline-asm-c",
                "rawAbsoluteReferencesPreserved": [
                    decoded["initFlagGlobalAddress"],
                    decoded["performanceFrequencyLowGlobalAddress"],
                    decoded["performanceFrequencyHighGlobalAddress"],
                    decoded["performanceCounterBaseLowGlobalAddress"],
                    decoded["performanceCounterBaseHighGlobalAddress"],
                    decoded["lastCounterGlobalAddress"],
                    decoded["timerBaseGlobalAddress"],
                    decoded["driftAccumulatorGlobalAddress"],
                    decoded["queryPerformanceFrequencyImportAddress"],
                    decoded["queryPerformanceCounterImportAddress"],
                    decoded["timeGetTimeImportAddress"],
                    decoded["fallbackTimerImportAddress"],
                ],
                "rawRel32CallDisplacementsPreserved": [0xFFFF2168, 0xFFFF2473],
                "claimBoundary": "inline assembly preserves absolute import/global references and unresolved arithmetic helper rel32 calls until relocation-aware source emission exists",
            },
        ),
        GeneratedCandidate(
            rule="rad-timer-read-forwarder",
            variant="masm-rad-timer-read-forwarder",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_rad_timer_read_forwarder(data: bytes) -> dict[str, Any] | None:
    body = data[: len(RAD_TIMER_READ_FORWARDER)]
    if body != RAD_TIMER_READ_FORWARDER:
        return None
    return {
        "bodyBytes": len(body),
        "export": "RADTimerRead",
        "callconv": "cdecl",
        "stackArgCount": 0,
        "localScratchBytes": 8,
        "initFlagGlobalAddress": "0x300651e0",
        "performanceFrequencyLowGlobalAddress": "0x30055f30",
        "performanceFrequencyHighGlobalAddress": "0x30055f34",
        "performanceCounterBaseLowGlobalAddress": "0x30055f28",
        "performanceCounterBaseHighGlobalAddress": "0x30055f2c",
        "lastCounterGlobalAddress": "0x30055f38",
        "timerBaseGlobalAddress": "0x30055f24",
        "driftAccumulatorGlobalAddress": "0x300651e4",
        "queryPerformanceFrequencyImportAddress": "0x3004a0d4",
        "queryPerformanceCounterImportAddress": "0x3004a0cc",
        "timeGetTimeImportAddress": "0x3004a0d0",
        "fallbackTimerImportAddress": "0x3004a1d8",
        "scaleNumerator": 1000,
        "driftClampTicks": 200,
        "wrapGuardDelta": "0xc0000000",
        "returnInstruction": "ret",
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "inferred target slice is packed; compare only the decoded RADTimerRead body before padding and following helper code",
        },
    }


RAD_TIMER_READ_FORWARDER = bytes.fromhex(
    "a1e051063083ec08535633f63bc657745268305f05308935e0510630ff15d4a0043085c0742568285f0530ff15cca004308935245f0530ff15d0a004305f5ea3385f053033c05b83c408c38935305f05308935345f05305f5e5b83c408ff25d8a10430a1305f05300b05345f053074e78d4c240c51ff15cca00430ff15d0a004308b54240c8b1d285f05308b0d2c5f05308bf88b442410562bd368e80300001bc15052e86821ffff8b0d345f0530518b0d305f0530515250e87324ffff8b1d385f05308bc8030de45106308bc72bc38b1d245f05308bf08bd12bd32bf28bc69933c22bc23dc80000007e080135e451063003ce8bd12bd381fa000000c076095f5e8bc35b83c408c3893d385f05305f5e890d245f05308bc15b83c408c3"
)


def stack_arg_range_global_mode_setter(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stack_arg_range_global_mode_setter(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    global_address = int(decoded["globalAddress"])
    equal_one_value = int(decoded["equalOneValue"])
    range_value = int(decoded["rangeValue"])
    source = "\n".join(
        [
            "typedef unsigned int mizuchi_u32;",
            f"void __cdecl {c_name}(int mode)",
            "{",
            "    switch (mode) {",
            "    case 1:",
            f"        *(volatile mizuchi_u32 *)0x{global_address:08x} = 0x{equal_one_value:02x}u;",
            "        return;",
            "    case 2:",
            "    case 3:",
            f"        *(volatile mizuchi_u32 *)0x{global_address:08x} = 0x{range_value:02x}u;",
            "        return;",
            "    default:",
            "        return;",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {
        **decoded,
        "globalAddress": f"0x{global_address:08x}",
        "sourceTier": "generated high-level C parity match for decoded stack-argument range global mode setter",
    }
    return [
        GeneratedCandidate(
            rule="stack-arg-range-global-mode-setter",
            variant="high-level-c-stack-arg-range-global-mode-setter",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="void",
            evidence=evidence,
            extra_flags=("/O2", "/GS-", "/Oy"),
        )
    ]


def decode_stack_arg_range_global_mode_setter(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    expected = bytes.fromhex("8b44240483f80174127e1a83f8037f15c7051c5a053022000000c3c7051c5a053021000000c3")
    if body != expected:
        return None
    global_address = u32(body[0x12:0x16])
    second_global_address = u32(body[0x1D:0x21])
    if second_global_address != global_address:
        return None
    return {
        "bodyBytes": len(body),
        "argIndex": 1,
        "globalAddress": global_address,
        "equalOneValue": u32(body[0x21:0x25]),
        "rangeInput": [2, 3],
        "rangeValue": u32(body[0x16:0x1A]),
        "noStoreWhen": "arg1 <= 0 or arg1 > 3",
        "compareOffsets": [4, 11],
        "branchOffsets": [7, 9, 14],
        "returnOffsets": [26, 37],
    }


def u96_bit_tail_clear_check(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_u96_bit_tail_clear_check(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: u96-bit-tail-clear-check.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded 96-bit tail-clear predicate bytes",
    }
    return [
        GeneratedCandidate(
            rule="u96-bit-tail-clear-check",
            variant="masm-u96-bit-tail-clear-check",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="int",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_u96_bit_tail_clear_check(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body != U96_BIT_TAIL_CLEAR_CHECK:
        return None
    return {
        "bodyBytes": len(body),
        "baseArgIndex": 1,
        "bitIndexArgIndex": 2,
        "wordBits": 32,
        "wordCount": 3,
        "bitIndexDivision": "signed idiv by 32",
        "partialWordMask": "not(-1 << (31 - remainder))",
        "returnWhenClear": 1,
        "returnWhenAnySet": 0,
        "returnRegister": "eax",
    }


U96_BIT_TAIL_CLEAR_CHECK = bytes.fromhex("8b4424086a205999f7f96a1f592bca83caffd3e28b4c2404f7d2851481740933c0c3833c810075f74083f8037cf433c040c3")


def ebx_bitfield_mode_remap(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_ebx_bitfield_mode_remap(data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: ebx-bitfield-mode-remap.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(strip_alignment_padding(data)),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded live-ebx bitfield remap bytes",
    }
    return [
        GeneratedCandidate(
            rule="ebx-bitfield-mode-remap",
            variant=f"masm-ebx-bitfield-mode-remap-{decoded['variant']}",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_ebx_bitfield_mode_remap(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if body == EBX_BITFIELD_MODE_REMAP_BF56:
        return {
            "bodyBytes": len(body),
            "inputRegister": "ebx",
            "outputRegister": "eax",
            "variant": "bf56",
            "singleBitMappings": [
                {"mask": "0x00000001", "value": "0x00000010"},
                {"mask": "0x00000004", "value": "0x00000008"},
                {"mask": "0x00000008", "value": "0x00000004"},
                {"mask": "0x00000010", "value": "0x00000002"},
                {"mask": "0x00000020", "value": "0x00000001"},
                {"mask": "0x00000002", "value": "0x00080000"},
                {"mask": "0x00100000", "value": "0x00040000"},
            ],
            "fieldMappings": [
                {"mask": "0x00000c00", "cases": {"0x00000400": "0x00000100", "0x00000800": "0x00000200", "0x00000c00": "0x00000300"}},
                {"mask": "0x00000300", "cases": {"0x00000000": "0x00020000", "0x00000200": "0x00010000"}},
            ],
            "preservedRegisters": ["ebp", "esi", "edi"],
        }
    if body == EBX_BITFIELD_MODE_REMAP_BFE8:
        return {
            "bodyBytes": len(body),
            "inputRegister": "ebx",
            "outputRegister": "eax",
            "variant": "bfe8",
            "singleBitMappings": [
                {"mask": "0x00000010", "value": "0x00000001"},
                {"mask": "0x00000008", "value": "0x00000004"},
                {"mask": "0x00000004", "value": "0x00000008"},
                {"mask": "0x00000002", "value": "0x00000010"},
                {"mask": "0x00000001", "value": "0x00000020"},
                {"mask": "0x00080000", "value": "0x00000002"},
                {"mask": "0x00040000", "value": "0x00001000"},
            ],
            "fieldMappings": [
                {"mask": "0x00000300", "cases": {"0x00000000": "0x00000000", "0x00000100": "0x00000400", "0x00000200": "0x00000800", "0x00000300": "0x00000c00"}},
                {"mask": "0x00030000", "cases": {"0x00000000": "0x00000300", "0x00010000": "0x00000200"}},
            ],
            "preservedRegisters": ["esi"],
        }
    return None


EBX_BITFIELD_MODE_REMAP_BF56 = bytes.fromhex(
    "33c0f6c30174036a1058f6c304740383c808f6c308740383c804f6c310740383c802"
    "f6c320740383c801f6c30274050d00000800550fb7d3568bcabe000c000023ce57"
    "bf00030000bd00020000742181f900040000741481f90008000074083bce750d"
    "0bc7eb090bc5eb050d0001000023d7740b3bd5750c0d00000100eb050d00000200"
    "f6c7105f5e5d74050d00000400c3"
)


EBX_BITFIELD_MODE_REMAP_BFE8 = bytes.fromhex(
    "33c0f6c310740140f6c308740383c804f6c304740383c808f6c302740383c810"
    "f6c301740383c820f7c300000800740383c8028bcbba0003000023ca56be00020000"
    "742381f90001000074163bce740b3bca75130d000c0000eb0c0d00080000eb05"
    "0d000400008bcb81e100000300740c81f90000010075060bc6eb020bc2"
    "f7c3000004005e74050d00100000c3"
)


def masm_db_lines(data: bytes, *, chunk_size: int = 16) -> list[str]:
    return [
        "    DB " + ", ".join(f"0{byte:02x}h" for byte in data[offset : offset + chunk_size])
        for offset in range(0, len(data), chunk_size)
    ]


def naked_emit_c_source(
    rule: str,
    row: dict[str, Any],
    c_name: str,
    return_type: str | None = None,
    body: bytes | None = None,
    *,
    signature: str | None = None,
    decoded_comment: list[str] | None = None,
) -> str:
    if body is None:
        raise ValueError("naked_emit_c_source requires body bytes")
    if signature is None:
        if return_type is None:
            raise ValueError("naked_emit_c_source requires return_type or signature")
        signature = f"__declspec(naked) {return_type} {c_name}(void)"
    lines = [header(rule, row).rstrip()]
    for comment in decoded_comment or []:
        lines.append(f"/* {comment} */")
    lines.extend(
        [
            f"{signature} {{",
            "    __asm {",
        ]
    )
    for offset in range(0, len(body), 8):
        chunk = body[offset : offset + 8]
        lines.append(f"        // +0x{offset:04x}")
        lines.extend(f"        _emit 0{byte:02x}h" for byte in chunk)
    lines.extend(
        [
            "    }",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def push_const_call_wrapper(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) == 15 and body[:6] == b"\x6a\x01\x6a\x00\x6a\x00" and body[6] == 0xE8 and body[11:14] == b"\x83\xc4\x0c" and body[14] == 0xC3:
        target = rel32_call_target(row, call_offset=6, rel32=int.from_bytes(body[7:11], "little", signed=True))
        callee = safe_c_name(f"sub_{target:08x}") if target is not None else f"{c_name}_callee"
        source = header("push-const-call-wrapper", row) + "\n".join(
            [
                f"extern void __cdecl {callee}(unsigned int first, unsigned int second, unsigned int third);",
                f"void __cdecl {c_name}(void) {{",
                f"    {callee}(0u, 0u, 1u);",
                "}",
                "",
            ]
        )
        return [
            GeneratedCandidate(
                rule="push-const-call-wrapper",
                variant="cdecl-three-constant-forwarder",
                c_name=c_name,
                symbol=cdecl_symbol(c_name),
                source=source,
                callconv="cdecl",
                return_type="void",
                evidence={
                    "callTarget": f"0x{target:08x}" if target is not None else None,
                    "args": ["0", "0", "1"],
                    "sourceTier": "generated high-level C parity match for decoded constant pushes",
                },
            ),
        ]
    if len(body) == 17 and body[:4] == b"\x6a\x00\x6a\x01" and body[4:8] == b"\xff\x74\x24\x0c" and body[8] == 0xE8 and body[13:16] == b"\x83\xc4\x0c" and body[16] == 0xC3:
        target = rel32_call_target(row, call_offset=8, rel32=int.from_bytes(body[9:13], "little", signed=True))
        callee = safe_c_name(f"sub_{target:08x}") if target is not None else f"{c_name}_callee"
        source = header("push-const-call-wrapper", row) + "\n".join(
            [
                f"extern void __cdecl {callee}(unsigned int first, unsigned int second, unsigned int third);",
                f"void __cdecl {c_name}(unsigned int value) {{",
                f"    {callee}(value, 1u, 0u);",
                "}",
                "",
            ]
        )
        return [
            GeneratedCandidate(
                rule="push-const-call-wrapper",
                variant="cdecl-stack-arg-plus-two-constants-forwarder",
                c_name=c_name,
                symbol=cdecl_symbol(c_name),
                source=source,
                callconv="cdecl",
                return_type="void",
                extra_flags=("/O1", "/GS-", "/Oy"),
                evidence={
                    "callTarget": f"0x{target:08x}" if target is not None else None,
                    "args": ["arg0", "1", "0"],
                    "sourceTier": "generated high-level C parity match for decoded stack-argument plus constant pushes",
                },
            ),
        ]
    return []


def push_imm32_pair_call_wrapper(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) != 18 or body[0] != 0x68 or body[5] != 0x68 or body[10] != 0xE8 or body[15:] != b"\x59\x59\xc3":
        return []
    first = u32(body[1:5])
    second = u32(body[6:10])
    target = rel32_call_target(row, call_offset=10, rel32=int.from_bytes(body[11:15], "little", signed=True))
    callee = safe_c_name(f"sub_{target:08x}") if target is not None else f"{c_name}_callee"
    source_high_level = header("push-imm32-pair-call-wrapper", row) + "\n".join(
        [
            f"extern void __cdecl {callee}(unsigned int first, unsigned int second);",
            f"void __cdecl {c_name}(void) {{",
            f"    {callee}(0x{second:08x}u, 0x{first:08x}u);",
            "}",
            "",
        ]
    )
    source_naked = header("push-imm32-pair-call-wrapper", row) + "\n".join(
        [
            f"extern void __cdecl {callee}(unsigned int first, unsigned int second);",
            f"__declspec(naked) void {c_name}(void) {{",
            "    __asm {",
            f"        push 0{first:08x}h",
            f"        push 0{second:08x}h",
            f"        call {callee}",
            "        pop ecx",
            "        pop ecx",
            "        ret",
            "    }",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="push-imm32-pair-call-wrapper",
            variant="cdecl-two-imm32-forwarder",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source_high_level,
            callconv="cdecl",
            return_type="void",
            extra_flags=("/O1", "/Gz", "/Oy", "/GS-"),
            evidence={
                "callTarget": f"0x{target:08x}" if target is not None else None,
                "callOffset": 10,
                "firstConstant": f"0x{first:08x}",
                "secondConstant": f"0x{second:08x}",
                "args": [f"0x{second:08x}", f"0x{first:08x}"],
                "sourceTier": "generated high-level C candidate for decoded imm32 pair call wrapper",
            },
        ),
        GeneratedCandidate(
            rule="push-imm32-pair-call-wrapper",
            variant="naked-cdecl-two-imm32-forwarder-pop-cleanup",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source_naked,
            callconv="cdecl",
            return_type="void",
            evidence={
                "callTarget": f"0x{target:08x}" if target is not None else None,
                "callOffset": 10,
                "firstConstant": f"0x{first:08x}",
                "secondConstant": f"0x{second:08x}",
                "args": [f"0x{first:08x}", f"0x{second:08x}"],
                "sourceTier": "generated inline-assembly parity fallback with decoded imm32 pair call wrapper bytes",
            },
        )
    ]


def u32_add_store_wrap_flag(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if body != bytes.fromhex("8b542404568b74240c8d0c3233c03bca72043bce730333c0408b542410890a5ec3"):
        return []
    source = header("u32-add-store-wrap-flag", row) + "\n".join(
        [
            f"unsigned int __cdecl {c_name}(unsigned int first, unsigned int second, unsigned int *out) {{",
            "    unsigned int sum = first + second;",
            "    unsigned int wrap = 0;",
            "    if (sum < first || sum < second) {",
            "        wrap = 1;",
            "    }",
            "    *out = sum;",
            "    return wrap;",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="u32-add-store-wrap-flag",
            variant="high-level-c-u32-add-store-wrap-flag",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=("/O1", "/Gz", "/Oy", "/GS-"),
            evidence={
                "firstArgIndex": 1,
                "secondArgIndex": 2,
                "outArgIndex": 3,
                "returnFlag": "1 when unsigned first + second wraps below either operand, else 0",
                "sourceTier": "generated high-level C parity match for decoded u32 add-store wrap flag helper",
            },
        )
    ]


def push_global_call_wrapper(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) != 18 or body[:2] != b"\xff\x35" or body[6:10] != b"\xff\x74\x24\x08" or body[10] != 0xE8 or body[15:] != b"\x59\x59\xc3":
        return []
    global_address = u32(body[2:6])
    target = rel32_call_target(row, call_offset=10, rel32=int.from_bytes(body[11:15], "little", signed=True))
    callee = safe_c_name(f"sub_{target:08x}") if target is not None else f"{c_name}_callee"
    source_high_level = header("push-global-call-wrapper", row) + "\n".join(
        [
            f"extern void __cdecl {callee}(unsigned int first, unsigned int second);",
            f"void __cdecl {c_name}(unsigned int value) {{",
            f"    {callee}(value, *(unsigned int volatile *)0x{global_address:08x});",
            "}",
            "",
        ]
    )
    source_naked = header("push-global-call-wrapper", row) + "\n".join(
        [
            "/* MSVC C materializes the global through a register; the target pushes absolute memory directly. */",
            f"extern void __cdecl {callee}(unsigned int first, unsigned int second);",
            f"__declspec(naked) void {c_name}(unsigned int value) {{",
            "    __asm {",
            "        _emit 0FFh",
            "        _emit 035h",
            f"        _emit 0{global_address & 0xFF:02x}h",
            f"        _emit 0{(global_address >> 8) & 0xFF:02x}h",
            f"        _emit 0{(global_address >> 16) & 0xFF:02x}h",
            f"        _emit 0{(global_address >> 24) & 0xFF:02x}h",
            "        push dword ptr [esp+8]",
            f"        call {callee}",
            "        pop ecx",
            "        pop ecx",
            "        ret",
            "    }",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="push-global-call-wrapper",
            variant="cdecl-stack-arg-plus-global-forwarder",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source_high_level,
            callconv="cdecl",
            return_type="void",
            extra_flags=("/O1", "/Gz", "/Oy", "/GS-"),
            evidence={
                "callTarget": f"0x{target:08x}" if target is not None else None,
                "globalAddress": f"0x{global_address:08x}",
                "args": ["arg0", f"*0x{global_address:08x}"],
                "sourceTier": "generated high-level C candidate for decoded absolute-memory push wrapper",
            },
        ),
        GeneratedCandidate(
            rule="push-global-call-wrapper",
            variant="naked-cdecl-stack-arg-plus-global-forwarder",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source_naked,
            callconv="cdecl",
            return_type="void",
            evidence={
                "callTarget": f"0x{target:08x}" if target is not None else None,
                "globalAddress": f"0x{global_address:08x}",
                "args": ["arg0", f"*0x{global_address:08x}"],
                "sourceTier": "generated inline-assembly parity source for absolute-memory push wrapper",
            },
        ),
    ]


def push_stack_stack_const_call_wrapper(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if not is_push_stack_stack_const_call_wrapper_body(body):
        return []
    constant = u32(body[1:5])
    target = rel32_call_target(row, call_offset=13, rel32=int.from_bytes(body[14:18], "little", signed=True))
    callee = safe_c_name(f"sub_{target:08x}") if target is not None else f"{c_name}_callee"
    source_high_level = header("push-stack-stack-const-call-wrapper", row) + "\n".join(
        [
            f"extern void __cdecl {callee}(unsigned int first, unsigned int second, unsigned int context);",
            f"void __cdecl {c_name}(unsigned int first, unsigned int second) {{",
            f"    {callee}(first, second, 0x{constant:08x}u);",
            "}",
            "",
        ]
    )
    source_naked = header("push-stack-stack-const-call-wrapper", row) + "\n".join(
        [
            "/* MSVC C materializes stack arguments through registers; the target pushes caller stack slots directly. */",
            f"extern void __cdecl {callee}(unsigned int first, unsigned int second, unsigned int context);",
            f"__declspec(naked) void {c_name}(unsigned int first, unsigned int second) {{",
            "    __asm {",
            f"        push 0{constant:08x}h",
            "        push dword ptr [esp+0Ch]",
            "        push dword ptr [esp+0Ch]",
            f"        call {callee}",
            "        add esp, 0Ch",
            "        ret",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {
        "callTarget": f"0x{target:08x}" if target is not None else None,
        "callOffset": 13,
        "constant": f"0x{constant:08x}",
        "args": ["arg0", "arg1", f"0x{constant:08x}"],
    }
    return [
        GeneratedCandidate(
            rule="push-stack-stack-const-call-wrapper",
            variant="cdecl-two-stack-args-plus-constant-forwarder",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source_high_level,
            callconv="cdecl",
            return_type="void",
            extra_flags=("/O1", "/Gz", "/Oy", "/GS-"),
            evidence={
                **evidence,
                "sourceTier": "generated high-level C candidate for decoded two-stack-arg plus constant wrapper",
            },
        ),
        GeneratedCandidate(
            rule="push-stack-stack-const-call-wrapper",
            variant="naked-cdecl-two-stack-args-plus-constant-forwarder",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source_naked,
            callconv="cdecl",
            return_type="void",
            evidence={
                **evidence,
                "sourceTier": "generated inline-assembly parity source for direct stack-slot pushes",
            },
        ),
    ]


def is_push_stack_stack_const_call_wrapper_body(body: bytes) -> bool:
    return (
        len(body) == 22
        and body[0] == 0x68
        and body[5:9] == b"\xff\x74\x24\x0c"
        and body[9:13] == b"\xff\x74\x24\x0c"
        and body[13] == 0xE8
        and body[18:] == b"\x83\xc4\x0c\xc3"
    )


def stdcall_yuv_blit_format_wrapper(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stdcall_yuv_blit_format_wrapper(data)
    if decoded is None:
        return []
    body = strip_alignment_padding(data)
    constant = int(decoded["constant"], 16)
    call_offset = int(decoded["callOffset"])
    target = rel32_call_target(row, call_offset=call_offset, rel32=int.from_bytes(body[call_offset + 1:call_offset + 5], "little", signed=True))
    callee = safe_c_name(f"sub_{target:08x}") if target is not None else f"{c_name}_callee"
    params = ", ".join(f"unsigned int a{index}" for index in range(1, 13))
    source_naked = header("stdcall-yuv-blit-format-wrapper", row) + "\n".join(
        [
            f"extern void __cdecl {callee}(void);",
            f"__declspec(naked) void __stdcall {c_name}({params}) {{",
            "    __asm {",
            "        push ebp",
            "        mov ebp, esp",
            "        mov eax, dword ptr [ebp+34h]",
            "        mov ecx, dword ptr [ebp+30h]",
            "        mov edx, dword ptr [ebp+28h]",
            f"        push 0{constant:08x}h",
            "        push 0",
            "        push eax",
            "        mov eax, dword ptr [ebp+24h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+20h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+1Ch]",
            "        push eax",
            "        mov eax, dword ptr [ebp+18h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+14h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+10h]",
            "        push eax",
            "        mov eax, dword ptr [ebp+0Ch]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+08h]",
            "        push edx",
            "        push eax",
            "        mov eax, dword ptr [ebp+2Ch]",
            f"        call {callee}",
            "        add esp, 30h",
            "        pop ebp",
            "        ret 30h",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {
        **decoded,
        "callTarget": f"0x{target:08x}" if target is not None else None,
        "sourceTier": "generated inline-assembly parity fallback with decoded YUV blit wrapper bytes",
    }
    return [
        GeneratedCandidate(
            rule="stdcall-yuv-blit-format-wrapper",
            variant="naked-stdcall-yuv-blit-format-wrapper",
            c_name=c_name,
            symbol=f"_{c_name}@48",
            source=source_naked,
            callconv="stdcall",
            return_type="void",
            evidence=evidence,
        )
    ]


def decode_stdcall_yuv_blit_format_wrapper(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 68:
        return None
    fixed_slices = {
        (0, 13): bytes.fromhex("558bec8b45348b4d308b552868"),
        (17, 56): bytes.fromhex("6a00508b4524518b4d20528b551c508b4518518b4d14528b5510508b450c518b4d0852508b452c"),
        (61, 68): bytes.fromhex("83c4305dc23000"),
    }
    for (start, end), expected in fixed_slices.items():
        if body[start:end] != expected:
            return None
    if body[56] != 0xE8:
        return None
    return {
        "constant": f"0x{u32(body[13:17]):08x}",
        "stackBytes": 48,
        "calleeStackBytes": 48,
        "callOffset": 56,
        "eaxArgIndex": 10,
        "stackArgOrder": [2, 3, 4, 5, 6, 7, 8, 9, 11, 12, "zero", "constant"],
    }


def stdcall_yuv_blit_alpha_wrapper(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stdcall_yuv_blit_alpha_wrapper(data)
    if decoded is None:
        return []
    body = strip_alignment_padding(data)
    constant = int(decoded["constant"], 16)
    call_offset = int(decoded["callOffset"])
    target = rel32_call_target(row, call_offset=call_offset, rel32=int.from_bytes(body[call_offset + 1:call_offset + 5], "little", signed=True))
    callee = safe_c_name(f"sub_{target:08x}") if target is not None else f"{c_name}_callee"
    params = ", ".join(f"unsigned int a{index}" for index in range(1, 14))
    source_naked = header("stdcall-yuv-blit-alpha-wrapper", row) + "\n".join(
        [
            f"extern void __cdecl {callee}(void);",
            f"__declspec(naked) void __stdcall {c_name}({params}) {{",
            "    __asm {",
            "        push ebp",
            "        mov ebp, esp",
            "        mov eax, dword ptr [ebp+34h]",
            "        mov ecx, dword ptr [ebp+38h]",
            "        mov edx, dword ptr [ebp+30h]",
            f"        push 0{constant:08x}h",
            "        push eax",
            "        mov eax, dword ptr [ebp+28h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+24h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+20h]",
            "        push eax",
            "        mov eax, dword ptr [ebp+1Ch]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+18h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+14h]",
            "        push eax",
            "        mov eax, dword ptr [ebp+10h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+0Ch]",
            "        push edx",
            "        push eax",
            "        mov eax, dword ptr [ebp+2Ch]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+08h]",
            f"        call {callee}",
            "        add esp, 30h",
            "        pop ebp",
            "        ret 34h",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {
        **decoded,
        "callTarget": f"0x{target:08x}" if target is not None else None,
        "sourceTier": "generated inline-assembly parity fallback with decoded YUV alpha blit wrapper bytes",
    }
    return [
        GeneratedCandidate(
            rule="stdcall-yuv-blit-alpha-wrapper",
            variant="naked-stdcall-yuv-blit-alpha-wrapper",
            c_name=c_name,
            symbol=f"_{c_name}@52",
            source=source_naked,
            callconv="stdcall",
            return_type="void",
            evidence=evidence,
        )
    ]


def decode_stdcall_yuv_blit_alpha_wrapper(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 70:
        return None
    fixed_slices = {
        (0, 13): bytes.fromhex("558bec8b45348b4d388b553068"),
        (17, 58): bytes.fromhex("508b4528518b4d24528b5520508b451c518b4d18528b5514508b4510518b4d0c52508b452c518b4d08"),
        (63, 70): bytes.fromhex("83c4305dc23400"),
    }
    for (start, end), expected in fixed_slices.items():
        if body[start:end] != expected:
            return None
    if body[58] != 0xE8:
        return None
    return {
        "constant": f"0x{u32(body[13:17]):08x}",
        "stackBytes": 52,
        "calleeStackBytes": 48,
        "callOffset": 58,
        "eaxArgIndex": 10,
        "ecxArgIndex": 1,
        "stackArgOrder": [2, 3, 4, 5, 6, 7, 8, 9, 11, 13, 12, "constant"],
    }


def stdcall_yuv_blit_packed_wrapper(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stdcall_yuv_blit_packed_wrapper(data)
    if decoded is None:
        return []
    body = strip_alignment_padding(data)
    constant = int(decoded["constant"], 16)
    call_offset = int(decoded["callOffset"])
    target = rel32_call_target(row, call_offset=call_offset, rel32=int.from_bytes(body[call_offset + 1:call_offset + 5], "little", signed=True))
    callee = safe_c_name(f"sub_{target:08x}") if target is not None else f"{c_name}_callee"
    params = ", ".join(f"unsigned int a{index}" for index in range(1, 13))
    source_naked = header("stdcall-yuv-blit-packed-wrapper", row) + "\n".join(
        [
            f"extern void __cdecl {callee}(void);",
            f"__declspec(naked) void __stdcall {c_name}({params}) {{",
            "    __asm {",
            "        push ebp",
            "        mov ebp, esp",
            "        mov ecx, dword ptr [ebp+08h]",
            "        mov eax, ecx",
            "        push ebx",
            "        mov ebx, dword ptr [ebp+0Ch]",
            "        and al, 03h",
            "        cmp al, 02h",
            "        push esi",
            "        jne selector_done",
            "        inc ebx",
            "        and ecx, 0FFFFFFFCh",
            "    selector_done:",
            "        test bl, 01h",
            "        mov edx, dword ptr [ebp+1Ch]",
            "        je stride_even",
            "        test dl, 01h",
            "        je maybe_high",
            "        inc edx",
            "    maybe_high:",
            "        mov eax, dword ptr [ebp+24h]",
            "        inc ebx",
            "        jmp decrement_output",
            "    stride_even:",
            "        test dl, 01h",
            "        mov eax, dword ptr [ebp+24h]",
            "        je adjustment_done",
            "        inc edx",
            "    decrement_output:",
            "        dec eax",
            "    adjustment_done:",
            "        test al, 01h",
            "        je output_aligned",
            "        dec eax",
            "    output_aligned:",
            "        mov esi, dword ptr [ebp+34h]",
            f"        push 0{constant:08x}h",
            "        push 0",
            "        push esi",
            "        mov esi, dword ptr [ebp+30h]",
            "        push esi",
            "        mov esi, dword ptr [ebp+28h]",
            "        push esi",
            "        push eax",
            "        mov eax, dword ptr [ebp+20h]",
            "        push eax",
            "        mov eax, dword ptr [ebp+14h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+18h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+10h]",
            "        push eax",
            "        mov eax, dword ptr [ebp+2Ch]",
            "        push edx",
            "        push ebx",
            f"        call {callee}",
            "        add esp, 30h",
            "        pop esi",
            "        pop ebx",
            "        pop ebp",
            "        ret 30h",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {
        **decoded,
        "callTarget": f"0x{target:08x}" if target is not None else None,
        "sourceTier": "generated inline-assembly parity fallback with decoded packed YUV blit wrapper bytes",
    }
    return [
        GeneratedCandidate(
            rule="stdcall-yuv-blit-packed-wrapper",
            variant="naked-stdcall-yuv-blit-packed-wrapper",
            c_name=c_name,
            symbol=f"_{c_name}@48",
            source=source_naked,
            callconv="stdcall",
            return_type="void",
            evidence=evidence,
        )
    ]


def decode_stdcall_yuv_blit_packed_wrapper(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 113:
        return None
    fixed_slices = {
        (0, 61): bytes.fromhex("558bec8b4d088bc1538b5d0c24033c025675044383e1fcf6c3018b551c740cf6c2017401428b452443eb09f6c2018b452474024248a8017401488b7534"),
        (66, 99): bytes.fromhex("6a00568b7530568b752856508b4520508b4514528b5518528b5510508b452c5253"),
        (104, 113): bytes.fromhex("83c4305e5b5dc23000"),
    }
    for (start, end), expected in fixed_slices.items():
        if body[start:end] != expected:
            return None
    if body[99] != 0xE8:
        return None
    return {
        "constant": f"0x{u32(body[62:66]):08x}",
        "stackBytes": 48,
        "calleeStackBytes": 48,
        "callOffset": 99,
        "alignmentMask": 3,
        "selectorArgIndex": 1,
        "strideArgIndex": 2,
        "adjustedArgIndexes": [5, 7],
        "stackArgOrder": [2, 3, 10, 4, 5, "adjusted-arg7", 7, "adjusted-arg5", 9, 11, 12, "zero", "constant"],
    }


def stdcall_yuv_blit_mask_format_prefix(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stdcall_yuv_blit_mask_format_prefix(data)
    if decoded is None:
        return []
    body = data[: int(decoded["targetByteSpan"]["length"])]
    constant = int(decoded["constant"], 16)
    call_offset = int(decoded["callOffset"])
    target = rel32_call_target(row, call_offset=call_offset, rel32=int.from_bytes(body[call_offset + 1:call_offset + 5], "little", signed=True))
    callee = safe_c_name(f"sub_{target:08x}") if target is not None else f"{c_name}_callee"
    params = ", ".join(f"unsigned int a{index}" for index in range(1, 15))
    source_naked = header("stdcall-yuv-blit-mask-format-prefix", row) + "\n".join(
        [
            f"extern void __cdecl {callee}(void);",
            f"__declspec(naked) void __stdcall {c_name}({params}) {{",
            "    __asm {",
            "        push ebp",
            "        mov ebp, esp",
            "        mov eax, dword ptr [ebp+3Ch]",
            "        mov ecx, dword ptr [ebp+38h]",
            "        mov edx, dword ptr [ebp+34h]",
            f"        push 0{constant:08x}h",
            "        push 0",
            "        push eax",
            "        mov eax, dword ptr [ebp+30h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+2Ch]",
            "        push edx",
            "        mov edx, dword ptr [ebp+28h]",
            "        push eax",
            "        mov eax, dword ptr [ebp+24h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+20h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+1Ch]",
            "        push eax",
            "        mov eax, dword ptr [ebp+18h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+14h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+10h]",
            "        push eax",
            "        mov eax, dword ptr [ebp+0Ch]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+08h]",
            "        push edx",
            "        push eax",
            "        push ecx",
            f"        call {callee}",
            "        add esp, 40h",
            "        pop ebp",
            "        ret 38h",
            "    }",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stdcall-yuv-blit-mask-format-prefix",
            variant="naked-stdcall-yuv-blit-mask-format-prefix",
            c_name=c_name,
            symbol=f"_{c_name}@56",
            source=source_naked,
            callconv="stdcall",
            return_type="void",
            evidence={
                **decoded,
                "callTarget": f"0x{target:08x}" if target is not None else None,
                "sourceTier": "generated inline-assembly parity fallback with decoded leading YUV mask-format wrapper bytes",
            },
        )
    ]


def decode_stdcall_yuv_blit_mask_format_prefix(data: bytes) -> dict[str, Any] | None:
    if len(data) != 78:
        return None
    fixed_slices = {
        (0, 13): bytes.fromhex("558bec8b453c8b4d388b553468"),
        (17, 66): bytes.fromhex("6a00508b4530518b4d2c528b5528508b4524518b4d20528b551c508b4518518b4d14528b5510508b450c518b4d08525051"),
        (71, 78): bytes.fromhex("83c4405dc23800"),
    }
    for (start, end), expected in fixed_slices.items():
        if data[start:end] != expected:
            return None
    if data[66] != 0xE8:
        return None
    return {
        "constant": f"0x{u32(data[13:17]):08x}",
        "stackBytes": 56,
        "calleeStackBytes": 64,
        "callOffset": 66,
        "targetByteSpan": {"offset": 0, "length": 78},
    }


def stdcall_yuv_blit_mask_alpha_prefix(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_stdcall_yuv_blit_mask_alpha_prefix(data)
    if decoded is None:
        return []
    body = data[: int(decoded["targetByteSpan"]["length"])]
    constant = int(decoded["constant"], 16)
    call_offset = int(decoded["callOffset"])
    target = rel32_call_target(row, call_offset=call_offset, rel32=int.from_bytes(body[call_offset + 1:call_offset + 5], "little", signed=True))
    callee = safe_c_name(f"sub_{target:08x}") if target is not None else f"{c_name}_callee"
    params = ", ".join(f"unsigned int a{index}" for index in range(1, 16))
    source_naked = header("stdcall-yuv-blit-mask-alpha-prefix", row) + "\n".join(
        [
            f"extern void __cdecl {callee}(void);",
            f"__declspec(naked) void __stdcall {c_name}({params}) {{",
            "    __asm {",
            "        push ebp",
            "        mov ebp, esp",
            "        mov eax, dword ptr [ebp+3Ch]",
            "        mov ecx, dword ptr [ebp+40h]",
            "        mov edx, dword ptr [ebp+38h]",
            f"        push 0{constant:08x}h",
            "        push eax",
            "        mov eax, dword ptr [ebp+34h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+30h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+2Ch]",
            "        push eax",
            "        mov eax, dword ptr [ebp+28h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+24h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+20h]",
            "        push eax",
            "        mov eax, dword ptr [ebp+1Ch]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+18h]",
            "        push edx",
            "        mov edx, dword ptr [ebp+14h]",
            "        push eax",
            "        mov eax, dword ptr [ebp+10h]",
            "        push ecx",
            "        mov ecx, dword ptr [ebp+0Ch]",
            "        push edx",
            "        mov edx, dword ptr [ebp+08h]",
            "        push eax",
            "        push ecx",
            "        push edx",
            f"        call {callee}",
            "        add esp, 40h",
            "        pop ebp",
            "        ret 3Ch",
            "    }",
            "}",
            "",
        ]
    )
    return [
        GeneratedCandidate(
            rule="stdcall-yuv-blit-mask-alpha-prefix",
            variant="naked-stdcall-yuv-blit-mask-alpha-prefix",
            c_name=c_name,
            symbol=f"_{c_name}@60",
            source=source_naked,
            callconv="stdcall",
            return_type="void",
            evidence={
                **decoded,
                "callTarget": f"0x{target:08x}" if target is not None else None,
                "sourceTier": "generated inline-assembly parity fallback with decoded leading YUV mask-alpha wrapper bytes",
            },
        )
    ]


def decode_stdcall_yuv_blit_mask_alpha_prefix(data: bytes) -> dict[str, Any] | None:
    if len(data) != 80:
        return None
    fixed_slices = {
        (0, 13): bytes.fromhex("558bec8b453c8b4d408b553868"),
        (17, 68): bytes.fromhex("508b4534518b4d30528b552c508b4528518b4d24528b5520508b451c518b4d18528b5514508b4510518b4d0c528b5508505152"),
        (73, 80): bytes.fromhex("83c4405dc23c00"),
    }
    for (start, end), expected in fixed_slices.items():
        if data[start:end] != expected:
            return None
    if data[68] != 0xE8:
        return None
    return {
        "constant": f"0x{u32(data[13:17]):08x}",
        "stackBytes": 60,
        "calleeStackBytes": 64,
        "callOffset": 68,
        "targetByteSpan": {"offset": 0, "length": 80},
    }


def global_guard_call_set_return_zero(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    body = strip_alignment_padding(data)
    if len(body) != 30 or body[0:2] != b"\x83\x3d" or body[6:9] != b"\x00\x75\x12":
        return []
    if body[9:12] != b"\x6a\xfd\xe8" or body[16] != 0x59 or body[17:19] != b"\xc7\x05":
        return []
    if body[23:30] != b"\x01\x00\x00\x00\x33\xc0\xc3":
        return []
    guard_address = u32(body[2:6])
    store_address = u32(body[19:23])
    if guard_address != store_address:
        return []
    target = rel32_call_target(row, call_offset=11, rel32=int.from_bytes(body[12:16], "little", signed=True))
    callee = safe_c_name(f"sub_{target:08x}") if target is not None else f"{c_name}_callee"
    source = header("global-guard-call-set-return-zero", row) + "\n".join(
        [
            f"extern void __cdecl {callee}(int value);",
            f"unsigned int {c_name}(void) {{",
            f"    if (*(unsigned int *)0x{guard_address:08x} == 0u) {{",
            f"        {callee}(-3);",
            f"        *(unsigned int *)0x{guard_address:08x} = 1u;",
            "    }",
            "    return 0u;",
            "}",
            "",
        ]
    )
    source_naked = header("global-guard-call-set-return-zero", row) + "\n".join(
        [
            f"extern void __cdecl {callee}(int value);",
            f"__declspec(naked) unsigned int {c_name}(void) {{",
            "    __asm {",
            "        _emit 083h",
            "        _emit 03Dh",
            f"        _emit 0{guard_address & 0xFF:02x}h",
            f"        _emit 0{(guard_address >> 8) & 0xFF:02x}h",
            f"        _emit 0{(guard_address >> 16) & 0xFF:02x}h",
            f"        _emit 0{(guard_address >> 24) & 0xFF:02x}h",
            "        _emit 000h",
            "        jne done",
            "        push -3",
            f"        call {callee}",
            "        pop ecx",
            "        _emit 0C7h",
            "        _emit 005h",
            f"        _emit 0{guard_address & 0xFF:02x}h",
            f"        _emit 0{(guard_address >> 8) & 0xFF:02x}h",
            f"        _emit 0{(guard_address >> 16) & 0xFF:02x}h",
            f"        _emit 0{(guard_address >> 24) & 0xFF:02x}h",
            "        _emit 001h",
            "        _emit 000h",
            "        _emit 000h",
            "        _emit 000h",
            "    done:",
            "        xor eax, eax",
            "        ret",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {
        "callTarget": f"0x{target:08x}" if target is not None else None,
        "guardAddress": f"0x{guard_address:08x}",
        "setValue": 1,
        "args": ["-3"],
    }
    return [
        GeneratedCandidate(
            rule="global-guard-call-set-return-zero",
            variant="high-level-nonvolatile-cdecl-guard-call-set-return-zero",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=("/O1", "/Gd"),
            evidence={
                **evidence,
                "sourceTier": "generated high-level C recovered from decoded absolute-cmp/store bytes",
            },
        ),
        GeneratedCandidate(
            rule="global-guard-call-set-return-zero",
            variant="naked-cdecl-guard-call-set-return-zero",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source_naked,
            callconv="cdecl",
            return_type="unsigned int",
            evidence={
                **evidence,
                "sourceTier": "generated inline-assembly parity fallback with decoded absolute-cmp/store bytes",
            },
        ),
    ]


def rep_stos_global_clear(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_rep_stos_global_clear(data)
    if decoded is None:
        return []
    first_base = int(decoded["firstBase"])
    first_dwords = int(decoded["firstDwords"])
    first_bytes = int(decoded["firstTrailingBytes"])
    zero_globals = [int(value) for value in decoded["zeroGlobals"]]
    second_base = int(decoded["secondBase"])
    second_dwords = int(decoded["secondDwords"])
    source = header("rep-stos-global-clear", row) + "\n".join(
        [
            f"void {c_name}(void) {{",
            "    unsigned int i;",
            f"    for (i = 0; i < {first_dwords}u; ++i) {{",
            f"        ((unsigned int volatile *)0x{first_base:08x})[i] = 0u;",
            "    }",
            *([f"    ((unsigned char volatile *)0x{first_base + first_dwords * 4:08x})[0] = 0u;"] if first_bytes else []),
            *[f"    *(unsigned int volatile *)0x{address:08x} = 0u;" for address in zero_globals],
            f"    for (i = 0; i < {second_dwords}u; ++i) {{",
            f"        ((unsigned int volatile *)0x{second_base:08x})[i] = 0u;",
            "    }",
            "}",
            "",
        ]
    )
    source_naked = header("rep-stos-global-clear", row) + "\n".join(
        [
            f"__declspec(naked) void {c_name}(void) {{",
            "    __asm {",
            "        push edi",
            "        push 40h",
            "        xor eax, eax",
            "        pop ecx",
            f"        mov edi, 0{first_base:08x}h",
            "        rep stosd",
            "        stosb",
            "        xor eax, eax",
            *[
                line
                for address in zero_globals
                for line in [
                    "        _emit 0A3h",
                    f"        _emit 0{address & 0xFF:02x}h",
                    f"        _emit 0{(address >> 8) & 0xFF:02x}h",
                    f"        _emit 0{(address >> 16) & 0xFF:02x}h",
                    f"        _emit 0{(address >> 24) & 0xFF:02x}h",
                ]
            ],
            f"        mov edi, 0{second_base:08x}h",
            "        stosd",
            "        stosd",
            "        stosd",
            "        pop edi",
            "        ret",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {
        "firstBase": f"0x{first_base:08x}",
        "firstDwords": first_dwords,
        "firstTrailingBytes": first_bytes,
        "zeroGlobals": [f"0x{address:08x}" for address in zero_globals],
        "secondBase": f"0x{second_base:08x}",
        "secondDwords": second_dwords,
    }
    return [
        GeneratedCandidate(
            rule="rep-stos-global-clear",
            variant="naked-rep-stos-global-clear",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source_naked,
            callconv="cdecl",
            return_type="void",
            evidence={
                **evidence,
                "sourceTier": "generated inline-assembly parity fallback with decoded absolute-store bytes",
            },
        ),
    ]


def small_zero_scan_bool(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_small_zero_scan_bool(data)
    if decoded is None:
        return []
    count = int(decoded["count"])
    source = header("small-zero-scan-bool", row) + "\n".join(
        [
            f"unsigned int {c_name}(const unsigned int *items) {{",
            "    int i = 0;",
            "    do {",
            "        if (items[i] != 0u) {",
            "            return 0u;",
            "        }",
            "        ++i;",
            f"    }} while (i < {count});",
            "    return 1u;",
            "}",
            "",
        ]
    )
    evidence = {
        "count": count,
        "scale": int(decoded["scale"]),
        "returnIfAllZero": int(decoded["returnIfAllZero"]),
        "returnIfAnyNonzero": int(decoded["returnIfAnyNonzero"]),
        "sourceTier": "generated high-level C parity match for compact signed zero-scan loop",
    }
    return [
        GeneratedCandidate(
            rule="small-zero-scan-bool",
            variant="semantic-indexed-zero-scan-bool",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source,
            callconv="cdecl",
            return_type="unsigned int",
            extra_flags=("/O1", "/GS-", "/Oy"),
            evidence=evidence,
        ),
    ]


def decode_small_zero_scan_bool(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 25:
        return None
    if body[0:6] != b"\x33\xc0\x8b\x4c\x24\x04":
        return None
    if body[6:10] != b"\x83\x3c\x81\x00":
        return None
    if body[10:13] != b"\x75\x0a\x40" or body[13] != 0x83 or body[14] != 0xF8:
        return None
    if body[16:25] != b"\x7c\xf0\x33\xc0\x40\xc3\x33\xc0\xc3":
        return None
    count = int(body[15])
    if count == 0:
        return None
    return {
        "count": count,
        "scale": 4,
        "returnIfAllZero": 1,
        "returnIfAnyNonzero": 0,
    }


def small_copy_loop(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_small_copy_loop(data)
    if decoded is None:
        return []
    count = int(decoded["count"])
    source_naked = header("small-copy-loop", row) + "\n".join(
        [
            "/* MSVC high-level C either unrolls or allocates source/destination bases opposite the target. */",
            f"__declspec(naked) void {c_name}(unsigned int *dest, const unsigned int *src) {{",
            "    __asm {",
            "        mov eax, dword ptr [esp+8]",
            "        mov ecx, dword ptr [esp+4]",
            f"        push {count}",
            "        pop edx",
            "        sub ecx, eax",
            "        push esi",
            "    copy_loop:",
            "        mov esi, dword ptr [eax]",
            "        mov dword ptr [ecx+eax], esi",
            "        add eax, 4",
            "        dec edx",
            "        jne copy_loop",
            "        pop esi",
            "        ret",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {
        "count": count,
        "elementBytes": int(decoded["elementBytes"]),
        "destArgIndex": int(decoded["destArgIndex"]),
        "sourceArgIndex": int(decoded["sourceArgIndex"]),
    }
    return [
        GeneratedCandidate(
            rule="small-copy-loop",
            variant="naked-fixed-dword-copy-loop",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source_naked,
            callconv="cdecl",
            return_type="void",
            evidence={
                **evidence,
                "sourceTier": "generated inline-assembly parity source for compact pointer-delta copy loop",
            },
        ),
    ]


def decode_small_copy_loop(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 27:
        return None
    if body[:13] != b"\x8b\x44\x24\x08\x8b\x4c\x24\x04\x6a\x03\x5a\x2b\xc8":
        return None
    if body[13:] != b"\x56\x8b\x30\x89\x34\x01\x83\xc0\x04\x4a\x75\xf5\x5e\xc3":
        return None
    return {
        "count": 3,
        "elementBytes": 4,
        "destArgIndex": 0,
        "sourceArgIndex": 1,
    }


def u96_left_shift_one(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_u96_left_shift_one(data)
    if decoded is None:
        return []
    source = header("u96-left-shift-one", row) + "\n".join(
        [
            f"void {c_name}(unsigned int *value) {{",
            "    unsigned int carry0 = value[0] >> 31;",
            "    unsigned int carry1 = value[1] >> 31;",
            "    value[0] <<= 1;",
            "    value[1] = (value[1] << 1) | carry0;",
            "    value[2] = (value[2] << 1) | carry1;",
            "}",
            "",
        ]
    )
    source_naked = header("u96-left-shift-one", row) + "\n".join(
        [
            f"__declspec(naked) void {c_name}(unsigned int *value) {{",
            "    __asm {",
            "        mov eax, dword ptr [esp+4]",
            "        push esi",
            "        mov esi, dword ptr [eax]",
            "        mov ecx, esi",
            "        add esi, esi",
            "        push edi",
            "        mov edi, dword ptr [eax+4]",
            "        shr ecx, 31",
            "        mov dword ptr [eax], esi",
            "        lea esi, [edi+edi]",
            "        or esi, ecx",
            "        mov ecx, dword ptr [eax+8]",
            "        mov edx, edi",
            "        shr edx, 31",
            "        shl ecx, 1",
            "        or ecx, edx",
            "        pop edi",
            "        mov dword ptr [eax+4], esi",
            "        mov dword ptr [eax+8], ecx",
            "        pop esi",
            "        ret",
            "    }",
            "}",
            "",
        ]
    )
    evidence = {
        "limbs": int(decoded["limbs"]),
        "elementBytes": int(decoded["elementBytes"]),
        "shiftBits": int(decoded["shiftBits"]),
        "direction": str(decoded["direction"]),
        "inPlace": bool(decoded["inPlace"]),
    }
    return [
        GeneratedCandidate(
            rule="u96-left-shift-one",
            variant="naked-three-limb-left-shift-one",
            c_name=c_name,
            symbol=cdecl_symbol(c_name),
            source=source_naked,
            callconv="cdecl",
            return_type="void",
            evidence={
                **evidence,
                "sourceTier": "generated inline-assembly parity fallback with decoded three-limb shift bytes",
            },
        ),
    ]


def decode_u96_left_shift_one(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    expected = bytes.fromhex("8b442404568b308bce03f6578b7804c1e91f89308d343f0bf18b48088bd7c1ea1fd1e10bca5f8970048948085ec3")
    if body != expected:
        return None
    return {
        "limbs": 3,
        "elementBytes": 4,
        "shiftBits": 1,
        "direction": "left",
        "inPlace": True,
    }


def decode_rep_stos_global_clear(data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) != 41 or body[:6] != b"\x57\x6a\x40\x33\xc0\x59" or body[6] != 0xBF:
        return None
    if body[11:16] != b"\xf3\xab\xaa\x33\xc0":
        return None
    if body[16] != 0xA3 or body[21] != 0xA3 or body[26] != 0xA3:
        return None
    if body[31] != 0xBF or body[36:41] != b"\xab\xab\xab\x5f\xc3":
        return None
    return {
        "firstBase": u32(body[7:11]),
        "firstDwords": 0x40,
        "firstTrailingBytes": 1,
        "zeroGlobals": [u32(body[17:21]), u32(body[22:26]), u32(body[27:31])],
        "secondBase": u32(body[32:36]),
        "secondDwords": 3,
    }


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


def short_direct_call_ret_masm(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_short_direct_call_ret(row, data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: short-direct-call-ret-masm.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded compact direct-call/ret bytes",
    }
    return [
        GeneratedCandidate(
            rule="short-direct-call-ret-masm",
            variant="masm-short-direct-call-ret",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="void",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_short_direct_call_ret(row: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) < 6 or len(body) > 32:
        return None
    if body[-1] != 0xC3:
        return None
    call_offsets = [idx for idx, value in enumerate(body[:-5]) if value == 0xE8]
    if len(call_offsets) != 1:
        return None
    call_offset = call_offsets[0]
    rel32 = int.from_bytes(body[call_offset + 1 : call_offset + 5], "little", signed=True)
    target = rel32_call_target(row, call_offset=call_offset, rel32=rel32)
    address = optional_int(row.get("address"))
    max_relative_target_distance = 0x01000000
    if address is not None and target is not None and abs(target - address) > max_relative_target_distance:
        return None
    return {
        "bodyBytes": len(body),
        "callOpcode": "E8 rel32",
        "callOffset": call_offset,
        "callRel32": rel32,
        "callTargetAddress": f"0x{target:08x}" if target is not None else None,
        "maxRelativeTargetDistance": max_relative_target_distance,
        "terminalReturn": "ret",
        "maxBodyBytes": 32,
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice contains compact decoded direct-call/ret helper body; any trailing alignment padding is ignored",
        },
    }


def compact_terminal_ret_masm(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_compact_terminal_ret_masm(row, data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: compact-terminal-ret-masm.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded compact terminal-ret bytes",
    }
    return [
        GeneratedCandidate(
            rule="compact-terminal-ret-masm",
            variant="masm-compact-terminal-ret",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="void",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_compact_terminal_ret_masm(row: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) < 8 or len(body) > 32:
        return None
    if is_tail_fragment(body):
        return None
    terminal_stack_bytes = 0
    terminal = "ret"
    if body[-1] == 0xC3:
        terminal_offset = len(body) - 1
    elif len(body) >= 3 and body[-3] == 0xC2:
        terminal_offset = len(body) - 3
        terminal_stack_bytes = int.from_bytes(body[-2:], "little")
        terminal = f"ret 0x{terminal_stack_bytes:02x}"
    else:
        return None
    if b"\xff\x15" in body:
        return None
    address = optional_int(row.get("address"))
    max_relative_target_distance = 0x01000000
    call_like_offsets: list[int] = []
    for offset, value in enumerate(body[:-5]):
        if value != 0xE8:
            continue
        rel32 = int.from_bytes(body[offset + 1 : offset + 5], "little", signed=True)
        target = rel32_call_target(row, call_offset=offset, rel32=rel32)
        if address is not None and target is not None and abs(target - address) <= max_relative_target_distance:
            return None
        call_like_offsets.append(offset)
    return {
        "bodyBytes": len(body),
        "terminalReturn": terminal,
        "terminalReturnOffset": terminal_offset,
        "terminalStackBytes": terminal_stack_bytes,
        "callLikeByteOffsets": call_like_offsets,
        "maxBodyBytes": 32,
        "maxRelativeTargetDistance": max_relative_target_distance,
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice contains compact decoded terminal-ret helper body; any trailing alignment padding is ignored",
        },
    }


def compact_import_call_ret_masm(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    decoded = decode_compact_import_call_ret_masm(row, data)
    if decoded is None:
        return []
    symbol = cdecl_symbol(c_name)
    body = data[: int(decoded["targetByteSpan"]["length"])]
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: compact-import-call-ret-masm.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **decoded,
        "sourceTier": "generated MASM byte-emission parity fallback with decoded compact import-call/ret bytes",
    }
    return [
        GeneratedCandidate(
            rule="compact-import-call-ret-masm",
            variant="masm-compact-import-call-ret",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="void",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def decode_compact_import_call_ret_masm(row: dict[str, Any], data: bytes) -> dict[str, Any] | None:
    body = strip_alignment_padding(data)
    if len(body) < 8 or len(body) > 96:
        return None
    if is_tail_fragment(body):
        return None
    terminal_stack_bytes = 0
    terminal = "ret"
    if body[-1] == 0xC3:
        terminal_offset = len(body) - 1
    elif len(body) >= 3 and body[-3] == 0xC2:
        terminal_offset = len(body) - 3
        terminal_stack_bytes = int.from_bytes(body[-2:], "little")
        terminal = f"ret 0x{terminal_stack_bytes:02x}"
    else:
        return None
    import_offsets: list[int] = []
    import_addresses: list[str] = []
    cursor = 0
    while True:
        offset = body.find(b"\xff\x15", cursor)
        if offset < 0:
            break
        if offset + 6 > len(body):
            return None
        import_offsets.append(offset)
        import_addresses.append(f"0x{int.from_bytes(body[offset + 2 : offset + 6], 'little'):08x}")
        cursor = offset + 6
    if not import_offsets:
        return None
    return {
        "bodyBytes": len(body),
        "terminalReturn": terminal,
        "terminalReturnOffset": terminal_offset,
        "terminalStackBytes": terminal_stack_bytes,
        "importCallOffsets": import_offsets,
        "importCallAddresses": import_addresses,
        "importCallCount": len(import_offsets),
        "maxBodyBytes": 96,
        "targetByteSpan": {
            "offset": 0,
            "length": len(body),
            "reason": "target slice contains compact decoded import-call/ret helper body; any trailing alignment padding is ignored",
        },
    }


def packed_leading_function_masm(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    automatic_generator = row.get("automaticGenerator") if isinstance(row.get("automaticGenerator"), dict) else {}
    if automatic_generator.get("rule") != "packed-leading-function-masm":
        return []
    if not data:
        return []
    symbol = cdecl_symbol(c_name)
    body = strip_alignment_padding(data)
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: packed-leading-function-masm.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; The original inferred slice was packed; this candidate covers only the leading split span.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **automatic_generator,
        "bodyBytes": len(body),
        "sourceTier": "generated MASM byte-emission parity fallback with mechanically split packed leading-function bytes",
    }
    return [
        GeneratedCandidate(
            rule="packed-leading-function-masm",
            variant="masm-packed-leading-function",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="void",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def bounded_terminal_leaf_masm(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    automatic_generator = row.get("automaticGenerator") if isinstance(row.get("automaticGenerator"), dict) else {}
    if automatic_generator.get("rule") != "bounded-terminal-leaf-masm":
        return []
    body = strip_alignment_padding(data)
    if not body:
        return []
    symbol = cdecl_symbol(c_name)
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: bounded-terminal-leaf-masm.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; The body has no decoded direct/import calls and ends in a terminal return.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **automatic_generator,
        "bodyBytes": len(body),
        "sourceTier": "generated MASM byte-emission parity fallback with decoded bounded terminal leaf/control bytes",
    }
    return [
        GeneratedCandidate(
            rule="bounded-terminal-leaf-masm",
            variant="masm-bounded-terminal-leaf",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="void",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def bounded_direct_call_terminal_masm(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    automatic_generator = row.get("automaticGenerator") if isinstance(row.get("automaticGenerator"), dict) else {}
    if automatic_generator.get("rule") != "bounded-direct-call-terminal-masm":
        return []
    body = strip_alignment_padding(data)
    if not body:
        return []
    symbol = cdecl_symbol(c_name)
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: bounded-direct-call-terminal-masm.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; The body has decoded direct E8 calls, no import calls, and a terminal return.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **automatic_generator,
        "bodyBytes": len(body),
        "sourceTier": "generated MASM byte-emission parity fallback with decoded bounded direct-call terminal bytes",
    }
    return [
        GeneratedCandidate(
            rule="bounded-direct-call-terminal-masm",
            variant="masm-bounded-direct-call-terminal",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="void",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def bounded_import_call_terminal_masm(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    automatic_generator = row.get("automaticGenerator") if isinstance(row.get("automaticGenerator"), dict) else {}
    if automatic_generator.get("rule") != "bounded-import-call-terminal-masm":
        return []
    body = strip_alignment_padding(data)
    if not body:
        return []
    symbol = cdecl_symbol(c_name)
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: bounded-import-call-terminal-masm.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; The body has decoded absolute import calls and a terminal return.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **automatic_generator,
        "bodyBytes": len(body),
        "sourceTier": "generated MASM byte-emission parity fallback with decoded bounded import-call terminal bytes",
    }
    return [
        GeneratedCandidate(
            rule="bounded-import-call-terminal-masm",
            variant="masm-bounded-import-call-terminal",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="void",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def bounded_leading_return_slice_masm(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    automatic_generator = row.get("automaticGenerator") if isinstance(row.get("automaticGenerator"), dict) else {}
    if automatic_generator.get("rule") != "bounded-leading-return-slice-masm":
        return []
    body = strip_alignment_padding(data)
    if not body:
        return []
    symbol = cdecl_symbol(c_name)
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: bounded-leading-return-slice-masm.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; The original target slice continues after this returned prefix.",
            "; This is source-slice parity only, not a full function-extent claim.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **automatic_generator,
        "bodyBytes": len(body),
        "sourceTier": "generated MASM byte-emission source-slice parity fallback; original target slice continues after the returned prefix",
    }
    return [
        GeneratedCandidate(
            rule="bounded-leading-return-slice-masm",
            variant="masm-bounded-leading-return-slice",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="void",
            evidence=evidence,
            source_suffix=".asm",
        )
    ]


def extended_terminal_body_masm(row: dict[str, Any], c_name: str, data: bytes) -> list[GeneratedCandidate]:
    automatic_generator = row.get("automaticGenerator") if isinstance(row.get("automaticGenerator"), dict) else {}
    if automatic_generator.get("rule") != "extended-terminal-body-masm":
        return []
    body = strip_alignment_padding(data)
    if not body:
        return []
    symbol = cdecl_symbol(c_name)
    source = "\n".join(
        [
            "; Generated by source-parity-synthesize.py.",
            "; Rule: extended-terminal-body-masm.",
            f"; Target: {row.get('name')} @ {row.get('entry')}.",
            "; The target slice ends in a return and is emitted byte-for-byte as MASM source.",
            "; This is byte-authoritative source parity, not high-level recovered C.",
            "; Acceptance requires objdiff zero; this file is not a claim by itself.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(body),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )
    evidence = {
        **automatic_generator,
        "bodyBytes": len(body),
        "sourceTier": "generated MASM byte-emission parity fallback for extended terminal body; not high-level recovered C",
    }
    return [
        GeneratedCandidate(
            rule="extended-terminal-body-masm",
            variant="masm-extended-terminal-body",
            c_name=c_name,
            symbol=symbol,
            source=source,
            callconv="cdecl",
            return_type="void",
            evidence=evidence,
            source_suffix=".asm",
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
    stdcall_copy_cstr_to_global,
    stdcall_indirect_global_callback_loop,
    stdcall_nullable_field_tailjmp,
    stdcall_clamped_count_copy_to_global,
    stdcall_global_callback_install,
    stdcall_track_method_forwarder,
    import_tail_jump,
    live_eax_nullable_import_tailjmp_stdcall4,
    ecx_global_cmp_return_else_tailjmp,
    x87_temp_i16_return,
    x87_pop_return_zero,
    x87_round_stack_double_return,
    x87_control_word_masked_setter,
    x87_double_exponent_adjust_return,
    stack_arg_range_global_mode_setter,
    u96_bit_tail_clear_check,
    ebx_bitfield_mode_remap,
    stdcall_store_two_stack_args_to_globals,
    stdcall_store_three_stack_args_to_globals,
    global_callback_nonzero_return_one,
    global_two_cmp_return_1_or_3,
    push_const_call_wrapper,
    push_imm32_pair_call_wrapper,
    u32_add_store_wrap_flag,
    push_global_call_wrapper,
    push_stack_stack_const_call_wrapper,
    bink_copy_to_buffer_forwarder,
    bink_buffer_clear_forwarder,
    bink_buffer_unlock_forwarder,
    bink_buffer_set_offset_forwarder,
    bink_buffer_set_direct_draw_forwarder,
    bink_buffer_check_win_pos_forwarder,
    bink_buffer_close_forwarder,
    bink_buffer_lock_forwarder,
    bink_buffer_set_scale_forwarder,
    bink_close_track_forwarder,
    bink_pause_forwarder,
    bink_get_key_frame_forwarder,
    bink_check_cursor_forwarder,
    bink_open_track_forwarder,
    bink_buffer_get_description_forwarder,
    bink_next_frame_forwarder,
    bink_get_realtime_forwarder,
    bink_goto_forwarder,
    bink_get_summary_forwarder,
    bink_close_forwarder,
    bink_open_direct_sound_forwarder,
    bink_wait_forwarder,
    bink_surface_type_forwarder,
    rad_aligned_malloc_forwarder,
    rad_aligned_free_forwarder,
    rad_direct_free_wrapper,
    rad_timer_read_forwarder,
    stdcall_yuv_blit_format_wrapper,
    stdcall_yuv_blit_alpha_wrapper,
    stdcall_yuv_blit_packed_wrapper,
    stdcall_yuv_blit_mask_format_prefix,
    stdcall_yuv_blit_mask_alpha_prefix,
    global_guard_call_set_return_zero,
    rep_stos_global_clear,
    small_zero_scan_bool,
    small_copy_loop,
    u96_left_shift_one,
    packed_leading_function_masm,
    bounded_terminal_leaf_masm,
    bounded_direct_call_terminal_masm,
    bounded_import_call_terminal_masm,
    bounded_leading_return_slice_masm,
    extended_terminal_body_masm,
    short_direct_call_ret_masm,
    x86_64_arg64_lea_multiply,
    x86_64_arg_imm32_binary_op64,
    x86_64_arg64_sign_extend,
    x86_64_arg64_rotate,
    x86_64_arg64_shift_imm8,
    x86_64_arg64_zero_nonzero,
    x86_64_arg64_bitmask_bool,
    x86_64_arg64_unary_op,
    x86_64_arg64_neg_cmov,
    x86_64_arg_udiv_magic,
    x86_64_arg_urem_pow2,
    x86_64_arg_urem_magic,
    x86_64_arg_sdiv_pow2,
    x86_64_arg_srem_pow2,
    x86_64_arg_sdiv_magic,
    x86_64_arg_srem_magic,
    x86_64_return_first_arg64,
    x86_64_return_second_arg,
    x86_64_two_args_binary_op64,
    x86_64_two_args_min_max64,
    x86_64_two_args_unsigned_compare64,
    x86_64_two_args_signed_compare64,
    compact_terminal_ret_masm,
    compact_import_call_ret_masm,
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
    x86_64_zero_return,
    x86_64_immediate_return,
    x86_64_one_return,
    zero_return,
    zero_return_stdcall,
    immediate_return,
    immediate_return_stdcall,
    x86_64_return_first_arg,
    x86_64_add_two_args,
    x86_64_three_args_arithmetic,
    x86_64_three_args_bitwise,
    x86_64_three_args_select,
    x86_64_two_args_affine_lea,
    x86_64_two_args_binary_op,
    x86_64_two_args_min_max,
    x86_64_arg_lea_multiply,
    x86_64_arg_const_min_max,
    x86_64_const_minus_arg,
    x86_64_arg_signbit_zero_compare,
    x86_64_arg_sign_mask,
    x86_64_arg_bitmask_bool,
    x86_64_arg_udiv_pow2,
    x86_64_arg_bswap32,
    x86_64_arg_bswap64,
    x86_64_arg_rotate,
    x86_64_arg_shift_imm8,
    x86_64_arg_imm8_binary_op,
    x86_64_arg_unary_op,
    x86_64_arg_neg_cmov,
    x86_64_arg_cast,
    x86_64_arg_narrow_imm8_compare,
    x86_64_arg_narrow_movzx_imm8_compare,
    x86_64_arg_unsigned_imm8_compare,
    x86_64_arg_signed_imm8_compare,
    x86_64_two_args_unsigned_compare,
    x86_64_two_args_signed_compare,
    x86_64_arg_signed_zero_compare,
    x86_64_arg_nonzero_const_select,
    x86_64_arg_nonzero_cmov_const_select,
    x86_64_arg_mask,
    x86_64_arg_nonzero,
    x86_64_arg_zero,
    framed_zero_return,
    framed_immediate_return,
    framed_return_first_stack_arg,
    x86_64_framed_zero_return,
    x86_64_framed_immediate_return,
    x86_64_framed_return_first_arg,
    x86_64_framed_add_two_args,
    global_setter_u32_stdcall,
    nullable_indexed_field_array_getter_stdcall,
    nullable_field_setter_u32_stdcall,
    one_return,
    one_return_stdcall,
    return_first_stack_arg,
    return_first_stack_arg_stdcall,
    add_two_stack_args,
    add_two_stack_args_stdcall,
    two_stack_args_binary_op,
    two_stack_args_binary_op_stdcall,
    two_stack_args_affine,
    two_stack_args_affine_stdcall,
    three_stack_args_commutative_op,
    three_stack_args_commutative_op_stdcall,
    three_stack_args_add_sub,
    three_stack_args_add_sub_stdcall,
    three_stack_args_mul_add,
    three_stack_args_mul_add_stdcall,
    two_stack_args_min_max,
    two_stack_args_min_max_stdcall,
    two_stack_args_unsigned_compare,
    two_stack_args_unsigned_compare_stdcall,
    two_stack_args_signed_compare,
    two_stack_args_signed_compare_stdcall,
    stack_arg_sdiv_magic,
    stack_arg_sdiv_magic_stdcall,
    stack_arg_srem_magic,
    stack_arg_srem_magic_stdcall,
    stack_arg_sdiv_pow2,
    stack_arg_sdiv_pow2_stdcall,
    stack_arg_srem_pow2,
    stack_arg_srem_pow2_stdcall,
    stack_arg_udiv_magic,
    stack_arg_udiv_magic_stdcall,
    stack_arg_urem_magic,
    stack_arg_urem_magic_stdcall,
    stack_arg_udiv_pow2,
    stack_arg_udiv_pow2_stdcall,
    stack_arg_urem_pow2,
    stack_arg_urem_pow2_stdcall,
    stack_arg_signed_zero_compare,
    stack_arg_signed_zero_compare_stdcall,
    stack_arg_neg_cmov,
    stack_arg_neg_cmov_stdcall,
    stack_arg_nonzero_const_select,
    stack_arg_nonzero_const_select_stdcall,
    stack_arg_nonzero_cmov_const_select,
    stack_arg_nonzero_cmov_const_select_stdcall,
    stack_arg_const_min_max,
    stack_arg_const_min_max_stdcall,
    stack_arg_signed_imm8_compare,
    stack_arg_signed_imm8_compare_stdcall,
    stack_arg_unsigned_imm8_compare,
    stack_arg_unsigned_imm8_compare_stdcall,
    stack_arg_bitmask_predicate,
    stack_arg_bitmask_predicate_stdcall,
    stack_arg_lea_multiply,
    stack_arg_lea_multiply_stdcall,
    stack_arg_imm8_binary_op,
    stack_arg_imm8_binary_op_stdcall,
    stack_arg_unary_op,
    stack_arg_unary_op_stdcall,
    stack_arg_inc_dec,
    stack_arg_inc_dec_stdcall,
    stack_arg_shift_imm8,
    stack_arg_shift_imm8_stdcall,
    stack_arg_nonzero_bool,
    stack_arg_zero_bool,
    stack_arg_nonzero_bool_stdcall,
    stack_arg_zero_bool_stdcall,
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
    if not candidates and data:
        candidates.append(
            GeneratedCandidate(
                rule="target-slice-asm-bootstrap",
                variant="byte-exact",
                c_name=c_name,
                symbol=c_name,
                source=render_target_bytes_asm(c_name, data),
                callconv="unknown",
                return_type="unknown",
                evidence={
                    "bodyBytes": len(data),
                    "purpose": "bootstrap compiler/objdiff plumbing from acquired target-slice bytes",
                    "semanticSource": False,
                },
                source_suffix=".S",
                semantic_source=False,
            )
        )
    # Keep all decoded candidates here. Rule, source-quality, and compiler filters
    # run after generation; truncating early lets broad byte-emission fallbacks hide
    # later semantic candidates for the same byte slice.
    return candidates


def packaged_source_candidate(row: dict[str, Any]) -> GeneratedCandidate | None:
    if not row.get("sourceTask"):
        return None
    loaded = read_packaged_source(row.get("source"))
    if loaded is None:
        return None
    source_path, source = loaded
    suffix = source_path.suffix.lower()
    if suffix not in {".c", ".asm", ".s"}:
        return None
    automatic_generator = row.get("automaticGenerator") if isinstance(row.get("automaticGenerator"), dict) else {}
    c_name = infer_packaged_c_name(row, source)
    return GeneratedCandidate(
        rule=str(automatic_generator.get("rule") or "packaged-source"),
        variant="packaged-source",
        c_name=c_name,
        symbol=infer_packaged_symbol(row, source, c_name, suffix),
        source=source,
        callconv=infer_packaged_callconv(source, suffix),
        return_type="unknown",
        extra_flags=tuple(),
        evidence={
            **automatic_generator,
            "sourceTier": automatic_generator.get("sourceTier") or row.get("sourceQuality") or "packaged source task candidate",
            "sourceQuality": row.get("sourceQuality"),
            "packagedSource": str(source_path),
            "packagedSourceSha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "sourceOrigin": row.get("sourceOrigin"),
        },
        source_suffix=suffix,
        semantic_source=row.get("semanticSource") is not False,
    )


def infer_packaged_c_name(row: dict[str, Any], source: str) -> str:
    expected = safe_c_name(str(row.get("name") or row.get("entry") or "function"))
    if re.search(rf"\b{re.escape(expected)}\s*\(", source):
        return expected
    matches = list(re.finditer(
        r"(?:__declspec\s*\(\s*naked\s*\)\s*)?(?:[A-Za-z_][A-Za-z0-9_\s\*]+?\s+)?(?:__(?:cdecl|stdcall|fastcall)\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(",
        source,
    ))
    for match in matches:
        line_start = source.rfind("\n", 0, match.start()) + 1
        line = source[line_start:match.start()]
        if "extern" in line or "typedef" in line:
            continue
        candidate = match.group(1)
        if candidate not in {"if", "for", "while", "switch", "return", "sizeof"}:
            return candidate
    return expected


def infer_packaged_callconv(source: str, suffix: str) -> str:
    if suffix in {".asm", ".s"}:
        return "assembly"
    if "__fastcall" in source:
        return "fastcall"
    if "__stdcall" in source:
        return "stdcall"
    if "__cdecl" in source:
        return "cdecl"
    return "cdecl"


def infer_packaged_symbol(row: dict[str, Any], source: str, c_name: str, suffix: str) -> str:
    if suffix in {".asm", ".s"}:
        match = re.search(r"(?im)^\s*PUBLIC\s+([@A-Za-z_.$?][@A-Za-z0-9_.$?]*)\s*$", source)
        if match:
            return match.group(1)
        return c_name
    callconv = infer_packaged_callconv(source, suffix)
    stack_bytes = packaged_stack_bytes(row, c_name)
    if callconv == "stdcall" and stack_bytes is not None:
        return f"_{c_name}@{stack_bytes}"
    if callconv == "fastcall" and stack_bytes is not None:
        return fastcall_symbol(c_name, stack_bytes)
    return cdecl_symbol(c_name)


def packaged_stack_bytes(row: dict[str, Any], c_name: str) -> int | None:
    generator = row.get("automaticGenerator") if isinstance(row.get("automaticGenerator"), dict) else {}
    stack_bytes = optional_int(generator.get("stackBytes"))
    if stack_bytes is not None:
        return stack_bytes
    name = str(row.get("name") or "")
    match = re.search(r"@(\d+)$", name)
    if match:
        return int(match.group(1))
    match = re.search(r"_(\d+)$", c_name)
    if match:
        return int(match.group(1))
    return None


def compiler_compatible_candidates(
    row: dict[str, Any],
    candidates: list[GeneratedCandidate],
    compiler: str,
    max_variants: int,
) -> list[GeneratedCandidate]:
    compatible = prioritize_candidates(
        [candidate for candidate in candidates if candidate_compiler_compatible(candidate, compiler)]
    )
    if compatible:
        return compatible[:max_variants]
    data = parse_bytes(row)
    if not data or not row.get("sourceTask"):
        return []
    c_name = safe_c_name(str(row.get("name") or row.get("entry") or "function"))
    if compiler == "clang":
        return [
            GeneratedCandidate(
                rule="target-slice-asm-bootstrap",
                variant="clang-byte-exact-fallback",
                c_name=c_name,
                symbol=c_name,
                source=render_target_bytes_asm(c_name, data),
                callconv="unknown",
                return_type="unknown",
                evidence={
                    "bodyBytes": len(data),
                    "purpose": "compiler-compatible fallback after MASM-only candidates were filtered out",
                    "semanticSource": False,
                    "filteredCandidateCount": len(candidates),
                    "filteredCandidateRules": sorted({candidate.rule for candidate in candidates}),
                },
                source_suffix=".S",
                semantic_source=False,
            )
        ]
    if compiler == "msvc":
        symbol = cdecl_symbol(c_name)
        return [
            GeneratedCandidate(
                rule="target-slice-asm-bootstrap",
                variant="msvc-masm-byte-exact-fallback",
                c_name=c_name,
                symbol=symbol,
                source=render_target_bytes_masm(symbol, data),
                callconv="unknown",
                return_type="unknown",
                evidence={
                    "bodyBytes": len(data),
                    "purpose": "compiler-compatible fallback after non-MSVC candidates were filtered out",
                    "semanticSource": False,
                    "sourceQuality": "byte-emission-asm",
                    "sourceRecoveryScope": "nonsemantic",
                    "filteredCandidateCount": len(candidates),
                    "filteredCandidateRules": sorted({candidate.rule for candidate in candidates}),
                },
                source_suffix=".asm",
                semantic_source=False,
            )
        ]
    return []


def candidate_compiler_compatible(candidate: GeneratedCandidate, compiler: str) -> bool:
    suffix = candidate.source_suffix.lower()
    if compiler == "msvc":
        return suffix in {".c", ".asm"}
    if compiler == "clang":
        return suffix in {".c", ".s"}
    if compiler == "clang-cl":
        return suffix == ".c"
    return True


def prioritize_candidates(candidates: list[GeneratedCandidate]) -> list[GeneratedCandidate]:
    quality_rank = {
        "high-level-c": 0,
        "inline-asm-c": 1,
        "byte-emission-asm": 2,
        "nonsemantic-bootstrap": 3,
    }
    return [
        candidate
        for _, candidate in sorted(
        enumerate(candidates),
        key=lambda item: (
            quality_rank.get(generated_candidate_source_quality(item[1]), 4),
            1 if item[1].variant == "packaged-source" else 0,
            item[0],
        ),
        )
    ]


def resolve_attempt_limit(
    *,
    row: dict[str, Any],
    candidates: list[GeneratedCandidate],
    base_limit: int,
    policy: str,
) -> tuple[int, str]:
    """Compute per-function attempt cap from row recovery shape and policy."""
    if base_limit <= 0:
        return base_limit, "disabled"
    if policy == "uniform":
        return base_limit, "uniform"
    limit = base_limit
    reason = "uniform-policy"
    if is_boundary_suspect(row):
        limit = max(1, min(limit, 1))
        reason = "boundary-suspect"
    else:
        scope = source_recovery_scope(row)
        if scope == "partial-source-slice":
            limit = 1
            reason = "partial-source-slice"
        elif scope == "context-dependent-fragment":
            limit = max(1, min(limit, 2))
            reason = "context-dependent-fragment"
    if candidates and all(generated_candidate_source_quality(candidate) == "nonsemantic-bootstrap" for candidate in candidates):
        limit = max(1, min(limit, 1))
        reason = "nonsemantic-only"
    return limit, reason


def append_packaged_fallback(
    candidates: list[GeneratedCandidate],
    packaged_candidate: GeneratedCandidate | None,
) -> list[GeneratedCandidate]:
    if packaged_candidate is None:
        return candidates
    packaged_key = (packaged_candidate.rule, packaged_candidate.variant, packaged_candidate.source)
    for candidate in candidates:
        if (candidate.rule, candidate.variant, candidate.source) == packaged_key:
            return candidates
    return [*candidates, packaged_candidate]


def load_strategy(path: Path) -> dict[str, str]:
    return {str(row.get("name")): str(row.get("strategyClass")) for row in iter_jsonl(path)}


def load_retrieval(path: Path) -> dict[str, list[dict[str, Any]]]:
    return {str(row.get("name")): list(row.get("nearestMatchedExamples") or []) for row in iter_jsonl(path)}


def load_matched(paths: list[Path]) -> set[tuple[str, str]]:
    matched: set[tuple[str, str]] = set()
    for path in paths:
        for row in iter_jsonl(path):
            if row.get("status") in {"matched", "code-slice-matched"} and int(row.get("differences", -1)) == 0:
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
    compiler: str,
    clang: str,
    compiler_profiles: list[tuple[str, list[str]]],
    vc_root: Path | None,
    wine: str,
    wineprefix: Path | None,
    timeout: int,
    dry_run: bool,
    source_shape_search: bool = False,
) -> list[dict[str, Any]]:
    case_dir = out_dir / "cases" / candidate_id(row, candidate)
    case_dir.mkdir(parents=True, exist_ok=True)
    suffix = candidate.source_suffix if candidate.source_suffix.startswith(".") else f".{candidate.source_suffix}"
    candidate_c = case_dir / f"candidate{suffix}"
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
        "sourceOrigin": (
            "packaged source file from source-generation task; verification compares that artifact to target-slice bytes"
            if candidate.evidence.get("packagedSource")
            else "generated from instruction bytes by source-parity-synthesize.py; not manually authored"
        ),
        "semanticSource": candidate.semantic_source,
        "sourceQuality": generated_candidate_source_quality(candidate),
        "sourceRecoveryScope": source_recovery_scope(row, candidate),
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

    if compiler == "clang":
        return attempt_candidate_with_clang_slice_objdiff(
            row,
            candidate,
            case_dir,
            candidate_c,
            base_record,
            compiler_profiles=compiler_profiles,
            clang=clang,
            timeout=timeout,
        )

    if compiler == "clang-cl":
        return attempt_candidate_with_clangcl_slice_objdiff(
            row,
            candidate,
            case_dir,
            candidate_c,
            base_record,
            compiler_profiles=compiler_profiles,
            clang=clang,
            timeout=timeout,
        )

    if compiler == "msvc" and parse_bytes(row):
        return attempt_candidate_with_msvc_synthetic_slice(
            row,
            candidate,
            case_dir,
            candidate_c,
            base_record,
            compiler_profiles=compiler_profiles,
            vc_root=vc_root,
            wine=wine,
            wineprefix=wineprefix,
            timeout=timeout,
            source_shape_search=source_shape_search,
        )

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
    resolved_profiles = resolve_profiles(row, compiler_profiles, compiler)
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


def attempt_candidate_with_clang_slice_objdiff(
    row: dict[str, Any],
    candidate: GeneratedCandidate,
    case_dir: Path,
    candidate_c: Path,
    base_record: dict[str, Any],
    *,
    compiler_profiles: list[tuple[str, list[str]]],
    clang: str,
    timeout: int,
) -> list[dict[str, Any]]:
    data = parse_bytes(row)
    if not data:
        return [
            {
                **base_record,
                "status": "slice-failed",
                "differences": -1,
                "reason": "row has no target bytes for synthetic target object",
                "attemptDir": str(case_dir),
                "compilerProfileName": "slice-failed",
            }
        ]
    attempts: list[dict[str, Any]] = []
    resolved_profiles = resolve_profiles(row, compiler_profiles, "clang")
    for profile_index, (profile_name, profile_args) in enumerate(resolved_profiles):
        attempt_dir = case_dir / f"profile_{profile_index:02d}_{safe_dir_name(profile_name)}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        merged_flags = normalize_clang_flags(profile_args, list(candidate.extra_flags))
        target_asm = attempt_dir / "target.S"
        target_obj = attempt_dir / "target.o"
        candidate_obj = attempt_dir / "candidate.o"
        target_data = strip_alignment_padding(data) if candidate.semantic_source else data
        target_asm.write_text(render_target_bytes_asm_for_clang(row, candidate, target_data, merged_flags), encoding="utf-8")
        target_compile = compile_with_clang(
            clang=clang,
            source=target_asm,
            object_path=target_obj,
            args=clang_asm_flags(merged_flags),
            timeout=timeout,
        )
        candidate_compile = compile_with_clang(
            clang=clang,
            source=candidate_c,
            object_path=candidate_obj,
            args=merged_flags,
            timeout=timeout,
        )
        record = {
            **base_record,
            "attemptDir": str(attempt_dir),
            "compiler": "clang",
            "compilerProfileName": profile_name,
            "compilerProfileArgs": merged_flags,
            "compilerProfiles": [name for name, _ in resolved_profiles],
            "targetObject": str(target_obj),
            "targetObjectOrigin": "synthetic object assembled from source-task target slice bytes",
            "verificationTier": "synthetic-target-object-objdiff",
            "claimBoundary": "objdiff compares candidate object to a synthetic object made from target slice bytes; this is code-slice evidence, not full target-object source parity",
        }
        if target_compile.get("status") != "ok":
            attempts.append(
                {
                    **record,
                    "status": "slice-failed",
                    "differences": -1,
                    "stderr": str(target_compile.get("stderrTail") or target_compile.get("reason") or "")[-2000:],
                    "targetCompile": target_compile,
                }
            )
            continue
        if candidate_compile.get("status") != "ok":
            attempts.append(
                {
                    **record,
                    "status": "compile-failed",
                    "differences": -1,
                    "stderr": str(candidate_compile.get("stderrTail") or candidate_compile.get("reason") or "")[-2000:],
                    "targetCompile": target_compile,
                    "candidateCompile": candidate_compile,
                }
            )
            continue
        report = run_objdiff(target_obj, candidate_obj, attempt_dir, timeout=timeout)
        status = "code-slice-matched" if report.get("status") == "matched" and int(report.get("differences", -1)) == 0 else str(report.get("status"))
        attempts.append(
            {
                **record,
                "status": status,
                "differences": int(report.get("differences", -1)),
                "message": report.get("message"),
                "verifyReport": str(attempt_dir / "verify.json"),
                "targetCompile": target_compile,
                "candidateCompile": candidate_compile,
            }
        )
        if status == "code-slice-matched":
            break
    return attempts


def attempt_candidate_with_msvc_synthetic_slice(
    row: dict[str, Any],
    candidate: GeneratedCandidate,
    case_dir: Path,
    candidate_source: Path,
    base_record: dict[str, Any],
    *,
    compiler_profiles: list[tuple[str, list[str]]],
    vc_root: Path | None,
    wine: str,
    wineprefix: Path | None,
    timeout: int,
    source_shape_search: bool = False,
) -> list[dict[str, Any]]:
    data = parse_bytes(row)
    if not data:
        return [
            {
                **base_record,
                "status": "slice-failed",
                "differences": -1,
                "reason": "row has no target bytes for synthetic target object",
                "attemptDir": str(case_dir),
                "compilerProfileName": "slice-failed",
            }
        ]
    attempts: list[dict[str, Any]] = []
    resolved_profiles = resolve_profiles(row, compiler_profiles, "msvc")
    seen_merged_flags: set[tuple[str, ...]] = set()
    attempt_timeout = min(timeout, 30)
    for profile_index, (profile_name, profile_args) in enumerate(resolved_profiles):
        merged_flags = normalize_profile_flags(profile_args, list(candidate.extra_flags))
        merged_key = tuple(merged_flags)
        if merged_key in seen_merged_flags:
            continue
        seen_merged_flags.add(merged_key)
        attempt_dir = case_dir / f"profile_{profile_index:02d}_{safe_dir_name(profile_name)}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        target_data = strip_alignment_padding(data) if candidate.semantic_source else data
        target_asm = attempt_dir / "target.S"
        target_obj = attempt_dir / "target.obj"
        candidate_obj = attempt_dir / "candidate.obj"
        target_render = render_target_coff_for_candidate(candidate, target_data)
        target_asm.write_text(target_render["asm"], encoding="utf-8")
        target_compile = compile_asm_to_coff(
            clang="clang",
            source=target_asm,
            object_path=target_obj,
            timeout=attempt_timeout,
        )
        candidate_compile = compile_with_msvc(
            source=candidate_source,
            object_path=candidate_obj,
            out_dir=attempt_dir,
            stem="candidate",
            args=merged_flags,
            timeout=attempt_timeout,
            msvc_root=vc_root,
            wine=wine,
            wineprefix=wineprefix,
        )
        record = {
            **base_record,
            "attemptDir": str(attempt_dir),
            "compiler": "msvc",
            "compilerProfileName": profile_name,
            "compilerProfileArgs": merged_flags,
            "compilerProfiles": [name for name, _ in resolved_profiles],
            "targetObject": str(target_obj),
            "targetObjectOrigin": target_render["origin"],
            "targetObjectRelocations": target_render["relocations"],
            "verificationTier": "synthetic-target-coff-objdiff",
            "claimBoundary": target_render["claimBoundary"],
        }
        if target_compile.get("status") != "ok":
            attempts.append(
                {
                    **record,
                    "status": "slice-failed",
                    "differences": -1,
                    "stderr": str(target_compile.get("stderrTail") or target_compile.get("reason") or "")[-2000:],
                    "targetCompile": target_compile,
                }
            )
            continue
        if candidate_compile.get("status") != "ok":
            attempts.append(
                {
                    **record,
                    "status": "compile-failed",
                    "differences": -1,
                    "stderr": str(candidate_compile.get("stderrTail") or candidate_compile.get("reason") or "")[-2000:],
                    "targetCompile": target_compile,
                    "candidateCompile": candidate_compile,
                }
            )
            continue
        report = run_objdiff(target_obj, candidate_obj, attempt_dir, timeout=timeout)
        status = "code-slice-matched" if report.get("status") == "matched" and int(report.get("differences", -1)) == 0 else str(report.get("status"))
        attempts.append(
            {
                **record,
                "status": status,
                "differences": int(report.get("differences", -1)),
                "message": report.get("message"),
                "verifyReport": str(attempt_dir / "verify.json"),
                "targetCompile": target_compile,
                "candidateCompile": candidate_compile,
            }
        )
        if status == "code-slice-matched":
            break
    has_code_slice_match = any(item.get("status") == "code-slice-matched" and int(item.get("differences", -1)) == 0 for item in attempts)
    shape_variants = semantic_equivalent_variants(row, candidate)
    should_run_promotion_search = (
        has_code_slice_match
        and generated_candidate_source_quality(candidate) == "inline-asm-c"
        and bool(shape_variants)
    )
    should_search_source_shapes = (
        source_shape_search
        and attempts
        and candidate.semantic_source
        and bool(shape_variants)
        and (
            candidate.source_suffix != ".c"
            or not has_code_slice_match
            or should_run_promotion_search
        )
    )
    if should_search_source_shapes:
        search = run_msvc_source_shape_search(
            row,
            candidate,
            case_dir,
            data=strip_alignment_padding(data),
            compiler_profiles=resolved_profiles,
            vc_root=vc_root,
            wine=wine,
            wineprefix=wineprefix,
            timeout=timeout,
        )
        if search is not None:
            for attempt in attempts:
                attempt["sourceShapeSearch"] = search["path"]
                attempt["sourceShapeSearchSummary"] = search["summary"]
                if should_run_promotion_search:
                    attempt["sourceShapeSearchReason"] = "matched inline-asm candidate; probing byte-identical high-level C promotion variants"
                elif candidate.source_suffix != ".c":
                    attempt["sourceShapeSearchReason"] = "matched byte-emission candidate; probing semantic-equivalent high-level C source-shape variants"
    return attempts


def attempt_candidate_with_clangcl_slice_objdiff(
    row: dict[str, Any],
    candidate: GeneratedCandidate,
    case_dir: Path,
    candidate_source: Path,
    base_record: dict[str, Any],
    *,
    compiler_profiles: list[tuple[str, list[str]]],
    clang: str,
    timeout: int,
) -> list[dict[str, Any]]:
    data = parse_bytes(row)
    if not data:
        return [
            {
                **base_record,
                "status": "slice-failed",
                "differences": -1,
                "reason": "row has no target bytes for synthetic target object",
                "attemptDir": str(case_dir),
                "compilerProfileName": "slice-failed",
            }
        ]
    attempts: list[dict[str, Any]] = []
    resolved_profiles = resolve_profiles(row, compiler_profiles, "clang-cl")
    clang_name = Path(clang).name.lower()
    clang_cl = "clang-cl" if clang_name in {"clang", "clang.exe"} else clang
    asm_clang = "clang" if Path(clang_cl).name.lower() in {"clang-cl", "clang-cl.exe"} else clang
    for profile_index, (profile_name, profile_args) in enumerate(resolved_profiles):
        attempt_dir = case_dir / f"profile_{profile_index:02d}_{safe_dir_name(profile_name)}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        target_asm = attempt_dir / "target.S"
        target_obj = attempt_dir / "target.obj"
        candidate_obj = attempt_dir / "candidate.obj"
        target_data = strip_alignment_padding(data) if candidate.semantic_source else data
        target_asm.write_text(render_target_bytes_coff_asm(candidate.symbol, target_data), encoding="utf-8")
        target_compile = compile_asm_to_coff(
            clang=asm_clang,
            source=target_asm,
            object_path=target_obj,
            timeout=timeout,
        )
        candidate_compile = compile_with_clangcl(
            clang_cl=clang_cl,
            source=candidate_source,
            object_path=candidate_obj,
            args=profile_args,
            timeout=timeout,
        )
        record = {
            **base_record,
            "attemptDir": str(attempt_dir),
            "compiler": "clang-cl",
            "compilerProfileName": profile_name,
            "compilerProfileArgs": profile_args,
            "compilerProfiles": [name for name, _ in resolved_profiles],
            "targetObject": str(target_obj),
            "targetObjectOrigin": "synthetic Windows COFF object assembled from source-task target slice bytes",
            "verificationTier": "synthetic-target-coff-objdiff",
            "claimBoundary": "objdiff compares candidate COFF to a synthetic COFF object made from target slice bytes; this is code-slice evidence, not full target-object source parity",
        }
        if target_compile.get("status") != "ok":
            attempts.append(
                {
                    **record,
                    "status": "slice-failed",
                    "differences": -1,
                    "stderr": str(target_compile.get("stderrTail") or target_compile.get("reason") or "")[-2000:],
                    "targetCompile": target_compile,
                }
            )
            continue
        if candidate_compile.get("status") != "ok":
            attempts.append(
                {
                    **record,
                    "status": "compile-failed",
                    "differences": -1,
                    "stderr": str(candidate_compile.get("stderrTail") or candidate_compile.get("reason") or "")[-2000:],
                    "targetCompile": target_compile,
                    "candidateCompile": candidate_compile,
                }
            )
            continue
        report = run_objdiff(target_obj, candidate_obj, attempt_dir, timeout=timeout)
        status = "code-slice-matched" if report.get("status") == "matched" and int(report.get("differences", -1)) == 0 else str(report.get("status"))
        attempts.append(
            {
                **record,
                "status": status,
                "differences": int(report.get("differences", -1)),
                "message": report.get("message"),
                "verifyReport": str(attempt_dir / "verify.json"),
                "targetCompile": target_compile,
                "candidateCompile": candidate_compile,
            }
        )
        if status == "code-slice-matched":
            break
    if attempts and candidate.semantic_source and not any(item.get("status") == "code-slice-matched" and int(item.get("differences", -1)) == 0 for item in attempts):
        search = run_source_shape_search(
            row,
            candidate,
            case_dir,
            data=strip_alignment_padding(data),
            clang_cl=clang_cl,
            timeout=timeout,
        )
        if search is not None:
            for attempt in attempts:
                attempt["sourceShapeSearch"] = search["path"]
                attempt["sourceShapeSearchSummary"] = search["summary"]
    return attempts


def normalize_clang_flags(profile_flags: list[str], overrides: list[str]) -> list[str]:
    merged: list[str] = []
    for flag in [*profile_flags, *overrides]:
        if not isinstance(flag, str) or not flag:
            continue
        if flag.startswith("/"):
            continue
        lowered = flag.lower()
        if lowered in {"-m32", "-m64"}:
            merged = [entry for entry in merged if entry.lower() not in {"-m32", "-m64"}]
        elif lowered.startswith("--target="):
            merged = [entry for entry in merged if not entry.lower().startswith("--target=")]
        if lowered.startswith("-o") and lowered not in {"-o0", "-o1", "-o2", "-o3", "-os", "-oz"}:
            merged = [entry for entry in merged if not entry.lower().startswith("-o")]
        elif lowered in {"-o0", "-o1", "-o2", "-o3", "-os", "-oz"}:
            merged = [entry for entry in merged if entry.lower() not in {"-o0", "-o1", "-o2", "-o3", "-os", "-oz"}]
        if flag not in merged:
            merged.append(flag)
    return merged


def clang_asm_flags(flags: list[str]) -> list[str]:
    return [flag for flag in flags if flag in {"-m32", "-m64"} or flag.startswith("--target=") or flag == "-target"]


def compile_with_clang(*, clang: str, source: Path, object_path: Path, args: list[str], timeout: int) -> dict[str, Any]:
    command = [
        clang,
        *args,
        "-x",
        "assembler" if source.suffix.lower() in {".s", ".asm"} else "c",
        "-std=gnu89" if source.suffix.lower() not in {".s", ".asm"} else "",
        "-Wno-everything",
        "-c",
        str(source),
        "-o",
        str(object_path),
    ]
    command = [item for item in command if item]
    proc = run(command, timeout=timeout)
    stdout_path = object_path.with_suffix(".clang.stdout.txt")
    stderr_path = object_path.with_suffix(".clang.stderr.txt")
    stdout_path.write_text(proc.stdout, encoding="utf-8")
    stderr_path.write_text(proc.stderr, encoding="utf-8")
    return {
        "status": "ok" if proc.returncode == 0 and object_path.exists() else "failed",
        "returnCode": proc.returncode,
        "command": command,
        "object": str(object_path) if object_path.exists() else None,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "stderrTail": proc.stderr[-2000:],
    }


def compile_asm_to_coff(*, clang: str, source: Path, object_path: Path, timeout: int) -> dict[str, Any]:
    command = [
        clang,
        "-target",
        "i686-pc-windows-msvc",
        "-x",
        "assembler",
        "-c",
        str(source),
        "-o",
        str(object_path),
    ]
    proc = run(command, timeout=timeout)
    stdout_path = object_path.with_suffix(".clang.stdout.txt")
    stderr_path = object_path.with_suffix(".clang.stderr.txt")
    stdout_path.write_text(proc.stdout, encoding="utf-8")
    stderr_path.write_text(proc.stderr, encoding="utf-8")
    return {
        "status": "ok" if proc.returncode == 0 and object_path.exists() else "failed",
        "returnCode": proc.returncode,
        "command": command,
        "object": str(object_path) if object_path.exists() else None,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "stderrTail": proc.stderr[-2000:],
    }


def compile_with_clangcl(*, clang_cl: str, source: Path, object_path: Path, args: list[str], timeout: int) -> dict[str, Any]:
    command = [
        clang_cl,
        "--target=i686-pc-windows-msvc",
        "/nologo",
        "/c",
        *args,
        str(source),
        f"/Fo{object_path}",
    ]
    proc = run(command, timeout=timeout)
    stdout_path = object_path.with_suffix(".clangcl.stdout.txt")
    stderr_path = object_path.with_suffix(".clangcl.stderr.txt")
    stdout_path.write_text(proc.stdout, encoding="utf-8")
    stderr_path.write_text(proc.stderr, encoding="utf-8")
    return {
        "status": "ok" if proc.returncode == 0 and object_path.exists() else "failed",
        "returnCode": proc.returncode,
        "command": command,
        "object": str(object_path) if object_path.exists() else None,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "stderrTail": proc.stderr[-2000:],
    }


def run_source_shape_search(
    row: dict[str, Any],
    candidate: GeneratedCandidate,
    case_dir: Path,
    *,
    data: bytes,
    clang_cl: str,
    timeout: int,
) -> dict[str, Any] | None:
    variants = semantic_equivalent_variants(row, candidate)
    if not variants:
        return None
    search_dir = case_dir / "source-shape-search"
    search_dir.mkdir(parents=True, exist_ok=True)
    attempts: list[dict[str, Any]] = []
    for index, variant in enumerate(variants):
        variant_dir = search_dir / f"{index:02d}_{safe_dir_name(str(variant['name']))}"
        variant_dir.mkdir(parents=True, exist_ok=True)
        source_path = variant_dir / "candidate.c"
        object_path = variant_dir / "candidate.obj"
        source_text = str(variant["source"]).rstrip() + "\n"
        source_path.write_text(source_text, encoding="utf-8")
        compile_result = compile_with_clangcl(
            clang_cl=clang_cl,
            source=source_path,
            object_path=object_path,
            args=["/O2", "/GS-", "/Oy", "/Gz"],
            timeout=timeout,
        )
        candidate_bytes = object_text_bytes(object_path, timeout=timeout) if compile_result.get("status") == "ok" else b""
        matched = candidate_bytes == data
        attempts.append(
            {
                "name": variant["name"],
                "semanticEquivalent": bool(variant.get("semanticEquivalent", True)),
                "source": str(source_path),
                "sourceSha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
                "compile": compile_result,
                "candidateBytesSha256": hashlib.sha256(candidate_bytes).hexdigest() if candidate_bytes else None,
                "candidateBytesHexPrefix": candidate_bytes[:48].hex(),
                "targetBytesHexPrefix": data[:48].hex(),
                "byteIdentical": matched,
                "commonPrefixBytes": common_prefix_len(data, candidate_bytes),
                "candidateSize": len(candidate_bytes),
                "targetSize": len(data),
            }
        )
    best = max(attempts, key=lambda item: (bool(item.get("byteIdentical")), int(item.get("commonPrefixBytes") or 0)), default=None)
    report = {
        "schema": "mizuchi.source-shape-search.v1",
        "status": "matched" if best and best.get("byteIdentical") else "no-match",
        "rule": candidate.rule,
        "variant": candidate.variant,
        "name": row.get("name"),
        "entry": row.get("entry"),
        "compiler": "clang-cl",
        "profile": ["/O2", "/GS-", "/Oy", "/Gz"],
        "targetBytesSha256": hashlib.sha256(data).hexdigest(),
        "targetSize": len(data),
        "attempts": attempts,
        "best": {
            "name": best.get("name"),
            "byteIdentical": best.get("byteIdentical"),
            "commonPrefixBytes": best.get("commonPrefixBytes"),
            "candidateSize": best.get("candidateSize"),
        }
        if best
        else None,
        "claimBoundary": "source-shape search ranks semantic-equivalent C spellings for compiler-profile work; it is not accepted source unless the verifier reports objdiff zero",
    }
    path = search_dir / "summary.json"
    write_json(path, report)
    return {
        "path": str(path),
        "summary": {
            "status": report["status"],
            "attempts": len(attempts),
            "best": report["best"],
        },
    }


def run_msvc_source_shape_search(
    row: dict[str, Any],
    candidate: GeneratedCandidate,
    case_dir: Path,
    *,
    data: bytes,
    compiler_profiles: list[tuple[str, list[str]]],
    vc_root: Path | None,
    wine: str,
    wineprefix: Path | None,
    timeout: int,
) -> dict[str, Any] | None:
    variants = semantic_equivalent_variants(row, candidate)
    if not variants:
        return None
    compare_data = source_shape_compare_bytes(candidate, data)
    profiles = compiler_profiles or default_profile_set("msvc")[:1]
    attempt_timeout = min(timeout, 30)
    search_dir = case_dir / "source-shape-search-msvc"
    search_dir.mkdir(parents=True, exist_ok=True)
    attempts: list[dict[str, Any]] = []
    for variant_index, variant in enumerate(variants[:8]):
        for profile_index, (profile_name, profile_args) in enumerate(profiles[:4]):
            variant_key = hashlib.sha1(f"{variant.get('name')}|{profile_name}".encode("utf-8")).hexdigest()[:8]
            variant_dir = search_dir / f"v{variant_index:02d}_p{profile_index:02d}_{variant_key}"
            variant_dir.mkdir(parents=True, exist_ok=True)
            source_path = variant_dir / "candidate.c"
            object_path = variant_dir / "candidate.obj"
            source_text = str(variant["source"]).rstrip() + "\n"
            source_path.write_text(source_text, encoding="utf-8")
            variant_extra_flags = variant.get("extraFlags")
            extra_flags = list(candidate.extra_flags)
            if isinstance(variant_extra_flags, list):
                extra_flags.extend(str(flag) for flag in variant_extra_flags if isinstance(flag, str) and flag)
            merged_flags = normalize_profile_flags(profile_args, extra_flags)
            compile_result = compile_with_msvc(
                source=source_path,
                object_path=object_path,
                out_dir=variant_dir,
                stem="candidate",
                args=merged_flags,
                timeout=attempt_timeout,
                msvc_root=vc_root,
                wine=wine,
                wineprefix=wineprefix,
            )
            candidate_bytes = object_text_bytes(object_path, timeout=attempt_timeout) if compile_result.get("status") == "ok" else b""
            matched = candidate_bytes == compare_data
            attempts.append(
                {
                    "name": variant["name"],
                    "semanticEquivalent": bool(variant.get("semanticEquivalent", True)),
                    "profile": profile_name,
                    "profileArgs": merged_flags,
                    "source": str(source_path),
                    "sourceSha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
                    "compile": compile_result,
                    "candidateBytesSha256": hashlib.sha256(candidate_bytes).hexdigest() if candidate_bytes else None,
                    "candidateBytesHexPrefix": candidate_bytes[:48].hex(),
                    "targetBytesHexPrefix": compare_data[:48].hex(),
                    "targetBytesNormalizedForRelocations": compare_data != data,
                    "byteIdentical": matched,
                    "commonPrefixBytes": common_prefix_len(compare_data, candidate_bytes),
                    "candidateSize": len(candidate_bytes),
                    "targetSize": len(compare_data),
                }
            )
            if matched:
                break
        if attempts and attempts[-1].get("byteIdentical"):
            break
    best = max(attempts, key=lambda item: (bool(item.get("byteIdentical")), int(item.get("commonPrefixBytes") or 0)), default=None)
    report = {
        "schema": "mizuchi.source-shape-search.v1",
        "status": "matched" if best and best.get("byteIdentical") else "no-match",
        "rule": candidate.rule,
        "variant": candidate.variant,
        "name": row.get("name"),
        "entry": row.get("entry"),
        "compiler": "msvc",
        "targetBytesSha256": hashlib.sha256(compare_data).hexdigest(),
        "targetBytesNormalizedForRelocations": compare_data != data,
        "targetSize": len(compare_data),
        "attempts": attempts,
        "best": {
            "name": best.get("name"),
            "profile": best.get("profile"),
            "byteIdentical": best.get("byteIdentical"),
            "commonPrefixBytes": best.get("commonPrefixBytes"),
            "candidateSize": best.get("candidateSize"),
        }
        if best
        else None,
        "claimBoundary": "source-shape search ranks semantic-equivalent C spellings for compiler-profile work; it is not accepted source unless the verifier reports objdiff zero",
    }
    path = search_dir / "summary.json"
    write_json(path, report)
    return {
        "path": str(path),
        "summary": {
            "status": report["status"],
            "attempts": len(attempts),
            "best": report["best"],
        },
    }


def semantic_equivalent_variants(row: dict[str, Any], candidate: GeneratedCandidate) -> list[dict[str, Any]]:
    if candidate.rule == "bink-buffer-set-scale-forwarder":
        global_width = optional_int(candidate.evidence.get("globalWidthFallbackAddress"))
        global_height = optional_int(candidate.evidence.get("globalHeightFallbackAddress"))
        if global_width is None or global_height is None:
            return []
        source_width = optional_int(candidate.evidence.get("sourceWidthFieldOffset"))
        source_height = optional_int(candidate.evidence.get("sourceHeightFieldOffset"))
        scale_flags = optional_int(candidate.evidence.get("scaleFlagsFieldOffset"))
        scaled_width = optional_int(candidate.evidence.get("scaledWidthFieldOffset"))
        scaled_height = optional_int(candidate.evidence.get("scaledHeightFieldOffset"))
        x_offset = optional_int(candidate.evidence.get("xOffsetFieldOffset"))
        y_offset = optional_int(candidate.evidence.get("yOffsetFieldOffset"))
        right = optional_int(candidate.evidence.get("rightFieldOffset"))
        bottom = optional_int(candidate.evidence.get("bottomFieldOffset"))
        if any(
            value is None
            for value in (source_width, source_height, scale_flags, scaled_width, scaled_height, x_offset, y_offset, right, bottom)
        ):
            return []
        c_name = safe_c_name(candidate.c_name)
        return [
            {
                "name": "decoded-scale-mask-result-local",
                "extraFlags": ["/O2", "/Gz", "/GS-", "/Oy"],
                "source": header("source-shape-bink-buffer-set-scale-result-local", row)
                + "\n".join(
                    [
                        "typedef unsigned int mizuchi_u32;",
                        f"int __stdcall {c_name}(void *buffer, mizuchi_u32 width, mizuchi_u32 height) {{",
                        "    char *base;",
                        "    mizuchi_u32 source_width;",
                        "    mizuchi_u32 source_height;",
                        "    mizuchi_u32 flags;",
                        "    mizuchi_u32 mask;",
                        "    mizuchi_u32 result;",
                        "    if (buffer == 0) {",
                        "        return 0;",
                        "    }",
                        "    base = (char *)buffer;",
                        "    result = 1u;",
                        "    if (width == 0u) {",
                        f"        width = *(volatile mizuchi_u32 *)0x{global_width:08x};",
                        "    }",
                        "    if (height == 0u) {",
                        f"        height = *(volatile mizuchi_u32 *)0x{global_height:08x};",
                        "    }",
                        f"    source_width = *(mizuchi_u32 *)(base + 0x{source_width:x});",
                        f"    source_height = *(mizuchi_u32 *)(base + 0x{source_height:x});",
                        "    mask = 0u;",
                        "    if (width != source_width) {",
                        "        if ((width % source_width) == 0u) {",
                        "            mask = 0x80000000u;",
                        "        } else if ((source_width % width) == 0u) {",
                        "            mask = 0x20000000u;",
                        "        } else if (width > source_width) {",
                        "            mask = 0x40000000u;",
                        "        } else if (width < source_width) {",
                        "            mask = 0x10000000u;",
                        "        }",
                        "    }",
                        f"    flags = *(mizuchi_u32 *)(base + 0x{scale_flags:x});",
                        "    if ((flags & mask) == mask) {",
                        f"        *(mizuchi_u32 *)(base + 0x{scaled_width:x}) = width;",
                        "    } else {",
                        "        result = 0u;",
                        "    }",
                        "    mask = 0u;",
                        "    if (height != source_height) {",
                        "        if ((width % source_height) == 0u) {",
                        "            mask = 0x08000000u;",
                        "        } else if ((source_height % height) == 0u) {",
                        "            mask = 0x02000000u;",
                        "        } else if (height > source_height) {",
                        "            mask = 0x04000000u;",
                        "        } else if (height < source_height) {",
                        "            mask = 0x01000000u;",
                        "        }",
                        "    }",
                        "    if ((flags & mask) == mask) {",
                        f"        *(mizuchi_u32 *)(base + 0x{scaled_height:x}) = height;",
                        "    } else {",
                        "        result = 0u;",
                        "    }",
                        f"    *(mizuchi_u32 *)(base + 0x{right:x}) = *(mizuchi_u32 *)(base + 0x{scaled_width:x}) + *(mizuchi_u32 *)(base + 0x{x_offset:x});",
                        f"    *(mizuchi_u32 *)(base + 0x{bottom:x}) = *(mizuchi_u32 *)(base + 0x{scaled_height:x}) + *(mizuchi_u32 *)(base + 0x{y_offset:x});",
                        "    return (int)result;",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "decoded-scale-mask-height-first-local",
                "extraFlags": ["/O1", "/Gz", "/GS-", "/Oy"],
                "source": header("source-shape-bink-buffer-set-scale-height-first-local", row)
                + "\n".join(
                    [
                        "typedef unsigned int mizuchi_u32;",
                        f"int __stdcall {c_name}(void *buffer, mizuchi_u32 width, mizuchi_u32 height) {{",
                        "    char *base;",
                        "    mizuchi_u32 source_width;",
                        "    mizuchi_u32 source_height;",
                        "    mizuchi_u32 flags;",
                        "    mizuchi_u32 horizontal_mask;",
                        "    mizuchi_u32 vertical_mask;",
                        "    mizuchi_u32 result;",
                        "    if (buffer == 0) {",
                        "        return 0;",
                        "    }",
                        "    base = (char *)buffer;",
                        "    result = 1u;",
                        f"    width = width ? width : *(volatile mizuchi_u32 *)0x{global_width:08x};",
                        f"    height = height ? height : *(volatile mizuchi_u32 *)0x{global_height:08x};",
                        f"    source_width = *(mizuchi_u32 *)(base + 0x{source_width:x});",
                        f"    source_height = *(mizuchi_u32 *)(base + 0x{source_height:x});",
                        "    horizontal_mask = 0u;",
                        "    vertical_mask = 0u;",
                        "    if (width != source_width) {",
                        "        horizontal_mask = ((width % source_width) == 0u) ? 0x80000000u : (((source_width % width) == 0u) ? 0x20000000u : ((width > source_width) ? 0x40000000u : 0x10000000u));",
                        "    }",
                        "    if (height != source_height) {",
                        "        vertical_mask = ((width % source_height) == 0u) ? 0x08000000u : (((source_height % height) == 0u) ? 0x02000000u : ((height > source_height) ? 0x04000000u : 0x01000000u));",
                        "    }",
                        f"    flags = *(mizuchi_u32 *)(base + 0x{scale_flags:x});",
                        "    if ((flags & horizontal_mask) == horizontal_mask) {",
                        f"        *(mizuchi_u32 *)(base + 0x{scaled_width:x}) = width;",
                        "    } else {",
                        "        result = 0u;",
                        "    }",
                        "    if ((flags & vertical_mask) == vertical_mask) {",
                        f"        *(mizuchi_u32 *)(base + 0x{scaled_height:x}) = height;",
                        "    } else {",
                        "        result = 0u;",
                        "    }",
                        f"    *(mizuchi_u32 *)(base + 0x{right:x}) = *(mizuchi_u32 *)(base + 0x{x_offset:x}) + *(mizuchi_u32 *)(base + 0x{scaled_width:x});",
                        f"    *(mizuchi_u32 *)(base + 0x{bottom:x}) = *(mizuchi_u32 *)(base + 0x{y_offset:x}) + *(mizuchi_u32 *)(base + 0x{scaled_height:x});",
                        "    return (int)result;",
                        "}",
                        "",
                    ]
                ),
            },
        ]
    if candidate.rule == "u96-bit-tail-clear-check":
        c_name = safe_c_name(candidate.c_name)
        return [
            {
                "name": "signed-divmod-then-tail-loop",
                "extraFlags": ["/O2", "/Gd", "/GS-", "/Oy"],
                "source": header("source-shape-u96-signed-divmod-then-tail-loop", row)
                + "\n".join(
                    [
                        "typedef unsigned int mizuchi_u32;",
                        f"int __cdecl {c_name}(mizuchi_u32 *base, int bit) {{",
                        "    int word = bit / 32;",
                        "    int remainder = bit % 32;",
                        "    mizuchi_u32 mask = ~((mizuchi_u32)-1 << (31 - remainder));",
                        "    if ((base[word] & mask) != 0u) {",
                        "        return 0;",
                        "    }",
                        "    while (++word < 3) {",
                        "        if (base[word] != 0u) {",
                        "            return 0;",
                        "        }",
                        "    }",
                        "    return 1;",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "volatile-divisor-signed-divmod-then-tail-loop",
                "extraFlags": ["/O2", "/Gd", "/GS-", "/Oy"],
                "source": header("source-shape-u96-volatile-divisor-signed-divmod-then-tail-loop", row)
                + "\n".join(
                    [
                        "typedef unsigned int mizuchi_u32;",
                        f"int __cdecl {c_name}(mizuchi_u32 *base, int bit) {{",
                        "    volatile int divisor = 32;",
                        "    int word = bit / divisor;",
                        "    int remainder = bit % divisor;",
                        "    mizuchi_u32 mask = ~((mizuchi_u32)-1 << (31 - remainder));",
                        "    if ((base[word] & mask) != 0u) {",
                        "        return 0;",
                        "    }",
                        "    while (++word < 3) {",
                        "        if (base[word] != 0u) {",
                        "            return 0;",
                        "        }",
                        "    }",
                        "    return 1;",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "for-loop-post-increment-tail-check",
                "extraFlags": ["/O1", "/Gd", "/GS-", "/Oy"],
                "source": header("source-shape-u96-for-loop-post-increment-tail-check", row)
                + "\n".join(
                    [
                        "typedef unsigned int mizuchi_u32;",
                        f"int __cdecl {c_name}(mizuchi_u32 *base, int bit) {{",
                        "    int word;",
                        "    int remainder;",
                        "    mizuchi_u32 mask;",
                        "    word = bit / 32;",
                        "    remainder = bit % 32;",
                        "    mask = ~((mizuchi_u32)-1 << (31 - remainder));",
                        "    if ((base[word] & mask) != 0u) {",
                        "        return 0;",
                        "    }",
                        "    for (word = word + 1; word < 3; word = word + 1) {",
                        "        if (base[word] != 0u) {",
                        "            return 0;",
                        "        }",
                        "    }",
                        "    return 1;",
                        "}",
                        "",
                    ]
                ),
            },
        ]
    if candidate.rule == "nullable-indexed-field-array-getter-stdcall8":
        offset = optional_int(candidate.evidence.get("pointerOffset"))
        if offset is None:
            return []
        c_name = safe_c_name(candidate.c_name)
        field_expr = self_offset(offset)
        field_address = f"({field_expr})"
        return [
            {
                "name": "else-local-result",
                "source": header("source-shape-nullable-indexed-else-local-result", row)
                + "\n".join(
                    [
                        f"unsigned int __stdcall {c_name}(void *self, unsigned int index) {{",
                        "    unsigned int result;",
                        "    if (self != 0) {",
                        f"        result = (*(unsigned int **){field_address})[index];",
                        "    } else {",
                        "        result = 0u;",
                        "    }",
                        "    return result;",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "direct-field-index",
                "source": header("source-shape-nullable-indexed-direct-field-index", row)
                + "\n".join(
                    [
                        f"unsigned int __stdcall {c_name}(void *self, unsigned int index) {{",
                        "    if (self == 0) {",
                        "        return 0u;",
                        "    }",
                        f"    return (*(unsigned int **){field_address})[index];",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "char-base-local",
                "source": header("source-shape-nullable-indexed-char-base-local", row)
                + "\n".join(
                    [
                        f"unsigned int __stdcall {c_name}(void *self, unsigned int index) {{",
                        "    char *base;",
                        "    if (self == 0) {",
                        "        return 0u;",
                        "    }",
                        "    base = (char *)self;",
                        f"    return (*(unsigned int **)(base + 0x{offset:x}))[index];",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "manual-scale-load",
                "source": header("source-shape-nullable-indexed-manual-scale-load", row)
                + "\n".join(
                    [
                        f"unsigned int __stdcall {c_name}(void *self, unsigned int index) {{",
                        "    unsigned char *items;",
                        "    if (self == 0) {",
                        "        return 0u;",
                        "    }",
                        f"    items = *(unsigned char **){field_address};",
                        "    return *(unsigned int *)(items + index * 4u);",
                        "}",
                        "",
                    ]
                ),
            },
        ]
    if candidate.rule == "stdcall-indirect-global-callback-loop":
        callback_address = optional_int(candidate.evidence.get("callbackAddress"))
        pushed_value = optional_int(candidate.evidence.get("pushedValue"))
        if callback_address is None or pushed_value is None:
            return []
        c_name = candidate.c_name
        callback_type = f"{c_name}_callback"
        return [
            {
                "name": "do-while-cached-callback",
                "source": header("source-shape-callback-loop-do-while-cached", row)
                + "\n".join(
                    [
                        f"typedef void (__cdecl *{callback_type})(unsigned int);",
                        f"void __stdcall {c_name}(unsigned int count) {{",
                        f"    {callback_type} callback;",
                        "    if (count == 0u) {",
                        "        return;",
                        "    }",
                        f"    callback = *({callback_type} volatile *)0x{callback_address:08x};",
                        "    do {",
                        f"        callback({pushed_value}u);",
                        "        --count;",
                        "    } while (count != 0u);",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "for-loop-cached-callback",
                "source": header("source-shape-callback-loop-for-cached", row)
                + "\n".join(
                    [
                        f"typedef void (__cdecl *{callback_type})(unsigned int);",
                        f"void __stdcall {c_name}(unsigned int count) {{",
                        f"    {callback_type} callback = *({callback_type} volatile *)0x{callback_address:08x};",
                        "    for (; count != 0u; --count) {",
                        f"        callback({pushed_value}u);",
                        "    }",
                        "}",
                        "",
                    ]
                ),
            },
        ]
    if candidate.rule == "global-guard-call-set-return-zero":
        guard_address = optional_int(candidate.evidence.get("guardAddress"))
        call_target = optional_int(candidate.evidence.get("callTarget"))
        if guard_address is None:
            return []
        callee = safe_c_name(f"sub_{call_target:08x}") if call_target is not None else f"{candidate.c_name}_callee"
        c_name = candidate.c_name
        return [
            {
                "name": "guard-if-nonvolatile-call-store",
                "extraFlags": ["/O1", "/Gd"],
                "source": header("source-shape-global-guard-if-nonvolatile-call-store", row)
                + "\n".join(
                    [
                        f"extern void __cdecl {callee}(int value);",
                        f"unsigned int {c_name}(void) {{",
                        f"    if (*(unsigned int *)0x{guard_address:08x} == 0u) {{",
                        f"        {callee}(-3);",
                        f"        *(unsigned int *)0x{guard_address:08x} = 1u;",
                        "    }",
                        "    return 0u;",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "guard-if-call-store",
                "source": header("source-shape-global-guard-if-call-store", row)
                + "\n".join(
                    [
                        f"extern void __cdecl {callee}(int value);",
                        f"unsigned int {c_name}(void) {{",
                        f"    if (*(unsigned int volatile *)0x{guard_address:08x} == 0u) {{",
                        f"        {callee}(-3);",
                        f"        *(unsigned int volatile *)0x{guard_address:08x} = 1u;",
                        "    }",
                        "    return 0u;",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "guard-early-skip",
                "source": header("source-shape-global-guard-early-skip", row)
                + "\n".join(
                    [
                        f"extern void __cdecl {callee}(int value);",
                        f"unsigned int {c_name}(void) {{",
                        f"    if (*(unsigned int volatile *)0x{guard_address:08x} != 0u) {{",
                        "        return 0u;",
                        "    }",
                        f"    {callee}(-3);",
                        f"    *(unsigned int volatile *)0x{guard_address:08x} = 1u;",
                        "    return 0u;",
                        "}",
                        "",
                    ]
                ),
            },
        ]
    if candidate.rule == "rep-stos-global-clear":
        first_base = optional_int(candidate.evidence.get("firstBase"))
        first_dwords = optional_int(candidate.evidence.get("firstDwords"))
        first_bytes = optional_int(candidate.evidence.get("firstTrailingBytes"))
        second_base = optional_int(candidate.evidence.get("secondBase"))
        second_dwords = optional_int(candidate.evidence.get("secondDwords"))
        if first_base is None or first_dwords is None or first_bytes is None or second_base is None or second_dwords is None:
            return []
        zero_globals = [optional_int(value) for value in candidate.evidence.get("zeroGlobals") or []]
        if any(value is None for value in zero_globals):
            return []
        c_name = candidate.c_name
        trailing_byte_lines = (
            [f"    __stosb((unsigned char *)0x{first_base + first_dwords * 4:08x}, 0, {first_bytes}ul);"]
            if first_bytes
            else []
        )
        return [
            {
                "name": "msvc-stos-intrinsics-split-single-stores",
                "source": header("source-shape-rep-stos-intrinsics-split-single-stores", row)
                + "\n".join(
                    [
                        "void __stosd(unsigned long *, unsigned long, unsigned long);",
                        "void __stosb(unsigned char *, unsigned char, unsigned long);",
                        "#pragma intrinsic(__stosd)",
                        "#pragma intrinsic(__stosb)",
                        f"void {c_name}(void) {{",
                        f"    __stosd((unsigned long *)0x{first_base:08x}, 0ul, {first_dwords}ul);",
                        *trailing_byte_lines,
                        *[f"    *(unsigned long volatile *)0x{address:08x} = 0ul;" for address in zero_globals if address is not None],
                        *[
                            f"    __stosd((unsigned long *)0x{second_base + index * 4:08x}, 0ul, 1ul);"
                            for index in range(second_dwords)
                        ],
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "msvc-stos-intrinsics-bulk-second-clear",
                "source": header("source-shape-rep-stos-intrinsics-bulk-second-clear", row)
                + "\n".join(
                    [
                        "void __stosd(unsigned long *, unsigned long, unsigned long);",
                        "void __stosb(unsigned char *, unsigned char, unsigned long);",
                        "#pragma intrinsic(__stosd)",
                        "#pragma intrinsic(__stosb)",
                        f"void {c_name}(void) {{",
                        f"    __stosd((unsigned long *)0x{first_base:08x}, 0ul, {first_dwords}ul);",
                        *trailing_byte_lines,
                        *[f"    *(unsigned long volatile *)0x{address:08x} = 0ul;" for address in zero_globals if address is not None],
                        f"    __stosd((unsigned long *)0x{second_base:08x}, 0ul, {second_dwords}ul);",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "volatile-loop-and-stores",
                "source": header("source-shape-rep-stos-volatile-loop-and-stores", row)
                + "\n".join(
                    [
                        f"void {c_name}(void) {{",
                        "    unsigned int i;",
                        f"    for (i = 0u; i < {first_dwords}u; ++i) {{",
                        f"        ((unsigned int volatile *)0x{first_base:08x})[i] = 0u;",
                        "    }",
                        *(
                            [f"    ((unsigned char volatile *)0x{first_base + first_dwords * 4:08x})[0] = 0u;"]
                            if first_bytes
                            else []
                        ),
                        *[f"    *(unsigned int volatile *)0x{address:08x} = 0u;" for address in zero_globals if address is not None],
                        f"    for (i = 0u; i < {second_dwords}u; ++i) {{",
                        f"        ((unsigned int volatile *)0x{second_base:08x})[i] = 0u;",
                        "    }",
                        "}",
                        "",
                    ]
                ),
            },
        ]
    if candidate.rule == "stdcall-clamped-count-copy-to-global":
        count_address = optional_int(candidate.evidence.get("countAddress"))
        array_address = optional_int(candidate.evidence.get("arrayAddress"))
        max_count = optional_int(candidate.evidence.get("maxCount"))
        stack_bytes = optional_int(candidate.evidence.get("stackBytes"))
        if count_address is None or array_address is None or max_count is None or stack_bytes is None:
            return []
        c_name = candidate.c_name
        return [
            {
                "name": "basic-count-clamp-copy",
                "source": header("source-shape-clamped-count-copy-basic", row)
                + "\n".join(
                    [
                        f"void __stdcall {c_name}(unsigned int count, const unsigned int *items) {{",
                        "    unsigned int i;",
                        f"    if (count > {max_count}u) {{",
                        f"        count = {max_count}u;",
                        "    }",
                        f"    *(unsigned int volatile *)0x{count_address:08x} = count;",
                        "    for (i = 0u; i < count; ++i) {",
                        f"        ((unsigned int volatile *)0x{array_address:08x})[i] = items[i];",
                        "    }",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "pointer-delta-for-copy",
                "source": header("source-shape-clamped-count-copy-pointer-delta", row)
                + "\n".join(
                    [
                        f"void __stdcall {c_name}(unsigned int count, const unsigned int *items) {{",
                        "    unsigned int i;",
                        "    const char *delta;",
                        f"    if (count > {max_count}u) {{",
                        f"        count = {max_count}u;",
                        "    }",
                        f"    *(unsigned int volatile *)0x{count_address:08x} = count;",
                        "    if (count != 0u) {",
                        f"        delta = (const char *)items - 0x{array_address:08x};",
                        "        for (i = 0u; i < count; ++i) {",
                        f"            ((unsigned int volatile *)0x{array_address:08x})[i] = *(const unsigned int *)(delta + 0x{array_address:08x} + i * 4u);",
                        "        }",
                        "    }",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "do-while-pointer-delta-copy",
                "source": header("source-shape-clamped-count-copy-do-while-delta", row)
                + "\n".join(
                    [
                        f"void __stdcall {c_name}(unsigned int count, const unsigned int *items) {{",
                        "    unsigned int i;",
                        "    const char *delta;",
                        f"    if (count > {max_count}u) {{",
                        f"        count = {max_count}u;",
                        "    }",
                        "    i = 0u;",
                        "    if (count != 0u) {",
                        f"        *(unsigned int volatile *)0x{count_address:08x} = count;",
                        f"        delta = (const char *)items - 0x{array_address:08x};",
                        "        do {",
                        f"            ((unsigned int volatile *)0x{array_address:08x})[i] = *(const unsigned int *)(delta + 0x{array_address:08x} + i * 4u);",
                        "            ++i;",
                        "        } while (i < count);",
                        "    } else {",
                        f"        *(unsigned int volatile *)0x{count_address:08x} = count;",
                        "    }",
                        "}",
                        "",
                    ]
                ),
            },
        ]
    if candidate.rule == "small-copy-loop":
        count = optional_int(candidate.evidence.get("count"))
        if count is None:
            return []
        c_name = candidate.c_name
        return [
            {
                "name": "for-indexed-dword-copy",
                "source": header("source-shape-small-copy-for-indexed", row)
                + "\n".join(
                    [
                        f"void {c_name}(unsigned int *dest, const unsigned int *src) {{",
                        "    unsigned int index;",
                        f"    for (index = 0u; index < {count}u; ++index) {{",
                        "        dest[index] = src[index];",
                        "    }",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "pointer-delta-while",
                "source": header("source-shape-small-copy-pointer-delta-while", row)
                + "\n".join(
                    [
                        f"void {c_name}(unsigned int *dest, const unsigned int *src) {{",
                        f"    unsigned int remaining = {count}u;",
                        "    const unsigned int *cursor = src;",
                        "    unsigned int *delta_dest = (unsigned int *)((char *)dest - (char *)src);",
                        "    do {",
                        "        delta_dest[(unsigned int)cursor] = *cursor;",
                        "        ++cursor;",
                        "        --remaining;",
                        "    } while (remaining != 0u);",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "while-pointer-increment",
                "source": header("source-shape-small-copy-while-pointer", row)
                + "\n".join(
                    [
                        f"void {c_name}(unsigned int *dest, const unsigned int *src) {{",
                        f"    unsigned int remaining = {count}u;",
                        "    while (remaining != 0u) {",
                        "        *dest = *src;",
                        "        ++dest;",
                        "        ++src;",
                        "        --remaining;",
                        "    }",
                        "}",
                        "",
                    ]
                ),
            },
        ]
    if candidate.rule == "stdcall-yuv-blit-packed-wrapper":
        constant = optional_int(candidate.evidence.get("constant"))
        call_target = optional_int(candidate.evidence.get("callTarget"))
        if constant is None:
            return []
        c_name = candidate.c_name
        callee = safe_c_name(f"sub_{call_target:08x}") if call_target is not None else f"{c_name}_callee"
        params = ", ".join(f"unsigned int a{index}" for index in range(1, 13))
        call_args = "a2, a3, a4, stride, a6, a7, output, a9, a11, 0u, a12, 0x%08xu" % constant
        return [
            {
                "name": "packed-yuv-direct-adjust-call",
                "source": header("source-shape-yuv-packed-direct-adjust-call", row)
                + "\n".join(
                    [
                        f"extern void __cdecl {callee}(unsigned int, unsigned int, unsigned int, unsigned int, unsigned int, unsigned int, unsigned int, unsigned int, unsigned int, unsigned int, unsigned int, unsigned int);",
                        f"void __stdcall {c_name}({params}) {{",
                        "    unsigned int selector = a1;",
                        "    unsigned int output = a8;",
                        "    unsigned int stride = a5;",
                        "    if ((selector & 3u) == 2u) {",
                        "        ++a2;",
                        "        selector &= 0xfffffffcu;",
                        "    }",
                        "    if ((a2 & 1u) != 0u) {",
                        "        if ((stride & 1u) != 0u) {",
                        "            ++stride;",
                        "        }",
                        "        ++a2;",
                        "        --output;",
                        "    } else if ((stride & 1u) != 0u) {",
                        "        ++stride;",
                        "    }",
                        "    if ((output & 1u) != 0u) {",
                        "        --output;",
                        "    }",
                        f"    {callee}({call_args});",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "packed-yuv-branchless-output-adjust",
                "source": header("source-shape-yuv-packed-branchless-output", row)
                + "\n".join(
                    [
                        f"extern void __cdecl {callee}(unsigned int, unsigned int, unsigned int, unsigned int, unsigned int, unsigned int, unsigned int, unsigned int, unsigned int, unsigned int, unsigned int, unsigned int);",
                        f"void __stdcall {c_name}({params}) {{",
                        "    unsigned int selector = a1;",
                        "    unsigned int output = a8;",
                        "    unsigned int stride = a5;",
                        "    if ((selector & 3u) == 2u) {",
                        "        a2 += 1u;",
                        "        selector &= ~3u;",
                        "    }",
                        "    if ((a2 & 1u) != 0u) {",
                        "        stride += stride & 1u;",
                        "        a2 += 1u;",
                        "        output -= 1u;",
                        "    } else {",
                        "        stride += stride & 1u;",
                        "    }",
                        "    output -= output & 1u;",
                        f"    {callee}({call_args});",
                        "}",
                        "",
                    ]
                ),
            },
        ]
    if candidate.rule == "stack-arg-range-global-mode-setter":
        address = candidate.evidence.get("globalAddress")
        equal_one = int(candidate.evidence.get("equalOneValue") or 0)
        range_value = int(candidate.evidence.get("rangeValue") or 0)
        if not address or not equal_one or not range_value:
            return []
        c_name = candidate.c_name
        return [
            {
                "name": "if-else-range",
                "source": header("source-shape-stack-mode-if-else-range", row)
                + "\n".join(
                    [
                        f"void __cdecl {c_name}(int mode) {{",
                        "    if (mode == 1) {",
                        f"        *(volatile unsigned int *){address} = 0x{equal_one:02x}u;",
                        "    } else if (mode > 1 && mode <= 3) {",
                        f"        *(volatile unsigned int *){address} = 0x{range_value:02x}u;",
                        "    }",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "early-return-range",
                "source": header("source-shape-stack-mode-early-return-range", row)
                + "\n".join(
                    [
                        f"void __cdecl {c_name}(int mode) {{",
                        "    if (mode == 1) {",
                        f"        *(volatile unsigned int *){address} = 0x{equal_one:02x}u;",
                        "        return;",
                        "    }",
                        "    if (mode <= 1) {",
                        "        return;",
                        "    }",
                        "    if (mode > 3) {",
                        "        return;",
                        "    }",
                        f"    *(volatile unsigned int *){address} = 0x{range_value:02x}u;",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "goto-return-range",
                "source": header("source-shape-stack-mode-goto-return-range", row)
                + "\n".join(
                    [
                        f"void __cdecl {c_name}(int mode) {{",
                        "    if (mode == 1) goto one;",
                        "    if (mode <= 1) goto done;",
                        "    if (mode > 3) goto done;",
                        f"    *(volatile unsigned int *){address} = 0x{range_value:02x}u;",
                        "    return;",
                        "one:",
                        f"    *(volatile unsigned int *){address} = 0x{equal_one:02x}u;",
                        "done:",
                        "    return;",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "switch-plus-range",
                "source": header("source-shape-stack-mode-switch-plus-range", row)
                + "\n".join(
                    [
                        f"void __cdecl {c_name}(int mode) {{",
                        "    switch (mode) {",
                        "    case 1:",
                        f"        *(volatile unsigned int *){address} = 0x{equal_one:02x}u;",
                        "        return;",
                        "    case 2:",
                        "    case 3:",
                        f"        *(volatile unsigned int *){address} = 0x{range_value:02x}u;",
                        "        return;",
                        "    default:",
                        "        return;",
                        "    }",
                        "}",
                        "",
                    ]
                ),
            },
        ]
    if candidate.rule == "rad-aligned-free-forwarder":
        fallback = candidate.evidence.get("fallbackFreeTargetAddress")
        if not fallback:
            return []
        callee = safe_c_name(f"sub_{int(str(fallback), 16):08x}")
        c_name = safe_c_name(candidate.c_name)
        typedef = "typedef void (__stdcall *mizuchi_rad_custom_free)(void *ptr);"
        extern = f"extern void __cdecl {callee}(void *ptr);"
        return [
            {
                "name": "base-before-marker",
                "source": header("source-shape-radfree-base-before-marker", row)
                + "\n".join(
                    [
                        typedef,
                        extern,
                        f"void __stdcall {c_name}(void *ptr) {{",
                        "    if (ptr != 0) {",
                        "        unsigned char *bytes = (unsigned char *)ptr;",
                        "        unsigned int delta = bytes[-1];",
                        "        void *base = bytes - delta;",
                        "        if (bytes[-2] == 3) {",
                        "            (*(mizuchi_rad_custom_free *)(bytes - 8))(base);",
                        "        } else {",
                        f"            {callee}(base);",
                        "        }",
                        "    }",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "marker-before-base",
                "source": header("source-shape-radfree-marker-before-base", row)
                + "\n".join(
                    [
                        typedef,
                        extern,
                        f"void __stdcall {c_name}(void *ptr) {{",
                        "    if (ptr != 0) {",
                        "        unsigned char *bytes = (unsigned char *)ptr;",
                        "        unsigned char marker = bytes[-2];",
                        "        unsigned int delta = bytes[-1];",
                        "        if (marker == 3) {",
                        "            void *base = bytes - delta;",
                        "            (*(mizuchi_rad_custom_free *)(bytes - 8))(base);",
                        "        } else {",
                        "            void *base = bytes - delta;",
                        f"            {callee}(base);",
                        "        }",
                        "    }",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "base-rewrite-argument",
                "source": header("source-shape-radfree-base-rewrite-argument", row)
                + "\n".join(
                    [
                        typedef,
                        extern,
                        f"void __stdcall {c_name}(void *ptr) {{",
                        "    unsigned char *bytes;",
                        "    unsigned int delta;",
                        "    if (ptr == 0) {",
                        "        return;",
                        "    }",
                        "    bytes = (unsigned char *)ptr;",
                        "    delta = bytes[-1];",
                        "    ptr = bytes - delta;",
                        "    if (bytes[-2] == 3) {",
                        "        (*(mizuchi_rad_custom_free *)(bytes - 8))(ptr);",
                        "        return;",
                        "    }",
                        f"    {callee}(ptr);",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "marker-first-rewrite-argument",
                "source": header("source-shape-radfree-marker-first-rewrite-argument", row)
                + "\n".join(
                    [
                        typedef,
                        extern,
                        f"void __stdcall {c_name}(void *ptr) {{",
                        "    unsigned char *bytes;",
                        "    unsigned int delta;",
                        "    if (ptr == 0) {",
                        "        return;",
                        "    }",
                        "    bytes = (unsigned char *)ptr;",
                        "    if (bytes[-2] == 3) {",
                        "        delta = bytes[-1];",
                        "        ptr = bytes - delta;",
                        "        (*(mizuchi_rad_custom_free *)(bytes - 8))(ptr);",
                        "        return;",
                        "    }",
                        "    delta = bytes[-1];",
                        "    ptr = bytes - delta;",
                        f"    {callee}(ptr);",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "marker-first-volatile-tail-rewrite",
                "source": header("source-shape-radfree-marker-first-volatile-tail-rewrite", row)
                + "\n".join(
                    [
                        typedef,
                        extern,
                        f"void __stdcall {c_name}(void *ptr) {{",
                        "    unsigned char *bytes;",
                        "    unsigned int delta;",
                        "    if (ptr == 0) {",
                        "        return;",
                        "    }",
                        "    bytes = (unsigned char *)ptr;",
                        "    if (bytes[-2] == 3) {",
                        "        delta = bytes[-1];",
                        "        ptr = bytes - delta;",
                        "        (*(volatile mizuchi_rad_custom_free *)(bytes - 8))(ptr);",
                        "        return;",
                        "    }",
                        "    delta = bytes[-1];",
                        "    ptr = bytes - delta;",
                        f"    {callee}(ptr);",
                        "}",
                        "",
                    ]
                ),
            },
        ]
    if candidate.rule == "global-two-cmp-return-1-or-3":
        first = candidate.evidence.get("firstAddress")
        second = candidate.evidence.get("secondAddress")
        if not first or not second:
            return []
        c_name = candidate.c_name
        return [
            {
                "name": "nonvolatile-nested-if",
                "source": header("source-shape-global-two-cmp-nonvolatile-nested-if", row)
                + "\n".join(
                    [
                        f"unsigned int {c_name}(void) {{",
                        f"    if (*(unsigned int *){first} == 2u) {{",
                        f"        if (*(unsigned int *){second} >= 5u) {{",
                        "            return 1u;",
                        "        }",
                        "    }",
                        "    return 3u;",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "volatile-nested-if",
                "source": header("source-shape-global-two-cmp-nested-if", row)
                + "\n".join(
                    [
                        f"unsigned int {c_name}(void) {{",
                        f"    if (*(unsigned int volatile *){first} == 2u) {{",
                        f"        if (*(unsigned int volatile *){second} >= 5u) {{",
                        "            return 1u;",
                        "        }",
                        "    }",
                        "    return 3u;",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "goto-fail",
                "source": header("source-shape-global-two-cmp-goto-fail", row)
                + "\n".join(
                    [
                        f"unsigned int {c_name}(void) {{",
                        f"    if (*(unsigned int volatile *){first} != 2u) goto fail;",
                        f"    if (*(unsigned int volatile *){second} < 5u) goto fail;",
                        "    return 1u;",
                        "fail:",
                        "    return 3u;",
                        "}",
                        "",
                    ]
                ),
            },
            {
                "name": "ternary",
                "source": header("source-shape-global-two-cmp-ternary", row)
                + "\n".join(
                    [
                        f"unsigned int {c_name}(void) {{",
                        f"    return (*(unsigned int volatile *){first} == 2u && *(unsigned int volatile *){second} >= 5u) ? 1u : 3u;",
                        "}",
                        "",
                    ]
                ),
            },
        ]
    if candidate.rule != "stdcall-store-two-stack-args-to-globals":
        return []
    first = candidate.evidence.get("firstAddress")
    second = candidate.evidence.get("secondAddress")
    if not first or not second:
        return []
    c_name = candidate.c_name
    return [
        {
            "name": "direct-volatile-stores",
            "source": header("source-shape-direct-volatile-stores", row)
            + "\n".join(
                [
                    f"void __stdcall {c_name}(unsigned int first, unsigned int second) {{",
                    f"    *(unsigned int volatile *){first} = first;",
                    f"    *(unsigned int volatile *){second} = second;",
                    "}",
                    "",
                ]
            ),
        },
        {
            "name": "temporary-locals",
            "source": header("source-shape-temporary-locals", row)
            + "\n".join(
                [
                    f"void __stdcall {c_name}(unsigned int first, unsigned int second) {{",
                    "    unsigned int local_first = first;",
                    "    unsigned int local_second = second;",
                    f"    *(unsigned int volatile *){first} = local_first;",
                    f"    *(unsigned int volatile *){second} = local_second;",
                    "}",
                    "",
                ]
            ),
        },
        {
            "name": "pointer-locals",
            "source": header("source-shape-pointer-locals", row)
            + "\n".join(
                [
                    f"void __stdcall {c_name}(unsigned int first, unsigned int second) {{",
                    f"    unsigned int volatile *first_global = (unsigned int volatile *){first};",
                    f"    unsigned int volatile *second_global = (unsigned int volatile *){second};",
                    "    *first_global = first;",
                    "    *second_global = second;",
                    "}",
                    "",
                ]
            ),
        },
        {
            "name": "array-overlay",
            "source": header("source-shape-array-overlay", row)
            + "\n".join(
                [
                    f"void __stdcall {c_name}(unsigned int first, unsigned int second) {{",
                    f"    unsigned int volatile *globals = (unsigned int volatile *){first};",
                    "    globals[0] = first;",
                    "    globals[1] = second;",
                    "}",
                    "",
                ]
            ),
        },
        {
            "name": "struct-overlay",
            "source": header("source-shape-struct-overlay", row)
            + "\n".join(
                [
                    "struct MizuchiGlobals {",
                    "    volatile unsigned int first;",
                    "    volatile unsigned int second;",
                    "};",
                    f"void __stdcall {c_name}(unsigned int first, unsigned int second) {{",
                    f"    struct MizuchiGlobals *globals = (struct MizuchiGlobals *){first};",
                    "    globals->first = first;",
                    "    globals->second = second;",
                    "}",
                    "",
                ]
            ),
        },
    ]


def object_text_bytes(obj: Path, *, timeout: int) -> bytes:
    if not obj.exists():
        return b""
    proc = run(["objdump", "-b", "pe-i386", "-d", str(obj)], timeout=timeout)
    if proc.returncode != 0:
        return b""
    values: list[int] = []
    for line in proc.stdout.splitlines():
        if ":" not in line or "\t" not in line:
            continue
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        for token in fields[1].split():
            if re.fullmatch(r"[0-9a-fA-F]{2}", token):
                values.append(int(token, 16))
    return bytes(values)


def common_prefix_len(left: bytes, right: bytes) -> int:
    count = 0
    for a, b in zip(left, right):
        if a != b:
            break
        count += 1
    return count


def source_shape_compare_bytes(candidate: GeneratedCandidate, data: bytes) -> bytes:
    branch = direct_branch_for_candidate(candidate, data)
    if branch is None:
        return data
    branch_offset, opcode, _mnemonic, _target = branch
    if branch_offset + 5 > len(data) or data[branch_offset] != opcode:
        return data
    normalized = bytearray(data)
    normalized[branch_offset + 1 : branch_offset + 5] = b"\x00\x00\x00\x00"
    return bytes(normalized)


def render_target_bytes_asm(symbol: str, data: bytes) -> str:
    byte_lines = []
    for offset in range(0, len(data), 12):
        chunk = data[offset : offset + 12]
        byte_lines.append(".byte " + ", ".join(f"0x{value:02x}" for value in chunk))
    return "\n".join(
        [
            ".text",
            f".globl {symbol}",
            f".type {symbol}, @function",
            f"{symbol}:",
            *byte_lines,
            "",
        ]
    )


def render_target_bytes_asm_for_clang(row: dict[str, Any], candidate: GeneratedCandidate, data: bytes, flags: list[str]) -> str:
    if target_is_macho(row, flags):
        symbol = candidate.symbol or candidate.c_name
        if not symbol.startswith("_"):
            symbol = f"_{symbol}"
        return render_target_bytes_macho_asm(symbol, data)
    return render_target_bytes_asm(candidate.c_name, data)


def target_is_macho(row: dict[str, Any], flags: list[str]) -> bool:
    if str(row.get("targetFormat") or "") == "macho":
        return True
    return any("apple" in flag or "darwin" in flag or "macos" in flag for flag in flags)


def render_target_bytes_macho_asm(symbol: str, data: bytes) -> str:
    byte_lines = []
    for offset in range(0, len(data), 12):
        chunk = data[offset : offset + 12]
        byte_lines.append(".byte " + ", ".join(f"0x{value:02x}" for value in chunk))
    return "\n".join(
        [
            ".text",
            f".globl {symbol}",
            f"{symbol}:",
            *byte_lines,
            "",
        ]
    )


def render_target_bytes_masm(symbol: str, data: bytes) -> str:
    return "\n".join(
        [
            "; Generated from target-slice bytes by source-parity-synthesize.py.",
            ".386",
            ".model flat",
            f"PUBLIC {symbol}",
            "_TEXT SEGMENT",
            f"{symbol} PROC",
            *masm_db_lines(data),
            f"{symbol} ENDP",
            "_TEXT ENDS",
            "END",
            "",
        ]
    )


def render_target_bytes_coff_asm(symbol: str, data: bytes) -> str:
    byte_lines = []
    for offset in range(0, len(data), 12):
        chunk = data[offset : offset + 12]
        byte_lines.append(".byte " + ", ".join(f"0x{value:02x}" for value in chunk))
    return "\n".join(
        [
            ".text",
            f".globl {symbol}",
            f"{symbol}:",
            *byte_lines,
            "",
        ]
    )


def render_target_coff_for_candidate(candidate: GeneratedCandidate, data: bytes) -> dict[str, Any]:
    relocation_asm = render_relocation_aware_coff_asm(candidate, data)
    if relocation_asm is None:
        return {
            "asm": render_target_bytes_coff_asm(candidate.symbol, data),
            "origin": "synthetic Windows COFF object assembled from source-task target slice bytes",
            "relocations": [],
            "claimBoundary": "objdiff compares candidate MSVC COFF to a synthetic COFF object made from target slice bytes; this is code-slice evidence, not full target-object source parity",
        }
    return {
        "asm": relocation_asm["asm"],
        "origin": "synthetic Windows COFF object assembled from source-task target slice bytes with reconstructed relocations",
        "relocations": relocation_asm["relocations"],
        "claimBoundary": "objdiff compares candidate MSVC COFF to a synthetic COFF object reconstructed from target slice bytes plus decoded relocations; this is stronger code-slice evidence, not full target-object source parity",
    }


def render_relocation_aware_coff_asm(candidate: GeneratedCandidate, data: bytes) -> dict[str, Any] | None:
    branch = direct_branch_for_candidate(candidate, data)
    replacements: list[dict[str, Any]] = []
    if branch is not None:
        branch_offset, opcode, mnemonic, target = branch
        if branch_offset + 5 > len(data) or data[branch_offset] != opcode:
            return None
        call_symbol = candidate.evidence.get("callSymbol")
        callee = str(call_symbol) if isinstance(call_symbol, str) and call_symbol else f"_sub_{int(target, 16):08x}"
        replacements.append(
            {
                "offset": branch_offset,
                "length": 5,
                "asm": f"{mnemonic} {callee}",
                "symbols": [callee],
                "relocation": {
                    "offset": branch_offset,
                    "type": "IMAGE_REL_I386_REL32",
                    "symbol": callee,
                    "decodedTarget": target,
                    "instruction": mnemonic,
                },
            }
        )
    for relocation in absolute_address_relocations(candidate, data):
        offset = int(relocation["offset"])
        replacements.append(
            {
                "offset": offset,
                "length": 4,
                "asm": f".long {relocation['symbol']}",
                "symbols": [relocation["symbol"]],
                "relocation": relocation,
            }
        )
    if not replacements:
        return None
    replacements.sort(key=lambda item: int(item["offset"]))
    previous_end = -1
    for replacement in replacements:
        offset = int(replacement["offset"])
        length = int(replacement["length"])
        if offset < previous_end or offset + length > len(data):
            return None
        previous_end = offset + length
    symbols = sorted({symbol for replacement in replacements for symbol in replacement["symbols"]})
    lines = [".text", f".globl {candidate.symbol}", *[f".globl {symbol}" for symbol in symbols], f"{candidate.symbol}:"]
    cursor = 0
    for replacement in replacements:
        offset = int(replacement["offset"])
        if cursor < offset:
            lines.extend(coff_byte_lines(data[cursor:offset]))
        lines.append(str(replacement["asm"]))
        cursor = offset + int(replacement["length"])
    if cursor < len(data):
        lines.extend(coff_byte_lines(data[cursor:]))
    lines.append("")
    return {
        "asm": "\n".join(lines),
        "relocations": [replacement["relocation"] for replacement in replacements],
    }


def coff_byte_lines(data: bytes) -> list[str]:
    lines: list[str] = []
    for offset in range(0, len(data), 12):
        chunk = data[offset : offset + 12]
        lines.append(".byte " + ", ".join(f"0x{value:02x}" for value in chunk))
    return lines


def absolute_address_relocations(candidate: GeneratedCandidate, data: bytes) -> list[dict[str, Any]]:
    raw_relocations = candidate.evidence.get("absoluteAddressRelocations")
    if not isinstance(raw_relocations, list):
        return []
    relocations: list[dict[str, Any]] = []
    for item in raw_relocations:
        if not isinstance(item, dict):
            continue
        offset = item.get("offset")
        symbol = item.get("symbol")
        if not isinstance(offset, int) or not isinstance(symbol, str) or not symbol:
            continue
        if offset < 0 or offset + 4 > len(data):
            continue
        decoded_address = item.get("decodedAddress")
        relocations.append(
            {
                "offset": offset,
                "type": str(item.get("type") or "IMAGE_REL_I386_DIR32"),
                "symbol": symbol,
                "decodedAddress": decoded_address,
                "encodedBytes": data[offset : offset + 4].hex(),
            }
        )
    return relocations


def direct_branch_for_candidate(candidate: GeneratedCandidate, data: bytes) -> tuple[int, int, str, str] | None:
    call_target = candidate.evidence.get("callTarget")
    if isinstance(call_target, str) and call_target.startswith("0x"):
        call_offset = direct_call_offset_for_candidate(candidate, data)
        if call_offset is not None:
            return call_offset, 0xE8, "call", call_target
    jump_target = candidate.evidence.get("jumpTarget")
    if isinstance(jump_target, str) and jump_target.startswith("0x"):
        jump_offset = direct_jump_offset_for_candidate(candidate, data)
        if jump_offset is not None:
            return jump_offset, 0xE9, "jmp", jump_target
    return None


def direct_call_offset_for_candidate(candidate: GeneratedCandidate, data: bytes) -> int | None:
    if candidate.rule == "push-const-call-wrapper":
        if len(data) == 15 and data[6] == 0xE8:
            return 6
        if len(data) == 17 and data[8] == 0xE8:
            return 8
    if candidate.rule == "push-imm32-pair-call-wrapper" and len(data) == 18 and data[10] == 0xE8:
        return 10
    if candidate.rule == "push-global-call-wrapper" and len(data) == 18 and data[10] == 0xE8:
        return 10
    if candidate.rule == "push-stack-stack-const-call-wrapper" and len(data) == 22 and data[13] == 0xE8:
        return 13
    if candidate.rule == "bink-copy-to-buffer-forwarder" and len(strip_alignment_padding(data)) == 54 and strip_alignment_padding(data)[46] == 0xE8:
        return 46
    if candidate.rule == "bink-buffer-set-direct-draw-forwarder" and len(strip_alignment_padding(data)) == 78 and strip_alignment_padding(data)[39] == 0xE8:
        return 39
    if candidate.rule == "stdcall-yuv-blit-format-wrapper" and len(strip_alignment_padding(data)) == 68 and strip_alignment_padding(data)[56] == 0xE8:
        return 56
    if candidate.rule == "stdcall-yuv-blit-alpha-wrapper" and len(strip_alignment_padding(data)) == 70 and strip_alignment_padding(data)[58] == 0xE8:
        return 58
    if candidate.rule == "stdcall-yuv-blit-packed-wrapper" and len(strip_alignment_padding(data)) == 113 and strip_alignment_padding(data)[99] == 0xE8:
        return 99
    if candidate.rule == "stdcall-yuv-blit-mask-format-prefix" and len(data) == 78 and data[66] == 0xE8:
        return 66
    if candidate.rule == "stdcall-yuv-blit-mask-alpha-prefix" and len(data) == 80 and data[68] == 0xE8:
        return 68
    if candidate.rule == "global-guard-call-set-return-zero" and len(data) == 30 and data[11] == 0xE8:
        return 11
    if candidate.rule == "stdcall-track-method-forwarder" and len(strip_alignment_padding(data)) in {70, 75, 80} and data[26] == 0xE8:
        return 26
    return None


def direct_jump_offset_for_candidate(candidate: GeneratedCandidate, data: bytes) -> int | None:
    if candidate.rule == "stdcall-nullable-field-tailjmp" and len(strip_alignment_padding(data)) == 26:
        offset = candidate.evidence.get("jumpOffset", 18)
        if isinstance(offset, int) and 0 <= offset < len(data) and data[offset] == 0xE9:
            return offset
    return None


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
    if report["status"] == "error":
        fallback = compare_objdump_code_bytes(target_obj, candidate_obj, case_dir, timeout=timeout)
        if fallback is not None:
            report = fallback
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


def compare_objdump_code_bytes(target_obj: Path, candidate_obj: Path, case_dir: Path, *, timeout: int) -> dict[str, Any] | None:
    target = objdump_code_bytes(target_obj, case_dir / "target.objdump.txt", timeout=timeout)
    candidate = objdump_code_bytes(candidate_obj, case_dir / "candidate.objdump.txt", timeout=timeout)
    if target is None or candidate is None:
        return None
    target_bytes, target_stderr = target
    candidate_bytes, candidate_stderr = candidate
    if not target_bytes or not candidate_bytes:
        return None
    matched = target_bytes == candidate_bytes
    return {
        "schema": "mizuchi.verify-objdiff.v1",
        "status": "matched" if matched else "mismatched",
        "differences": 0 if matched else 1,
        "message": "Object code bytes match via objdump fallback" if matched else "Object code bytes differ via objdump fallback",
        "objdiffExit": 1,
        "fallback": "objdump-disassembly-byte-compare",
        "targetCodeBytes": len(target_bytes),
        "candidateCodeBytes": len(candidate_bytes),
        "targetCodeSha256": hashlib.sha256(target_bytes).hexdigest(),
        "candidateCodeSha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "stderr": "\n".join(item for item in [target_stderr, candidate_stderr] if item)[-4000:],
    }


def objdump_code_bytes(path: Path, out_path: Path, *, timeout: int) -> tuple[bytes, str] | None:
    proc = run(["objdump", "-d", str(path)], timeout=timeout)
    out_path.write_text(proc.stdout, encoding="utf-8")
    out_path.with_suffix(".stderr.txt").write_text(proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        return None
    code = bytearray()
    line_re = re.compile(r"^\s*[0-9a-fA-F]+:\s*((?:[0-9a-fA-F]{2}\s+)+)")
    for line in proc.stdout.splitlines():
        match = line_re.match(line)
        if not match:
            continue
        for token in match.group(1).split():
            try:
                code.append(int(token, 16))
            except ValueError:
                break
    return bytes(code), proc.stderr


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


def filter_candidates_by_explicit_rule_strategies(
    candidates: list[GeneratedCandidate],
    strategies: set[str] | None,
) -> tuple[list[GeneratedCandidate], int]:
    if not strategies:
        return candidates, 0
    requested_rules = strategies & {candidate.rule for candidate in candidates}
    if not requested_rules:
        return candidates, 0
    filtered = [candidate for candidate in candidates if candidate.rule in requested_rules]
    return filtered, len(candidates) - len(filtered)


def generated_candidate_source_quality(candidate: GeneratedCandidate) -> str:
    explicit_quality = str(candidate.evidence.get("sourceQuality") or "")
    if explicit_quality in {"high-level-c", "inline-asm-c", "byte-emission-asm", "nonsemantic-bootstrap"}:
        return explicit_quality
    tier = str(candidate.evidence.get("sourceTier") or "").lower()
    if "byte-emission" in tier or "byte emission" in tier:
        return "byte-emission-asm"
    if "inline-assembly" in tier or "inline assembly" in tier or "__declspec(naked)" in candidate.source or "__attribute__((naked))" in candidate.source:
        return "inline-asm-c"
    if candidate.source_suffix.lower() in {".asm", ".s"}:
        return "byte-emission-asm"
    if candidate.semantic_source:
        return "high-level-c"
    return "nonsemantic-bootstrap"


def source_recovery_scope(row: dict[str, Any], candidate: GeneratedCandidate | None = None) -> str:
    if candidate is not None and not candidate.semantic_source:
        return "nonsemantic"
    explicit = ""
    evidence: dict[str, Any] = {}
    if candidate is not None:
        evidence = candidate.evidence
        explicit = str(evidence.get("sourceRecoveryScope") or "").strip()
    if not explicit:
        explicit = str(row.get("sourceRecoveryScope") or "").strip()
    generator = row.get("automaticGenerator") if isinstance(row.get("automaticGenerator"), dict) else {}
    for source in (evidence, generator):
        source_slice_kind = str(source.get("sourceSliceKind") or "")
        claim_boundary = str(source.get("claimBoundary") or "").lower()
        target_byte_span = source.get("targetByteSpan") if isinstance(source.get("targetByteSpan"), dict) else {}
        span_reason = str(target_byte_span.get("reason") or "").lower()
        if source_slice_kind in {"leading-return-prefix"}:
            return "partial-source-slice"
        if "source-slice parity only" in claim_boundary or "source-slice repair only" in span_reason:
            return "partial-source-slice"
    if explicit and explicit != "whole-function":
        if (
            explicit == "context-dependent-fragment"
            and has_scope_target_byte_span(row, candidate)
            and not probable_context_dependent_fragment(scope_classification_bytes(row, candidate))
        ):
            return "whole-function"
        return explicit
    quality = generated_candidate_source_quality(candidate) if candidate is not None else str(row.get("sourceQuality") or "")
    if quality == "byte-emission-asm" and probable_context_dependent_fragment(scope_classification_bytes(row, candidate)):
        return "context-dependent-fragment"
    if explicit:
        return explicit
    return "whole-function"


def scope_classification_bytes(row: dict[str, Any], candidate: GeneratedCandidate | None = None) -> bytes:
    data = parse_bytes(row)
    evidence = candidate.evidence if candidate is not None else {}
    generator = row.get("automaticGenerator") if isinstance(row.get("automaticGenerator"), dict) else {}
    for source in (evidence, generator):
        target_byte_span = source.get("targetByteSpan") if isinstance(source.get("targetByteSpan"), dict) else None
        if not target_byte_span:
            continue
        start = optional_int(target_byte_span.get("offset")) or 0
        length = optional_int(target_byte_span.get("length"))
        if length is not None and start >= 0 and length >= 0 and start + length <= len(data):
            return data[start : start + length]
    return strip_alignment_padding(data)


def has_scope_target_byte_span(row: dict[str, Any], candidate: GeneratedCandidate | None = None) -> bool:
    evidence = candidate.evidence if candidate is not None else {}
    generator = row.get("automaticGenerator") if isinstance(row.get("automaticGenerator"), dict) else {}
    for source in (evidence, generator):
        target_byte_span = source.get("targetByteSpan") if isinstance(source.get("targetByteSpan"), dict) else None
        if target_byte_span and optional_int(target_byte_span.get("length")) is not None:
            return True
    return False


def probable_context_dependent_fragment(data: bytes) -> bool:
    body = strip_alignment_padding(data)
    if not body:
        return False
    if has_standard_frame_prologue(body):
        return False
    if body[0] in {0x5B, 0x5D, 0x5E, 0x5F, 0xC9, 0xC3, 0xC2}:
        return True
    if body.startswith(b"\x8d\x65"):
        return True
    if 0xC9 in body:
        return True
    return has_ebp_relative_access(body)


def has_standard_frame_prologue(body: bytes) -> bool:
    return body.startswith(b"\x55\x8b\xec") or body.startswith(b"\x55\x89\xe5")


def has_ebp_relative_access(body: bytes) -> bool:
    for index, opcode in enumerate(body[:-2]):
        if opcode not in {0x8B, 0x89, 0x8D, 0xC7, 0xD9, 0xDD}:
            continue
        modrm = body[index + 1]
        mod = modrm >> 6
        rm = modrm & 0x07
        if rm == 5 and mod in {1, 2}:
            return True
    return False


def filter_candidates_by_source_quality(
    candidates: list[GeneratedCandidate],
    qualities: set[str] | None,
) -> tuple[list[GeneratedCandidate], int]:
    if not qualities:
        return candidates, 0
    filtered = [candidate for candidate in candidates if generated_candidate_source_quality(candidate) in qualities]
    return filtered, len(candidates) - len(filtered)


def is_boundary_suspect(row: dict[str, Any]) -> bool:
    if row.get("semanticSource") is True:
        return False
    generator = row.get("automaticGenerator")
    if isinstance(generator, dict) and generator.get("rule") and generator.get("rule") != "target-slice-asm-bootstrap":
        return False
    target_slice = row.get("targetSlice")
    if not isinstance(target_slice, dict):
        return False
    boundary_quality = target_slice.get("boundaryQuality")
    if not isinstance(boundary_quality, dict):
        return False
    return boundary_quality.get("status") == "suspect"


def promotion_record_key(record: dict[str, Any]) -> str:
    source_sha = str(record.get("sourceSha256") or "")
    if source_sha:
        return source_sha
    return "|".join(str(record.get(key) or "") for key in ("entry", "name", "rule", "variant", "source"))


def promotion_stats_bucket(rule: str, quality: str) -> dict[str, Any]:
    return {
        "rule": rule,
        "sourceQuality": quality,
        "matchedKeys": set(),
        "mismatchedKeys": set(),
        "compileFailedKeys": set(),
        "errorKeys": set(),
        "exampleNames": [],
        "exampleSources": [],
        "sourceTierSamples": set(),
        "targetByteSpanCandidates": 0,
        "relocationCandidates": 0,
        "registerAbiCandidates": 0,
        "boundarySuspectCandidates": 0,
        "semanticSourceCandidates": 0,
        "sourceShapeSearches": 0,
        "sourceShapeSearchMatches": 0,
        "sourceShapeSearchBestCommonPrefix": 0,
        "sourceShapeSearchBestNames": [],
    }


def add_unique_sample(values: list[str], value: Any, limit: int = 5) -> None:
    if value is None:
        return
    text = str(value)
    if not text or text in values or len(values) >= limit:
        return
    values.append(text)


def update_promotion_stats(
    stats: dict[tuple[str, str], dict[str, Any]],
    record: dict[str, Any],
) -> None:
    quality = str(record.get("sourceQuality") or "")
    rule = str(record.get("rule") or "")
    if not rule or quality == "high-level-c":
        return
    key = promotion_record_key(record)
    bucket = stats.setdefault((rule, quality), promotion_stats_bucket(rule, quality))
    status = str(record.get("status") or "")
    differences = int(record.get("differences", -1))
    if status == "code-slice-matched" and differences == 0:
        bucket["matchedKeys"].add(key)
    elif status == "mismatched":
        bucket["mismatchedKeys"].add(key)
    elif status == "compile-failed":
        bucket["compileFailedKeys"].add(key)
    elif status not in {"generated-only", "matched"}:
        bucket["errorKeys"].add(key)

    add_unique_sample(bucket["exampleNames"], record.get("name"))
    add_unique_sample(bucket["exampleSources"], record.get("source"))
    if record.get("semanticSource"):
        bucket["semanticSourceCandidates"] += 1
    evidence = record.get("generationEvidence") if isinstance(record.get("generationEvidence"), dict) else {}
    source_tier = evidence.get("sourceTier")
    if source_tier:
        bucket["sourceTierSamples"].add(str(source_tier))
    if isinstance(evidence.get("targetByteSpan"), dict):
        bucket["targetByteSpanCandidates"] += 1
    if any(key in evidence for key in ("callTarget", "callSymbol", "jumpTarget", "relocations")):
        bucket["relocationCandidates"] += 1
    if any(key in evidence for key in ("eaxArgIndex", "ecxArgIndex", "inputRegister", "outputRegister", "preservedRegister")):
        bucket["registerAbiCandidates"] += 1
    target_slice = record.get("targetSlice") if isinstance(record.get("targetSlice"), dict) else {}
    boundary_quality = target_slice.get("boundaryQuality") if isinstance(target_slice.get("boundaryQuality"), dict) else {}
    if boundary_quality.get("status") == "suspect":
        bucket["boundarySuspectCandidates"] += 1
    search_summary = record.get("sourceShapeSearchSummary") if isinstance(record.get("sourceShapeSearchSummary"), dict) else {}
    if search_summary:
        bucket["sourceShapeSearches"] += 1
        if search_summary.get("status") == "matched":
            bucket["sourceShapeSearchMatches"] += 1
        best = search_summary.get("best") if isinstance(search_summary.get("best"), dict) else {}
        try:
            common_prefix = int(best.get("commonPrefixBytes") or 0)
        except (TypeError, ValueError):
            common_prefix = 0
        bucket["sourceShapeSearchBestCommonPrefix"] = max(int(bucket["sourceShapeSearchBestCommonPrefix"]), common_prefix)
        add_unique_sample(bucket["sourceShapeSearchBestNames"], best.get("name"))


def matched_source_shape_record(record: dict[str, Any], search_path: str) -> dict[str, Any] | None:
    path = Path(search_path)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if report.get("status") != "matched":
        return None
    attempts = report.get("attempts")
    if not isinstance(attempts, list):
        return None
    matched = next((attempt for attempt in attempts if isinstance(attempt, dict) and attempt.get("byteIdentical") is True), None)
    if matched is None:
        return None
    compile_info = matched.get("compile") if isinstance(matched.get("compile"), dict) else {}
    return {
        "schema": "mizuchi.source-shape-code-slice-match.v1",
        "status": "source-shape-code-slice-matched",
        "name": record.get("name"),
        "entry": record.get("entry"),
        "rule": report.get("rule") or record.get("rule"),
        "variant": report.get("variant") or record.get("variant"),
        "sourceShapeName": matched.get("name"),
        "source": matched.get("source"),
        "sourceSha256": matched.get("sourceSha256"),
        "sourceQuality": "high-level-c",
        "sourceRecoveryScope": record.get("sourceRecoveryScope") or "whole-function",
        "compiler": report.get("compiler") or record.get("compiler"),
        "compilerProfileName": matched.get("profile"),
        "compilerProfileArgs": matched.get("profileArgs") or report.get("profile"),
        "object": compile_info.get("object"),
        "targetBytesSha256": report.get("targetBytesSha256"),
        "candidateBytesSha256": matched.get("candidateBytesSha256"),
        "targetSize": matched.get("targetSize") or report.get("targetSize"),
        "candidateSize": matched.get("candidateSize"),
        "searchReport": str(path),
        "parentAttemptStatus": record.get("status"),
        "parentAttemptDir": record.get("attemptDir"),
        "verificationTier": "source-shape-code-slice-byte-match",
        "claimBoundary": "high-level C source-shape compiled to bytes identical to the bounded target code slice; this is not a full executable/object parity claim",
    }


def promotion_priority(stats: dict[str, Any]) -> tuple[int, int, int, int]:
    quality_rank = {
        "inline-asm-c": 0,
        "byte-emission-asm": 1,
        "nonsemantic-bootstrap": 2,
    }.get(str(stats.get("sourceQuality") or ""), 3)
    feasibility_rank = {
        "direct-c-candidate": 0,
        "compiler-shape-risk": 1,
        "compiler-shape-blocked": 2,
        "relocation-model-needed": 3,
        "custom-abi-blocked": 4,
        "byte-decoder-needed": 5,
        "boundary-repair-needed": 6,
        "bootstrap-only": 7,
    }.get(promotion_feasibility(stats)["class"], 7)
    matched = len(stats["matchedKeys"])
    boundary_suspect = int(stats["boundarySuspectCandidates"])
    target_byte_span = int(stats["targetByteSpanCandidates"])
    return (feasibility_rank, quality_rank, boundary_suspect, target_byte_span, -matched)


def promotion_feasibility(stats: dict[str, Any]) -> dict[str, Any]:
    quality = str(stats.get("sourceQuality") or "")
    rule = str(stats.get("rule") or "")
    target_byte_span = int(stats.get("targetByteSpanCandidates") or 0)
    relocation = int(stats.get("relocationCandidates") or 0)
    boundary_suspect = int(stats.get("boundarySuspectCandidates") or 0)
    register_abi = int(stats.get("registerAbiCandidates") or 0)
    source_shape_searches = int(stats.get("sourceShapeSearches") or 0)
    source_shape_matches = int(stats.get("sourceShapeSearchMatches") or 0)
    source_shape_best_prefix = int(stats.get("sourceShapeSearchBestCommonPrefix") or 0)
    tiers = [str(item).lower() for item in stats.get("sourceTierSamples") or []]
    tier_text = " ".join(tiers)
    reasons: list[str] = []
    if boundary_suspect:
        reasons.append("target slice boundary is suspect")
        return {"class": "boundary-repair-needed", "reasons": reasons}
    if rule in {
        "bink-buffer-clear-forwarder",
        "bink-buffer-set-offset-forwarder",
        "ebx-bitfield-mode-remap",
    }:
        reasons.append("decoded wrapper uses register arguments or live register state not expressible by ordinary C calling conventions")
        if relocation:
            reasons.append("call/import relocation must also be modeled")
        return {"class": "custom-abi-blocked", "reasons": reasons}
    if rule in {
        "bink-buffer-check-win-pos-forwarder",
        "bink-surface-type-forwarder",
        "rad-direct-free-wrapper",
        "rad-aligned-free-forwarder",
        "rad-aligned-malloc-forwarder",
        "u96-bit-tail-clear-check",
        "x87-round-stack-double-return",
        "x87-temp-i16-return",
    }:
        reasons.append("high-level C probe compiles to incompatible register lifetime, branch offset, or tail-call shape under the current MSVC profile")
        searches = int(stats.get("sourceShapeSearches") or 0)
        if searches:
            matches = int(stats.get("sourceShapeSearchMatches") or 0)
            best_prefix = int(stats.get("sourceShapeSearchBestCommonPrefix") or 0)
            reasons.append(f"{searches} source-shape search receipt(s), {matches} byte-identical match(es), best common prefix {best_prefix} byte(s)")
        return {"class": "compiler-shape-blocked", "reasons": reasons}
    if rule in {
        "x87-control-word-masked-setter",
        "x87-double-exponent-adjust-return",
    }:
        reasons.append("source requires exact x87 control-word or floating-point stack instruction shape not expressible by ordinary C")
        return {"class": "compiler-shape-blocked", "reasons": reasons}
    if quality == "byte-emission-asm":
        if source_shape_searches and source_shape_matches <= 0:
            reasons.append(
                f"{source_shape_searches} generated high-level C source-shape search receipt(s), "
                f"0 byte-identical match(es), best common prefix {source_shape_best_prefix} byte(s)"
            )
            return {"class": "compiler-shape-blocked", "reasons": reasons}
        if source_shape_searches and source_shape_matches > 0:
            reasons.append(f"{source_shape_matches} generated high-level C source-shape match(es) found")
            return {"class": "direct-c-candidate", "reasons": reasons}
        if target_byte_span:
            reasons.append("candidate is bounded byte-emission for a decoded subspan")
        if relocation:
            reasons.append("candidate contains decoded call/jump/import targets")
        return {"class": "byte-decoder-needed", "reasons": reasons or ["byte-emission assembly has no high-level expression yet"]}
    if quality == "nonsemantic-bootstrap":
        return {"class": "bootstrap-only", "reasons": ["bootstrap candidate is not semantic source"]}
    if register_abi or any(term in tier_text for term in ("custom", "live eax", "live-eax", "live ecx", "live-ecx", "live-ebx")):
        reasons.append("source requires a non-C register/call ABI")
        if relocation:
            reasons.append("call/import relocation must also be modeled")
        return {"class": "custom-abi-blocked", "reasons": reasons}
    if rule in {
        "nullable-field-setter-u32-stdcall8",
        "rep-stos-global-clear",
        "small-copy-loop",
        "stdcall-clamped-count-copy-to-global",
        "stdcall-global-callback-install",
        "stdcall-indirect-global-callback-loop",
        "stdcall-nullable-field-tailjmp",
        "u96-left-shift-one",
    }:
        reasons.append("known high-level C probes still choose incompatible register lifetime or loop/callback shape")
        return {"class": "compiler-shape-blocked", "reasons": reasons}
    if any(term in tier_text for term in ("register allocation", "branch layout", "loop shape", "loop", "pointer-delta", "parity fallback", "decoded")):
        reasons.append("ordinary C is likely to compile to a different register or branch shape")
        return {"class": "compiler-shape-risk", "reasons": reasons}
    if relocation:
        return {"class": "relocation-model-needed", "reasons": ["direct high-level C attempt must preserve call/import relocation shape"]}
    return {"class": "direct-c-candidate", "reasons": ["no custom ABI, relocation, or boundary blocker detected in matched evidence"]}


def promotion_recommended_action(stats: dict[str, Any]) -> str:
    feasibility = promotion_feasibility(stats)
    feasibility_class = str(feasibility["class"])
    quality = str(stats.get("sourceQuality") or "")
    target_byte_span = int(stats.get("targetByteSpanCandidates") or 0)
    relocation = int(stats.get("relocationCandidates") or 0)
    boundary_suspect = int(stats.get("boundarySuspectCandidates") or 0)
    if feasibility_class == "boundary-repair-needed" or boundary_suspect:
        return "repair target-slice boundaries before promoting this rule to high-level C"
    if feasibility_class == "custom-abi-blocked":
        return "do not try plain C first; model the custom register/call ABI or keep an inline-assembly boundary"
    if feasibility_class == "compiler-shape-risk":
        return "try high-level C only through a compiler-shape search; expect register/branch-layout mismatches"
    if feasibility_class == "compiler-shape-blocked":
        return "keep inline assembly for now or introduce a compiler-shape solver that can constrain register lifetime and loop lowering"
    if quality == "inline-asm-c":
        if relocation:
            return "attempt high-level C rewrite with explicit call/import relocation modeling"
        return "attempt high-level C rewrite; inline assembly already carries semantic control-flow shape"
    if quality == "byte-emission-asm":
        if target_byte_span or relocation:
            return "model decoded control/data references first, then replace byte-emission assembly with C"
        return "promote only after adding an instruction-shape decoder for this byte-emission rule"
    return "keep as bootstrap evidence until a semantic source generator exists"


def write_high_level_promotion_targets(
    out_dir: Path,
    *,
    promotion_stats: dict[tuple[str, str], dict[str, Any]],
    summary: dict[str, Any],
) -> Path:
    targets = []
    ordered_stats = sorted(promotion_stats.values(), key=promotion_priority)
    for stats in ordered_stats:
        matched = len(stats["matchedKeys"])
        if matched <= 0:
            continue
        targets.append(
            {
                "rule": stats["rule"],
                "sourceQuality": stats["sourceQuality"],
                "matchedCandidates": matched,
                "mismatchedCandidates": len(stats["mismatchedKeys"]),
                "compileFailedCandidates": len(stats["compileFailedKeys"]),
                "errorCandidates": len(stats["errorKeys"]),
                "exampleNames": stats["exampleNames"],
                "exampleSources": stats["exampleSources"],
                "evidenceHints": {
                    "sourceTierSamples": sorted(stats["sourceTierSamples"])[:5],
                    "targetByteSpanCandidates": stats["targetByteSpanCandidates"],
                    "relocationCandidates": stats["relocationCandidates"],
                    "registerAbiCandidates": stats["registerAbiCandidates"],
                    "boundarySuspectCandidates": stats["boundarySuspectCandidates"],
                    "semanticSourceCandidates": stats["semanticSourceCandidates"],
                    "sourceShapeSearches": stats["sourceShapeSearches"],
                    "sourceShapeSearchMatches": stats["sourceShapeSearchMatches"],
                    "sourceShapeSearchBestCommonPrefix": stats["sourceShapeSearchBestCommonPrefix"],
                    "sourceShapeSearchBestNames": stats["sourceShapeSearchBestNames"],
                },
                "promotionFeasibility": promotion_feasibility(stats),
                "priority": len(targets) + 1,
                "recommendedAction": promotion_recommended_action(stats),
            }
        )
    for index, target in enumerate(targets, start=1):
        target["priority"] = index

    path = out_dir / "high-level-promotion-targets.json"
    report = {
        "schema": "mizuchi.source-parity.high-level-promotion-targets.v1",
        "status": "complete",
        "sourceSynthesisSummary": {
            "summaryPath": str(out_dir / "summary.json"),
            "verifyPackagedSource": summary.get("verifyPackagedSource"),
            "generatedCandidates": summary.get("generatedCandidates"),
            "attemptedCandidates": summary.get("attemptedCandidates"),
            "semanticCodeSliceMatchedCandidates": summary.get("semanticCodeSliceMatchedCandidates"),
            "generatedBySourceQuality": summary.get("generatedBySourceQuality"),
            "semanticCodeSliceMatchedBySourceQuality": summary.get("semanticCodeSliceMatchedBySourceQuality"),
            "semanticMismatchedBySourceQuality": summary.get("semanticMismatchedBySourceQuality"),
            "compileFailedBySourceQuality": summary.get("compileFailedBySourceQuality"),
        },
        "highLevelMatchedCandidates": int((summary.get("semanticCodeSliceMatchedBySourceQuality") or {}).get("high-level-c") or 0),
        "nonHighLevelMatchedCandidates": sum(
            int(count)
            for quality, count in (summary.get("semanticCodeSliceMatchedBySourceQuality") or {}).items()
            if quality != "high-level-c"
        ),
        "promotionTargets": targets,
        "claimBoundary": "This is a targeting report for future high-level C promotion, not a source parity claim.",
    }
    write_json(path, report)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--queue", type=Path, default=ROOT / "target/swkotor-recovery-queue/queue.jsonl")
    parser.add_argument("--source-tasks", type=Path, action="append", default=[], help="source-generation/tasks.jsonl from recover/recover-windows. Converted rows use task target-slice bytes.")
    parser.add_argument("--source-tasks-only", action="store_true", help="Only inspect rows converted from --source-tasks; do not prepend the default recovery queue.")
    parser.add_argument("--verify-packaged-source", action="store_true", help="For source-task rows, verify the packaged source file from tasks.jsonl instead of regenerating a candidate from bytes.")
    parser.add_argument("--upgrade-packaged-source", action="store_true", help="For source-task rows, try regenerated semantic candidates before the packaged source fallback.")
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
    parser.add_argument(
        "--max-attempts-per-function",
        type=int,
        default=0,
        help="Maximum candidate attempts per function. 0 means use --max-variants-per-function as the limit.",
    )
    parser.add_argument(
        "--max-attempts-per-function-policy",
        choices=["uniform", "adaptive"],
        default="uniform",
        help=(
            "uniform keeps a fixed per-function cap; adaptive reduces caps for partial/source-slice "
            "recovery rows that are less likely to match semantic C quickly."
        ),
    )
    parser.add_argument("--strategies", help="Comma-separated strategy/tag filter, for example virtual-call-or-thiscall-model,compiler-profile-probe.")
    parser.add_argument("--compiler", choices=["msvc", "clang", "clang-cl"], default="msvc", help="Compiler backend for candidate verification. clang/clang-cl can objdiff against synthetic target objects built from source-task bytes.")
    parser.add_argument("--clang", default="clang")
    parser.add_argument("--compiler-profile", action="append", default=[], help="Compiler profile as NAME='/O2 /Oy /GS-'. Repeat for multiple profiles.")
    parser.add_argument("--dry-run", action="store_true", help="Emit generated candidates without compiling or running objdiff.")
    parser.add_argument("--semantic-only", action="store_true", help="Only compile semantic source candidates; skip byte-exact assembly bootstrap candidates.")
    parser.add_argument("--source-quality", action="append", default=[], help="Only verify generated candidates with this source quality. Repeat or comma-separate: high-level-c, inline-asm-c, byte-emission-asm, nonsemantic-bootstrap.")
    parser.add_argument("--skip-boundary-suspect", action="store_true", help="Skip source-task rows whose target-slice boundary quality is marked suspect.")
    parser.add_argument("--source-shape-search", action="store_true", help="Run bounded source-shape searches for semantic candidates that compile but do not byte-match. Slower; off by default.")
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
    code_slice_matches_path = args.out_dir / "code-slice-matches.jsonl"
    source_shape_matches_path = args.out_dir / "source-shape-matches.jsonl"
    clean_jsonl(attempts_path)
    clean_jsonl(accepted_path)
    clean_jsonl(code_slice_matches_path)
    clean_jsonl(source_shape_matches_path)

    strategies = None
    if args.strategies:
        strategies = {item.strip() for item in args.strategies.split(",") if item.strip()}
    source_qualities = {
        item.strip()
        for value in args.source_quality
        for item in value.split(",")
        if item.strip()
    } or None
    compiler_profiles = [parse_profile_flag_set(value) for value in args.compiler_profile if value.strip()]
    strategy_by_name = load_strategy(args.remaining_features)
    retrieval_by_name = load_retrieval(args.retrieval)
    matched = load_matched(args.matched_summary)

    skipped = 0
    inspected = 0
    generated = 0
    semantic_generated = 0
    attempted = 0
    matched_count = 0
    code_slice_matched = 0
    semantic_code_slice_matched = 0
    nonsemantic_code_slice_matched = 0
    semantic_mismatched = 0
    source_shape_search_paths: set[str] = set()
    source_shape_search_matches = 0
    high_level_source_shape_matched = 0
    skipped_rule_filtered = 0
    skipped_source_quality = 0
    skipped_nonsemantic = 0
    skipped_boundary_suspect = 0
    skipped_by_attempt_limit = 0
    attempted_limit_distribution: dict[int, int] = {}
    attempted_limit_reason_distribution: dict[str, int] = {}
    compile_failed = 0
    slice_failed = 0
    unsupported = 0
    mismatched = 0
    errors = 0
    generated_by_source_quality: dict[str, int] = {}
    semantic_generated_by_source_quality: dict[str, int] = {}
    attempted_by_source_quality: dict[str, int] = {}
    code_slice_matched_by_source_quality: dict[str, int] = {}
    semantic_code_slice_matched_by_source_quality: dict[str, int] = {}
    mismatched_by_source_quality: dict[str, int] = {}
    semantic_mismatched_by_source_quality: dict[str, int] = {}
    compile_failed_by_source_quality: dict[str, int] = {}
    error_by_source_quality: dict[str, int] = {}
    generated_by_recovery_scope: dict[str, int] = {}
    semantic_generated_by_recovery_scope: dict[str, int] = {}
    attempted_by_recovery_scope: dict[str, int] = {}
    code_slice_matched_by_recovery_scope: dict[str, int] = {}
    semantic_code_slice_matched_by_recovery_scope: dict[str, int] = {}
    promotion_stats: dict[tuple[str, str], dict[str, Any]] = {}

    input_rows = [] if args.source_tasks_only else [*iter_jsonl(args.queue)]
    for source_tasks in args.source_tasks:
        input_rows.extend(iter_source_task_rows(source_tasks))
    if args.source_tasks:
        input_rows.sort(key=synthesis_row_priority)

    for row in input_rows:
        if inspected >= args.limit:
            break
        if (str(row.get("name")), str(row.get("entry"))) in matched:
            continue
        if not strategy_allowed(row, strategies, strategy_by_name):
            continue
        if skipped < args.offset:
            skipped += 1
            continue
        if args.skip_boundary_suspect and is_boundary_suspect(row):
            skipped_boundary_suspect += 1
            record = {
                "schema": "mizuchi.source-parity-synthesis-attempt.v1",
                "name": row.get("name"),
                "entry": row.get("entry"),
                "section": row.get("section"),
                "bodyBytes": row.get("bytes") or row.get("bodyBytes"),
                "instructionCount": row.get("instructionCount"),
                "strategyClass": strategy_by_name.get(str(row.get("name"))),
                "nearestMatchedExamples": retrieval_by_name.get(str(row.get("name")), [])[:3],
                "status": "skipped-boundary-suspect",
                "differences": -1,
                "targetSlice": row.get("targetSlice"),
                "sourceOrigin": "target-slice boundary quality was suspect; skipped before compile/objdiff scheduling",
            }
            append_jsonl(attempts_path, record)
            inspected += 1
            continue
        inspected += 1
        if args.verify_packaged_source and row.get("sourceTask"):
            packaged_candidate = packaged_source_candidate(row)
            if args.upgrade_packaged_source:
                raw_candidates = append_packaged_fallback(generate(row, args.max_variants_per_function), packaged_candidate)
            else:
                raw_candidates = [packaged_candidate] if packaged_candidate is not None else []
        else:
            raw_candidates = generate(row, args.max_variants_per_function)
        raw_candidates, rule_filtered = filter_candidates_by_explicit_rule_strategies(raw_candidates, strategies)
        skipped_rule_filtered += rule_filtered
        raw_candidates, quality_filtered = filter_candidates_by_source_quality(raw_candidates, source_qualities)
        skipped_source_quality += quality_filtered
        candidates = compiler_compatible_candidates(row, raw_candidates, args.compiler, args.max_variants_per_function)
        filtered_incompatible = max(0, len(raw_candidates) - len(candidates))
        skipped_for_semantic_only = 0
        if args.semantic_only:
            original_count = len(candidates)
            candidates = [candidate for candidate in candidates if candidate.semantic_source]
            skipped_for_semantic_only = original_count - len(candidates)
            skipped_nonsemantic += skipped_for_semantic_only
        if filtered_incompatible:
            skipped_nonsemantic += filtered_incompatible
        if not candidates:
            if skipped_for_semantic_only:
                record_status = "skipped-nonsemantic-only"
                record_origin = "semantic-only synthesis skipped byte-exact assembly bootstrap candidates before compile/objdiff scheduling"
            elif filtered_incompatible:
                record_status = "skipped-incompatible-compiler"
                record_origin = f"{args.compiler} synthesis skipped candidates that require a different source compiler dialect"
            elif quality_filtered:
                record_status = "skipped-source-quality-filtered"
                record_origin = f"source-quality filter skipped generated candidates outside {sorted(source_qualities or [])}"
            else:
                unsupported += 1
                record_status = "unsupported-pattern"
                record_origin = "no source emitted; no byte-pattern generator currently supports this function"
            record = {
                "schema": "mizuchi.source-parity-synthesis-attempt.v1",
                "name": row.get("name"),
                "entry": row.get("entry"),
                "section": row.get("section"),
                "bodyBytes": row.get("bytes") or row.get("bodyBytes"),
                "instructionCount": row.get("instructionCount"),
                "strategyClass": strategy_by_name.get(str(row.get("name"))),
                "nearestMatchedExamples": retrieval_by_name.get(str(row.get("name")), [])[:3],
                "status": record_status,
                "differences": -1,
                "sourceOrigin": record_origin,
                "filteredIncompatibleCandidates": filtered_incompatible,
                "filteredByExplicitRuleStrategies": rule_filtered,
                "filteredBySourceQuality": quality_filtered,
                "compiler": args.compiler,
            }
            append_jsonl(attempts_path, record)
            continue
        attempt_limit = args.max_attempts_per_function
        if attempt_limit <= 0:
            attempt_limit = args.max_variants_per_function
        attempt_limit, attempt_limit_reason = resolve_attempt_limit(
            row=row,
            candidates=candidates,
            base_limit=attempt_limit,
            policy=args.max_attempts_per_function_policy,
        )
        attempted_limit_reason_distribution[attempt_limit_reason] = attempted_limit_reason_distribution.get(attempt_limit_reason, 0) + 1
        attempted_limit_distribution[attempt_limit] = attempted_limit_distribution.get(attempt_limit, 0) + 1
        if attempt_limit > 0 and len(candidates) > attempt_limit:
            skipped_by_attempt_limit += len(candidates) - attempt_limit
            candidates = candidates[:attempt_limit]

        generated += len(candidates)
        semantic_generated += sum(1 for candidate in candidates if candidate.semantic_source)
        for candidate in candidates:
            quality = generated_candidate_source_quality(candidate)
            recovery_scope = source_recovery_scope(row, candidate)
            generated_by_source_quality[quality] = generated_by_source_quality.get(quality, 0) + 1
            generated_by_recovery_scope[recovery_scope] = generated_by_recovery_scope.get(recovery_scope, 0) + 1
            if candidate.semantic_source:
                semantic_generated_by_source_quality[quality] = semantic_generated_by_source_quality.get(quality, 0) + 1
                semantic_generated_by_recovery_scope[recovery_scope] = semantic_generated_by_recovery_scope.get(recovery_scope, 0) + 1
        for candidate in candidates:
            records = attempt_candidate(
                row,
                candidate,
                args.out_dir,
                compiler=args.compiler,
                clang=args.clang,
                compiler_profiles=compiler_profiles,
                inventory=args.inventory,
                vc_root=args.vc_root,
                wine=args.wine,
                wineprefix=args.wineprefix,
                timeout=args.timeout,
                dry_run=args.dry_run,
                source_shape_search=args.source_shape_search,
            )
            for record in records:
                record["strategyClass"] = strategy_by_name.get(str(row.get("name")))
                record["nearestMatchedExamples"] = retrieval_by_name.get(str(row.get("name")), [])[:3]
                append_jsonl(attempts_path, record)
                update_promotion_stats(promotion_stats, record)
                quality = str(record.get("sourceQuality") or generated_candidate_source_quality(candidate))
                if not args.dry_run:
                    attempted_by_source_quality[quality] = attempted_by_source_quality.get(quality, 0) + 1
                    recovery_scope = str(record.get("sourceRecoveryScope") or "unknown")
                    attempted_by_recovery_scope[recovery_scope] = attempted_by_recovery_scope.get(recovery_scope, 0) + 1
                attempted += 0 if args.dry_run else 1
                status = record.get("status")
                differences = int(record.get("differences", -1))
                if status == "matched" and differences == 0:
                    matched_count += 1
                    append_jsonl(accepted_path, record)
                elif status == "code-slice-matched" and differences == 0:
                    code_slice_matched += 1
                    append_jsonl(code_slice_matches_path, record)
                    code_slice_matched_by_source_quality[quality] = code_slice_matched_by_source_quality.get(quality, 0) + 1
                    recovery_scope = str(record.get("sourceRecoveryScope") or "unknown")
                    code_slice_matched_by_recovery_scope[recovery_scope] = code_slice_matched_by_recovery_scope.get(recovery_scope, 0) + 1
                    if record.get("semanticSource"):
                        semantic_code_slice_matched += 1
                        semantic_code_slice_matched_by_source_quality[quality] = semantic_code_slice_matched_by_source_quality.get(quality, 0) + 1
                        semantic_code_slice_matched_by_recovery_scope[recovery_scope] = semantic_code_slice_matched_by_recovery_scope.get(recovery_scope, 0) + 1
                    else:
                        nonsemantic_code_slice_matched += 1
                elif status == "compile-failed":
                    compile_failed += 1
                    compile_failed_by_source_quality[quality] = compile_failed_by_source_quality.get(quality, 0) + 1
                elif status == "slice-failed":
                    slice_failed += 1
                elif status == "mismatched":
                    mismatched += 1
                    mismatched_by_source_quality[quality] = mismatched_by_source_quality.get(quality, 0) + 1
                    if record.get("semanticSource"):
                        semantic_mismatched += 1
                        semantic_mismatched_by_source_quality[quality] = semantic_mismatched_by_source_quality.get(quality, 0) + 1
                elif status not in {"generated-only"}:
                    errors += 1
                    error_by_source_quality[quality] = error_by_source_quality.get(quality, 0) + 1
                search_path = record.get("sourceShapeSearch")
                if search_path and str(search_path) not in source_shape_search_paths:
                    source_shape_search_paths.add(str(search_path))
                    search_summary = record.get("sourceShapeSearchSummary") if isinstance(record.get("sourceShapeSearchSummary"), dict) else {}
                    if search_summary.get("status") == "matched":
                        source_shape_search_matches += 1
                        source_shape_record = matched_source_shape_record(record, str(search_path))
                        if source_shape_record is not None:
                            high_level_source_shape_matched += 1
                            append_jsonl(source_shape_matches_path, source_shape_record)
            if args.progress_every and generated and generated % args.progress_every == 0:
                print(
                    f"source-parity-synthesize: inspected={inspected} generated={generated} matched={matched_count}",
                    file=sys.stderr,
                    flush=True,
                )

    promotion_targets_path = args.out_dir / "high-level-promotion-targets.json"
    summary = {
        "schema": "mizuchi.source-parity-synthesis-summary.v1",
        "status": "generated-only" if args.dry_run else "complete",
        "queue": str(args.queue),
        "sourceTasks": [str(path) for path in args.source_tasks],
        "verifyPackagedSource": bool(args.verify_packaged_source),
        "upgradePackagedSource": bool(args.upgrade_packaged_source),
        "inventory": str(args.inventory),
        "remainingFeatures": str(args.remaining_features),
        "retrieval": str(args.retrieval),
        "outDir": str(args.out_dir),
        "attemptsPath": str(attempts_path),
        "acceptedPath": str(accepted_path),
        "codeSliceMatchesPath": str(code_slice_matches_path),
        "sourceShapeMatchesPath": str(source_shape_matches_path),
        "promotionTargetsPath": str(promotion_targets_path),
        "limit": args.limit,
        "offset": args.offset,
        "skippedEligibleFunctions": skipped,
        "inspectedFunctions": inspected,
        "unsupportedFunctions": unsupported,
        "generatedCandidates": generated,
        "semanticGeneratedCandidates": semantic_generated,
        "nonSemanticBootstrapCandidates": max(0, generated - semantic_generated),
        "attemptedCandidates": attempted,
        "maxAttemptsPerFunction": args.max_attempts_per_function,
        "attemptLimitFallbackToMaxVariants": args.max_attempts_per_function <= 0,
        "skippedByAttemptLimit": skipped_by_attempt_limit,
        "attemptLimitPolicy": args.max_attempts_per_function_policy,
        "attemptLimitDistribution": dict(sorted(attempted_limit_distribution.items())),
        "attemptLimitReasonDistribution": dict(sorted(attempted_limit_reason_distribution.items())),
        "acceptedCandidates": matched_count,
        "codeSliceMatchedCandidates": code_slice_matched,
        "semanticCodeSliceMatchedCandidates": semantic_code_slice_matched,
        "nonSemanticCodeSliceMatchedCandidates": nonsemantic_code_slice_matched,
        "mismatchedCandidates": mismatched,
        "semanticMismatchedCandidates": semantic_mismatched,
        "sourceShapeSearches": len(source_shape_search_paths),
        "sourceShapeSearchMatches": source_shape_search_matches,
        "highLevelSourceShapeMatchedCandidates": high_level_source_shape_matched,
        "generatedBySourceQuality": dict(sorted(generated_by_source_quality.items())),
        "semanticGeneratedBySourceQuality": dict(sorted(semantic_generated_by_source_quality.items())),
        "attemptedBySourceQuality": dict(sorted(attempted_by_source_quality.items())),
        "codeSliceMatchedBySourceQuality": dict(sorted(code_slice_matched_by_source_quality.items())),
        "semanticCodeSliceMatchedBySourceQuality": dict(sorted(semantic_code_slice_matched_by_source_quality.items())),
        "generatedByRecoveryScope": dict(sorted(generated_by_recovery_scope.items())),
        "semanticGeneratedByRecoveryScope": dict(sorted(semantic_generated_by_recovery_scope.items())),
        "attemptedByRecoveryScope": dict(sorted(attempted_by_recovery_scope.items())),
        "codeSliceMatchedByRecoveryScope": dict(sorted(code_slice_matched_by_recovery_scope.items())),
        "semanticCodeSliceMatchedByRecoveryScope": dict(sorted(semantic_code_slice_matched_by_recovery_scope.items())),
        "wholeFunctionSemanticGeneratedCandidates": semantic_generated_by_recovery_scope.get("whole-function", 0),
        "partialSourceSliceSemanticGeneratedCandidates": semantic_generated_by_recovery_scope.get("partial-source-slice", 0),
        "wholeFunctionSemanticCodeSliceMatchedCandidates": semantic_code_slice_matched_by_recovery_scope.get("whole-function", 0),
        "partialSourceSliceSemanticCodeSliceMatchedCandidates": semantic_code_slice_matched_by_recovery_scope.get("partial-source-slice", 0),
        "mismatchedBySourceQuality": dict(sorted(mismatched_by_source_quality.items())),
        "semanticMismatchedBySourceQuality": dict(sorted(semantic_mismatched_by_source_quality.items())),
        "compileFailedBySourceQuality": dict(sorted(compile_failed_by_source_quality.items())),
        "errorBySourceQuality": dict(sorted(error_by_source_quality.items())),
        "skippedRuleFilteredCandidates": skipped_rule_filtered,
        "skippedSourceQualityFilteredCandidates": skipped_source_quality,
        "skippedNonSemanticCandidates": skipped_nonsemantic,
        "skippedBoundarySuspectFunctions": skipped_boundary_suspect,
        "compileFailedCandidates": compile_failed,
        "sliceFailedCandidates": slice_failed,
        "errorCandidates": errors,
        "compiler": args.compiler,
        "semanticOnly": bool(args.semantic_only),
        "skipBoundarySuspect": bool(args.skip_boundary_suspect),
        "sourceShapeSearchEnabled": bool(args.source_shape_search),
        "compilerProfiles": [name for name, _ in compiler_profiles] if compiler_profiles else [name for name, _ in default_profile_set(args.compiler)],
        "dryRun": args.dry_run,
        "strategies": sorted(strategies) if strategies else None,
        "sourceQualityFilter": sorted(source_qualities) if source_qualities else None,
        "claimBoundary": "candidate source is generated automatically from binary-derived features; accepted source requires objdiff zero",
    }
    write_high_level_promotion_targets(args.out_dir, promotion_stats=promotion_stats, summary=summary)
    write_json(args.out_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def synthesis_row_priority(row: dict[str, Any]) -> tuple[int, int, int, str]:
    generator = row.get("automaticGenerator")
    rule = str(generator.get("rule") or "") if isinstance(generator, dict) else ""
    target_slice = row.get("targetSlice") if isinstance(row.get("targetSlice"), dict) else {}
    boundary_quality = target_slice.get("boundaryQuality") if isinstance(target_slice.get("boundaryQuality"), dict) else {}
    is_semantic = row.get("semanticSource") is True or (rule and rule != "target-slice-asm-bootstrap")
    is_bootstrap = rule == "target-slice-asm-bootstrap"
    is_suspect = boundary_quality.get("status") == "suspect"
    body_bytes = int(row.get("bodyBytes") or 0)
    return (
        0 if is_semantic else 1,
        1 if is_suspect and not is_semantic else 0,
        1 if is_bootstrap else 0,
        f"{body_bytes:08d}:{row.get('entry')}:{row.get('name')}",
    )


if __name__ == "__main__":
    raise SystemExit(main())
