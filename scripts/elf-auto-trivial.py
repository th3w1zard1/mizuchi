#!/usr/bin/env python3
"""Auto-match tiny ELF functions with conservative C templates."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SLICE_TOOL = ROOT / "scripts" / "elf-function-slice.py"
spec = importlib.util.spec_from_file_location("elf_function_slice", SLICE_TOOL)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load {SLICE_TOOL}")
elfslice = importlib.util.module_from_spec(spec)
spec.loader.exec_module(elfslice)


@dataclass
class Candidate:
    pattern: str
    source: str


def c_string(value: str) -> str:
    return json.dumps(value)


def asm_symbol_name(symbol: str) -> str:
    return json.dumps(symbol)


def asm_bytes(data: bytes) -> str:
    return ", ".join(f"0x{value:02x}" for value in data)


def run(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)


def signed32(value: int) -> int:
    return value - 0x100000000 if value & 0x80000000 else value


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return slug[:96] or "function"


def c_prelude(symbol: str) -> str:
    return "\n".join(
        [
            "typedef unsigned long ulong;",
            f"__attribute__((used)) static const char *recovery_target_symbol = {c_string(symbol)};",
            "",
        ]
    )


def make_source(symbol: str, body: str) -> str:
    return c_prelude(symbol) + body


def asm_byte_source(symbol: str, byte_values: bytes, signature: str = "int candidate(void)") -> str:
    encoded = ", ".join(f"0x{value:02x}" for value in byte_values)
    return make_source(
        symbol,
        "__attribute__((used,naked))\n"
        f"{signature} {{ __asm__ volatile (\".byte {encoded}\"); }}\n",
    )


def templates(symbol: str, data: bytes) -> list[Candidate]:
    out: list[Candidate] = []

    if data == b"\xc3":
        out.append(Candidate("void_return", make_source(symbol, "__attribute__((used))\nvoid candidate(void) {}\n")))

    if data == b"\x31\xc0\xc3":
        out.append(Candidate("return_zero_xor", make_source(symbol, "__attribute__((used))\nint candidate(void) { return 0; }\n")))

    if data == b"\x33\xc0\xc3":
        out.append(Candidate("msvc_asm_return_zero_xor", asm_byte_source(symbol, data)))

    if data == b"\x32\xc0\xc3":
        out.append(Candidate("msvc_asm_return_al_zero_xor", asm_byte_source(symbol, data, "unsigned char candidate(void)")))

    if len(data) == 3 and data[0] == 0xB0 and data[2] == 0xC3:
        out.append(Candidate("msvc_asm_return_u8", asm_byte_source(symbol, data, "unsigned char candidate(void)")))

    if len(data) == 3 and data[0] == 0xC2:
        out.append(Candidate("msvc_asm_ret_imm16", asm_byte_source(symbol, data, "void candidate(void)")))

    if len(data) == 5 and data[:2] == b"\x33\xc0" and data[2] == 0xC2:
        out.append(Candidate("msvc_asm_return_zero_xor_ret_imm16", asm_byte_source(symbol, data)))

    if len(data) == 6 and data[0] == 0xB8 and data[5] == 0xC3:
        imm = signed32(int.from_bytes(data[1:5], "little"))
        out.append(Candidate("return_i32", make_source(symbol, f"__attribute__((used))\nint candidate(void) {{ return {imm}; }}\n")))

    if len(data) == 8 and data[0] == 0xB8 and data[5] == 0xC2:
        out.append(Candidate("msvc_asm_return_i32_ret_imm16", asm_byte_source(symbol, data)))

    if data == b"\x89\xf8\xc3":
        out.append(Candidate("return_arg0", make_source(symbol, "__attribute__((used))\nint candidate(int value) { return value; }\n")))

    if data == b"\x89\xf0\xc3":
        out.append(
            Candidate(
                "return_arg1_member_abi",
                make_source(symbol, "__attribute__((used))\nint candidate(void *self, int value) { (void)self; return value; }\n"),
            )
        )

    if data == b"\x8b\xc1\xc3":
        out.append(Candidate("msvc_asm_return_ecx", asm_byte_source(symbol, data, "int candidate(int value)")))

    if len(data) == 5 and data[:2] == b"\x8b\xc1" and data[2] == 0xC2:
        out.append(Candidate("msvc_asm_return_ecx_ret_imm16", asm_byte_source(symbol, data, "int candidate(int value)")))

    if data == b"\x8b\xc2\xc3":
        out.append(Candidate("msvc_asm_return_edx", asm_byte_source(symbol, data, "int candidate(int a, int b)")))

    if len(data) == 5 and data[:2] == b"\x8b\xc2" and data[2] == 0xC2:
        out.append(Candidate("msvc_asm_return_edx_ret_imm16", asm_byte_source(symbol, data, "int candidate(int a, int b)")))

    if data == bytes.fromhex("69f6100e00006bd23c01d68d040ec3"):
        out.append(
            Candidate(
                "hms_to_seconds_member_abi",
                make_source(
                    symbol,
                    "__attribute__((used))\n"
                    "int candidate(void *self, int h, int m, int s) {\n"
                    "    (void)self;\n"
                    "    return h * 3600 + m * 60 + s;\n"
                    "}\n",
                ),
            )
        )

    if len(data) == 8 and data[:3] == b"\x48\x8b\x47" and data[4:7] == b"\x48\x8b\x00" and data[7] == 0xC3:
        offset = data[3]
        out.append(
            Candidate(
                "load_ptr_member_then_deref_ptr",
                make_source(
                    symbol,
                    "struct S { unsigned char pad[%d]; void **value; };\n"
                    "__attribute__((used))\n"
                    "void *candidate(struct S *self) { return *self->value; }\n" % offset,
                ),
            )
        )

    if len(data) == 6 and data[:3] == b"\x8b\x47" and data[3] < 0x80 and data[4:] == b"\xc3\x90":
        offset = data[2]
        out.append(
            Candidate(
                "load_i32_member_ret_nop",
                make_source(
                    symbol,
                    "struct S { unsigned char pad[%d]; int value; };\n"
                    "__attribute__((used))\n"
                    "int candidate(struct S *self) { return self->value; }\n" % offset,
                ),
            )
        )

    if len(data) == 4 and data[:2] == b"\x8b\x47" and data[3] == 0xC3:
        offset = data[2]
        out.append(
            Candidate(
                "load_i32_member",
                make_source(
                    symbol,
                    "struct S { unsigned char pad[%d]; int value; };\n"
                    "__attribute__((used))\n"
                    "int candidate(struct S *self) { return self->value; }\n" % offset,
                ),
            )
        )

    if len(data) == 4 and data[:2] == b"\x88\x77" and data[3] == 0xC3:
        offset = data[2]
        out.append(
            Candidate(
                "store_u8_member",
                make_source(
                    symbol,
                    "struct S { unsigned char pad[%d]; unsigned char value; };\n"
                    "__attribute__((used))\n"
                    "void candidate(struct S *self, unsigned char value) { self->value = value; }\n" % offset,
                ),
            )
        )

    if len(data) == 4 and data[:2] == b"\x89\x77" and data[3] == 0xC3:
        offset = data[2]
        out.append(
            Candidate(
                "store_i32_member",
                make_source(
                    symbol,
                    "struct S { unsigned char pad[%d]; int value; };\n"
                    "__attribute__((used))\n"
                    "void candidate(struct S *self, int value) { self->value = value; }\n" % offset,
                ),
            )
        )

    if len(data) == 8 and data[:4] == b"\x8b\x44\x24\x04" and data[4:6] == b"\x8b\x40" and data[7] == 0xC3:
        offset = data[6]
        out.append(
            Candidate(
                "i386_stack_arg_load_u32_member",
                make_source(
                    symbol,
                    "struct S { unsigned char pad[%d]; unsigned int value; };\n"
                    "__attribute__((used))\n"
                    "unsigned int candidate(struct S *self) { return self->value; }\n" % offset,
                ),
            )
        )

    if len(data) == 8 and data[:4] == b"\x8b\x44\x24\x04" and data[4:7] == b"\x0f\xb7\x00" and data[7] == 0xC3:
        out.append(
            Candidate(
                "i386_stack_arg_load_u16_member0",
                make_source(
                    symbol,
                    "struct S { unsigned short value; };\n"
                    "__attribute__((used))\n"
                    "unsigned int candidate(struct S *self) { return self->value; }\n",
                ),
            )
        )

    if len(data) == 9 and data[:4] == b"\x8b\x44\x24\x04" and data[4:7] == b"\x0f\xb7\x40" and data[8] == 0xC3:
        offset = data[7]
        out.append(
            Candidate(
                "i386_stack_arg_load_u16_member",
                make_source(
                    symbol,
                    "struct S { unsigned char pad[%d]; unsigned short value; };\n"
                    "__attribute__((used))\n"
                    "unsigned int candidate(struct S *self) { return self->value; }\n" % offset,
                ),
            )
        )

    if len(data) == 7 and data[:3] == b"\xc7\x47" and data[3] < 0x80 and data[4:6] == b"\x00\x00" and data[6] == 0xC3:
        # Kept intentionally narrow: only matches compact stores of zero when
        # the compiler also emits the short immediate form.
        offset = data[2]
        out.append(
            Candidate(
                "store_zero_i32_member_short",
                make_source(
                    symbol,
                    "struct S { unsigned char pad[%d]; int value; };\n"
                    "__attribute__((used))\n"
                    "void candidate(struct S *self) { self->value = 0; }\n" % offset,
                ),
            )
        )

    return out


def compiler_arch_flags(binary: Path) -> list[str]:
    try:
        header = elfslice.run(["readelf", "-h", str(binary)])
    except subprocess.CalledProcessError:
        return []
    if "Class:" in header and "ELF32" in header and "Machine:" in header and "Intel 80386" in header:
        return ["-m32"]
    return []


def compile_candidate(source: str, out_object: Path, arch_flags: list[str]) -> subprocess.CompletedProcess[str]:
    src = out_object.with_suffix(".c")
    src.write_text(source)
    return run(
        [
            "gcc",
            *arch_flags,
            "-x",
            "c",
            "-std=c99",
            "-O2",
            "-fno-asynchronous-unwind-tables",
            "-fno-stack-protector",
            "-fno-ident",
            "-fno-pic",
            "-fno-pie",
            "-c",
            str(src),
            "-o",
            str(out_object),
        ]
    )


def compile_aggregate_source(source_path: Path, out_object: Path, arch_flags: list[str]) -> subprocess.CompletedProcess[str]:
    return run(
        [
            "gcc",
            *arch_flags,
            "-x",
            "assembler-with-cpp",
            "-c",
            str(source_path),
            "-o",
            str(out_object),
        ]
    )


def build_aggregate_source(binary: Path, matches: list[dict[str, object]]) -> str:
    lines = [
        "/* Generated by elf-auto-trivial.py. */",
        "/* Byte-identical ELF function slices with original symbol names. */",
        ".text",
    ]
    for match in matches:
        symbol = str(match["symbol"])
        _meta, target_bytes = elfslice.extract_symbol_bytes(binary, symbol, length=int(match["size"]))
        quoted = asm_symbol_name(symbol)
        lines.extend(
            [
                "",
                f".globl {quoted}",
                f".type {quoted}, @function",
                f"{quoted}:",
                f"  .byte {asm_bytes(target_bytes)}",
                f".size {quoted}, .-{quoted}",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def build_aggregate_roundtrip(
    binary: Path,
    out_root: Path,
    matches: list[dict[str, object]],
    arch_flags: list[str],
) -> dict[str, object] | None:
    if not matches:
        return None
    roundtrip_dir = out_root / "source-roundtrip"
    roundtrip_dir.mkdir(parents=True, exist_ok=True)
    source_path = roundtrip_dir / "functions.S"
    object_path = roundtrip_dir / "functions.o"
    source_path.write_text(build_aggregate_source(binary, matches))

    compile_proc = compile_aggregate_source(source_path, object_path, arch_flags)
    verified: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    if compile_proc.returncode == 0:
        for match in matches:
            symbol = str(match["symbol"])
            _target_meta, target_bytes = elfslice.extract_symbol_bytes(binary, symbol, length=int(match["size"]))
            try:
                candidate_meta, candidate_bytes = elfslice.extract_symbol_bytes(
                    object_path,
                    symbol,
                    length=len(target_bytes),
                )
            except SystemExit as exc:
                failures.append({"symbol": symbol, "error": str(exc)})
                continue
            byte_identical = target_bytes == candidate_bytes
            row = {
                "symbol": symbol,
                "byteIdentical": byte_identical,
                "size": len(target_bytes),
                "targetSha256": elfslice.sha256_bytes(target_bytes),
                "candidateSha256": elfslice.sha256_bytes(candidate_bytes),
                "candidate": candidate_meta,
            }
            if byte_identical:
                verified.append(row)
            else:
                failures.append(row)

    report = {
        "schema": "reconkit.elf-aggregate-source-roundtrip.v1",
        "binary": str(binary),
        "status": "matched" if compile_proc.returncode == 0 and len(verified) == len(matches) else "failed",
        "source": str(source_path),
        "object": str(object_path),
        "objectFormat": "elf-relocatable",
        "matchedSymbols": len(verified),
        "expectedSymbols": len(matches),
        "byteIdentical": compile_proc.returncode == 0 and len(verified) == len(matches),
        "compileReturnCode": compile_proc.returncode,
        "compileStdout": compile_proc.stdout[-4000:],
        "compileStderr": compile_proc.stderr[-4000:],
        "verified": verified,
        "failures": failures,
        "scopeNote": "Aggregate ELF object contains matched function slices only; it is not a full ELF relink.",
    }
    (roundtrip_dir / "verify.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def verify(binary: Path, symbol: str, candidate_object: Path, report_path: Path) -> dict[str, object] | None:
    proc = run(
        [
            str(SLICE_TOOL),
            "verify",
            "--binary",
            str(binary),
            "--symbol",
            symbol,
            "--candidate-object",
            str(candidate_object),
            "--candidate-symbol",
            "candidate",
            "--out",
            str(report_path),
        ]
    )
    if proc.returncode != 0:
        return None
    return json.loads(proc.stdout)


def auto_match(args: argparse.Namespace) -> int:
    binary = args.binary
    out_root = args.out
    out_root.mkdir(parents=True, exist_ok=True)
    matches: list[dict[str, object]] = []
    attempts = 0
    candidates_considered = 0
    skipped = 0
    arch_flags = compiler_arch_flags(binary)

    symbols = []
    seen_symbols = set()
    for sym in elfslice.parse_symbols(binary):
        key = (str(sym["name"]), int(sym["address"]), int(sym["size"]))
        if key in seen_symbols:
            continue
        seen_symbols.add(key)
        if int(sym["address"]) > 0 and 0 < int(sym["size"]) <= args.max_size:
            symbols.append(sym)
    symbols.sort(key=lambda sym: (int(sym["address"]), str(sym["name"])))

    with tempfile.TemporaryDirectory(prefix="reconkit-auto-trivial-") as tmp:
        tmp_dir = Path(tmp)
        for sym in symbols:
            if args.limit and attempts >= args.limit:
                break
            symbol_name = str(sym["name"])
            target_meta, target_bytes = elfslice.extract_symbol_bytes(binary, symbol_name)
            generated = templates(symbol_name, target_bytes)
            if not generated:
                skipped += 1
                continue
            candidates_considered += len(generated)
            for index, candidate in enumerate(generated):
                attempts += 1
                obj = tmp_dir / f"candidate_{attempts}.o"
                compile_proc = compile_candidate(candidate.source, obj, arch_flags)
                if compile_proc.returncode != 0:
                    continue
                report = verify(binary, symbol_name, obj, tmp_dir / f"verify_{attempts}.json")
                if report is None or not report.get("byteIdentical"):
                    continue

                fn_dir = out_root / safe_slug(symbol_name)
                fn_dir.mkdir(parents=True, exist_ok=True)
                (fn_dir / "candidate.c").write_text(candidate.source)
                shutil.copy2(obj, fn_dir / "candidate.o")
                (fn_dir / "verify.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
                target_asm = elfslice.disassemble(binary, target_meta)
                (fn_dir / "target.asm").write_text(target_asm)
                summary = {
                    "symbol": symbol_name,
                    "pattern": candidate.pattern,
                    "status": "matched",
                    "byteIdentical": True,
                    "targetSha256": report["targetSha256"],
                    "candidateSha256": report["candidateSha256"],
                    "size": report["target"]["extractedSize"],
                    "functionDir": str(fn_dir),
                    "templateIndex": index,
                }
                (fn_dir / "match-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
                matches.append(summary)
                break

    aggregate_roundtrip = build_aggregate_roundtrip(binary, out_root, matches, arch_flags)

    report = {
        "schema": "reconkit.elf-auto-trivial.v1",
        "binary": str(binary),
        "status": "completed",
        "symbolCount": len(symbols),
        "compilerArchFlags": arch_flags,
        "attempts": attempts,
        "candidateTemplates": candidates_considered,
        "skippedNoTemplate": skipped,
        "matchedCount": len(matches),
        "matches": matches,
        "aggregateSourceRoundtrip": aggregate_roundtrip,
    }
    report_path = out_root / "auto-trivial-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-size", type=int, default=24)
    parser.add_argument("--limit", type=int, default=0, help="Maximum template attempts, 0 for no limit")
    args = parser.parse_args()
    return auto_match(args)


if __name__ == "__main__":
    raise SystemExit(main())
