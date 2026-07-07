#!/usr/bin/env python3
"""Match simple swkotor call/jump wrappers using relocation-aware target objects."""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VC_ROOT = Path("/run/media/brunner56/MyBook/MizuchiSource/toolchains/msvc8.0-main")
DEFAULT_WINEPREFIX = ROOT / "target/toolchain-acquire/vctoolkit2003/wineprefix"
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class RelocCandidate:
    name: str
    symbol: str
    target_name: str
    target_symbol: str
    kind: str
    source: str
    target_asm: str


def run(args: list[str], *, env: dict[str, str] | None = None, timeout: int = 240) -> subprocess.CompletedProcess[str]:
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


def load_inventory(path: Path) -> tuple[list[dict], dict[int, dict]]:
    rows = list(iter_inventory(path))
    by_entry = {int(row["entry"], 16): row for row in rows if row.get("entry")}
    return rows, by_entry


def signed_rel_target(entry: int, offset: int, data: bytes) -> int:
    disp = struct.unpack("<i", data[offset + 1 : offset + 5])[0]
    return entry + offset + 5 + disp


def symbol(name: str, *, stdcall_bytes: int | None = None) -> str:
    return f"_{name}@{stdcall_bytes}" if stdcall_bytes is not None else f"_{name}"


def ensure_identifier(*names: str) -> bool:
    return all(IDENT_RE.match(name) for name in names)


def thunk_candidate(row: dict, target_row: dict) -> RelocCandidate | None:
    name = str(row["name"])
    target_name = str(target_row["name"])
    if not ensure_identifier(name, target_name):
        return None
    if name == target_name:
        return None
    sym = symbol(name)
    target_sym = symbol(target_name)
    return RelocCandidate(
        name=name,
        symbol=sym,
        target_name=target_name,
        target_symbol=target_sym,
        kind="reloc-tailcall-thunk-cdecl-void",
        source="\n".join(
            [
                f"extern void {target_name}(void);",
                "",
                f"void {name}(void) {{",
                f"    {target_name}();",
                "}",
                "",
            ]
        ),
        target_asm="\n".join(
            [
                "    .intel_syntax noprefix",
                "    .text",
                f"    .globl {sym}",
                f"    .extern {target_sym}",
                f"{sym}:",
                f"    jmp {target_sym}",
                "",
            ]
        ),
    )


def stdcall_wrapper_candidate(row: dict, target_row: dict) -> RelocCandidate | None:
    name = str(row["name"])
    target_name = str(target_row["name"])
    if not ensure_identifier(name, target_name):
        return None

    data = bytes.fromhex(row["bytes"])
    args: list[str] | None = None
    pushes: list[str] | None = None
    wrapper_ret = 0

    # void __stdcall f(int a0) { callee(a0, imm, imm); }
    if (
        len(data) == 17
        and data[:4] == b"\x8b\x44\x24\x04"
        and data[4] == 0x6A
        and data[6] == 0x6A
        and data[8] == 0x50
        and data[9] == 0xE8
        and data[14:] == b"\xc2\x04\x00"
    ):
        args = ["a0", str(data[7]), str(data[5])]
        pushes = [str(data[5]), str(data[7]), "eax"]
        wrapper_ret = 4

    # void __stdcall f(int a0) { callee(a0, imm); }
    if (
        args is None
        and len(data) == 15
        and data[:4] == b"\x8b\x44\x24\x04"
        and data[4] == 0x6A
        and data[6] == 0x50
        and data[7] == 0xE8
        and data[12:] == b"\xc2\x04\x00"
    ):
        args = ["a0", str(data[5])]
        pushes = [str(data[5]), "eax"]
        wrapper_ret = 4

    if args is None or pushes is None:
        return None

    callee_bytes = len(args) * 4
    sym = symbol(name, stdcall_bytes=wrapper_ret)
    target_sym = symbol(target_name, stdcall_bytes=callee_bytes)
    params = ", ".join(f"int a{i}" for i in range(wrapper_ret // 4))
    extern_params = ", ".join("int" for _ in args)
    source_args = ", ".join(args)
    asm_lines = [
        "    .intel_syntax noprefix",
        "    .text",
        f"    .globl {sym}",
        f"    .extern {target_sym}",
        f"{sym}:",
    ]
    if wrapper_ret == 4:
        asm_lines.append("    mov eax, dword ptr [esp + 4]")
    else:
        asm_lines.append("    mov eax, dword ptr [esp + 8]")
        asm_lines.append("    mov edx, dword ptr [esp + 4]")
    for pushed in pushes:
        asm_lines.append(f"    push {pushed}")
    asm_lines.append(f"    call {target_sym}")
    asm_lines.append(f"    ret {wrapper_ret}")
    asm_lines.append("")

    return RelocCandidate(
        name=name,
        symbol=sym,
        target_name=target_name,
        target_symbol=target_sym,
        kind=f"reloc-stdcall-wrapper-{wrapper_ret // 4}arg-{len(args)}callargs",
        source="\n".join(
            [
                f"extern void __stdcall {target_name}({extern_params});",
                "",
                f"void __stdcall {name}({params}) {{",
                f"    {target_name}({source_args});",
                "}",
                "",
            ]
        ),
        target_asm="\n".join(asm_lines),
    )


def ecx_tail_candidate(row: dict, target_row: dict) -> RelocCandidate | None:
    name = str(row["name"])
    target_name = str(target_row["name"])
    if not ensure_identifier(name, target_name):
        return None
    if name == target_name:
        return None

    data = bytes.fromhex(row["bytes"])
    sym = symbol(name, stdcall_bytes=4).replace("_", "@", 1)
    target_sym = symbol(target_name, stdcall_bytes=4).replace("_", "@", 1)
    body: list[str] | None = None
    asm_lines = [
        "    .intel_syntax noprefix",
        "    .text",
        f"    .globl {sym}",
        f"    .extern {target_sym}",
        f"{sym}:",
    ]
    kind = ""

    if len(data) == 8 and data[:2] == b"\x8b\x49" and data[3] == 0xE9:
        offset = data[2]
        body = [f"    {target_name}(*(void **)((char *)self + 0x{offset:x}));"]
        asm_lines.extend([f"    mov ecx, dword ptr [ecx + 0x{offset:x}]", f"    jmp {target_sym}", ""])
        kind = "reloc-fastcall-field-tail-jmp"

    elif len(data) == 8 and data[:2] == b"\x83\xc1" and data[3] == 0xE9:
        offset = data[2]
        body = [f"    {target_name}((char *)self + 0x{offset:x});"]
        asm_lines.extend([f"    add ecx, 0x{offset:x}", f"    jmp {target_sym}", ""])
        kind = "reloc-fastcall-add-tail-jmp"

    elif len(data) == 11 and data[:2] == b"\xc7\x01" and data[6] == 0xE9:
        vtable = int.from_bytes(data[2:6], "little")
        body = [
            f"    *(unsigned int *)self = 0x{vtable:08x}u;",
            f"    {target_name}(self);",
        ]
        asm_lines.extend([f"    mov dword ptr [ecx], 0x{vtable:08x}", f"    jmp {target_sym}", ""])
        kind = "reloc-fastcall-vtable-tail-jmp"

    elif len(data) == 14 and data[:2] == b"\xc7\x01" and data[6:8] == b"\x83\xc1" and data[9] == 0xE9:
        vtable = int.from_bytes(data[2:6], "little")
        offset = data[8]
        body = [
            f"    *(unsigned int *)self = 0x{vtable:08x}u;",
            f"    {target_name}((char *)self + 0x{offset:x});",
        ]
        asm_lines.extend(
            [
                f"    mov dword ptr [ecx], 0x{vtable:08x}",
                f"    add ecx, 0x{offset:x}",
                f"    jmp {target_sym}",
                "",
            ]
        )
        kind = "reloc-fastcall-vtable-add-tail-jmp"

    if body is None:
        return None

    return RelocCandidate(
        name=name,
        symbol=sym,
        target_name=target_name,
        target_symbol=target_sym,
        kind=kind,
        source="\n".join(
            [
                f"extern void __fastcall {target_name}(void *self);",
                "",
                f"void __fastcall {name}(void *self) {{",
                *body,
                "}",
                "",
            ]
        ),
        target_asm="\n".join(asm_lines),
    )


def candidate_for(row: dict, by_entry: dict[int, dict], *, text_section: str = ".textV") -> RelocCandidate | None:
    if row.get("section") != text_section:
        return None
    data = bytes.fromhex(row["bytes"])
    entry = int(row["entry"], 16)

    if len(data) == 5 and data[0] == 0xE9:
        target = signed_rel_target(entry, 0, data)
        target_row = by_entry.get(target)
        if target_row is not None:
            return thunk_candidate(row, target_row)

    jmp_offset = data.rfind(b"\xe9")
    if jmp_offset > 0 and jmp_offset + 5 == len(data):
        target = signed_rel_target(entry, jmp_offset, data)
        target_row = by_entry.get(target)
        if target_row is not None:
            candidate = ecx_tail_candidate(row, target_row)
            if candidate is not None:
                return candidate

    call_offset = data.find(b"\xe8")
    if call_offset >= 0 and call_offset + 5 <= len(data):
        target = signed_rel_target(entry, call_offset, data)
        target_row = by_entry.get(target)
        if target_row is not None:
            return stdcall_wrapper_candidate(row, target_row)

    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=ROOT / "target/swkotor-unpack/facts/function-inventory.jsonl")
    parser.add_argument("--limit", type=int, default=200, help="Max candidates to attempt; 0 means no limit.")
    parser.add_argument("--out", type=Path, default=ROOT / "target/swkotor-reloc-wrapper-matches/summary.jsonl")
    parser.add_argument("--summary", type=Path, default=ROOT / "target/swkotor-reloc-wrapper-matches/summary.json")
    parser.add_argument("--text-section", default=".textV", help="Inventory section to match (e.g. .textV, .textU).")
    parser.add_argument("--match-root", type=Path, default=ROOT / "target/swkotor-reloc-wrapper-matches")
    parser.add_argument("--vc-root", type=Path, default=DEFAULT_VC_ROOT)
    parser.add_argument("--wineprefix", type=Path, default=DEFAULT_WINEPREFIX)
    parser.add_argument("--progress-every", type=int, default=0, help="Print attempted/matched progress to stderr every N attempted candidates.")
    args = parser.parse_args()

    rows, by_entry = load_inventory(args.inventory)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)

    records = []
    attempted = 0
    matched = 0
    with args.out.open("w", encoding="utf-8") as fh:
        for row in rows:
            candidate = candidate_for(row, by_entry, text_section=args.text_section)
            if candidate is None:
                continue
            out_dir = args.match_root / f"{row.get('entry')}_{candidate.name}"
            out_dir.mkdir(parents=True, exist_ok=True)
            target_s = out_dir / "target.S"
            target_obj = out_dir / "target.obj"
            candidate_c = out_dir / "candidate.c"
            candidate_obj = out_dir / "candidate.obj"
            target_s.write_text(candidate.target_asm, encoding="utf-8")
            candidate_c.write_text(candidate.source, encoding="utf-8")

            target_proc = run(["clang", "-target", "i686-pc-windows-msvc", "-c", str(target_s), "-o", str(target_obj)])
            if target_proc.returncode != 0:
                record = {
                    "schema": "mizuchi.swkotor-reloc-wrapper-match.v1",
                    "name": candidate.name,
                    "entry": row.get("entry"),
                    "kind": candidate.kind,
                    "targetName": candidate.target_name,
                    "targetSymbol": candidate.target_symbol,
                    "status": "target-compile-failed",
                    "differences": -1,
                    "stderr": target_proc.stderr[-2000:],
                    "outDir": str(out_dir),
                }
                records.append(record)
                fh.write(json.dumps(record, sort_keys=True) + "\n")
                fh.flush()
                continue

            env = os.environ.copy()
            env.update({"VC_ROOT": str(args.vc_root), "WINEPREFIX": str(args.wineprefix), "CL_OPT": "/O2"})
            compile_proc = run(
                [
                    "bash",
                    str(ROOT / "scripts/cl-compile.sh"),
                    str(candidate_c),
                    str(candidate_obj),
                    "/GS-",
                    "/Oy",
                ],
                env=env,
            )
            (out_dir / "compile.stdout").write_text(compile_proc.stdout, encoding="utf-8")
            (out_dir / "compile.stderr").write_text(compile_proc.stderr, encoding="utf-8")
            if compile_proc.returncode != 0:
                record = {
                    "schema": "mizuchi.swkotor-reloc-wrapper-match.v1",
                    "name": candidate.name,
                    "entry": row.get("entry"),
                    "kind": candidate.kind,
                    "status": "compile-failed",
                    "differences": -1,
                    "stderr": compile_proc.stderr[-2000:],
                    "outDir": str(out_dir),
                }
                records.append(record)
                fh.write(json.dumps(record, sort_keys=True) + "\n")
                fh.flush()
                attempted += 1
                if args.progress_every and attempted % args.progress_every == 0:
                    print(f"swkotor-match-reloc-wrappers: attempted={attempted} matched={matched}", file=sys.stderr, flush=True)
                continue

            verify_proc = run(
                [
                    "bash",
                    str(ROOT / "scripts/lib/verify-objdiff.sh"),
                    str(target_obj),
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
                "schema": "mizuchi.swkotor-reloc-wrapper-match.v1",
                "name": candidate.name,
                "entry": row.get("entry"),
                "section": row.get("section"),
                "bodyBytes": row.get("bodyBytes"),
                "instructionCount": row.get("instructionCount"),
                "kind": candidate.kind,
                "symbol": candidate.symbol,
                "targetName": candidate.target_name,
                "targetSymbol": candidate.target_symbol,
                "status": status,
                "differences": differences,
                "outDir": str(out_dir),
            }
            records.append(record)
            fh.write(json.dumps(record, sort_keys=True) + "\n")
            fh.flush()
            if args.progress_every and attempted % args.progress_every == 0:
                print(f"swkotor-match-reloc-wrappers: attempted={attempted} matched={matched}", file=sys.stderr, flush=True)
            if args.limit and attempted >= args.limit:
                break

    by_kind = []
    for kind in sorted({str(record.get("kind")) for record in records}):
        group = [record for record in records if str(record.get("kind")) == kind]
        by_kind.append(
            {
                "kind": kind,
                "count": len(group),
                "matched": sum(1 for record in group if record.get("status") == "matched" and record.get("differences") == 0),
            }
        )
    rollup = {
        "schema": "mizuchi.swkotor-reloc-wrapper-matches-summary.v1",
        "inventory": str(args.inventory),
        "summaryJsonl": str(args.out),
        "attempted": len(records),
        "matched": sum(1 for record in records if record.get("status") == "matched" and record.get("differences") == 0),
        "mismatched": sum(1 for record in records if record.get("status") != "matched" or record.get("differences") != 0),
        "byKind": by_kind,
        "matchedFunctions": [
            record["name"]
            for record in records
            if record.get("status") == "matched" and record.get("differences") == 0
        ],
    }
    args.summary.write_text(json.dumps(rollup, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"attempted": attempted, "matched": matched, "summary": str(args.out), "rollup": str(args.summary)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
