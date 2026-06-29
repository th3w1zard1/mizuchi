"""Source-shape and compiler-profile sweeps for recovered-source packages."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .package_verify import is_code_match, resolve_manifest_path, resolve_package_path, strip_trailing_padding, verify_source, verify_target_slice
from .state import atomic_write_json


DEFAULT_CLANG_PROFILES = [
    [],
    ["-O1"],
    ["-O2"],
    ["-Os"],
]

CONTROL_CALLS = {"if", "for", "while", "switch", "return", "sizeof"}


def sweep_recovered_source_package(
    package: Path,
    *,
    out_dir: Path | None = None,
    clang: str = "clang",
    clang_args: list[str] | None = None,
    clang_profiles: list[list[str]] | None = None,
    timeout: int = 30,
    clang_target: str | None = "i686-pc-windows-msvc",
    objcopy: str = "objcopy",
    objdump: str = "objdump",
    max_variants_per_function: int = 8,
) -> dict[str, Any]:
    manifest_path = resolve_manifest_path(package)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    package_dir = manifest_path.parent
    sweep_dir = out_dir or package_dir / "sweep"
    sweep_dir.mkdir(parents=True, exist_ok=True)
    attempts_path = sweep_dir / "attempts.jsonl"

    profiles = clang_profiles or DEFAULT_CLANG_PROFILES
    base_args = clang_args or []
    function_results: list[dict[str, Any]] = []
    attempts_written = 0

    with attempts_path.open("w", encoding="utf-8") as attempts_out:
        for fn in manifest.get("functions", []):
            source = resolve_package_path(package_dir, fn.get("source"))
            metadata = resolve_package_path(package_dir, fn.get("metadata"))
            meta = read_json(metadata)
            meta["_metadataPath"] = str(metadata)
            original = source.read_text(encoding="utf-8", errors="replace") if source.exists() else ""
            variants = generate_source_variants(original, meta, max_variants_per_function)
            best: dict[str, Any] | None = None
            matched_attempts: list[dict[str, Any]] = []
            semantic_matched_attempts: list[dict[str, Any]] = []

            for variant_index, variant in enumerate(variants):
                for profile_index, profile_args in enumerate(profiles):
                    attempt_id = safe_attempt_id(meta, variant["name"], profile_args, variant_index, profile_index)
                    attempt_dir = sweep_dir / "attempts" / attempt_id
                    attempt_dir.mkdir(parents=True, exist_ok=True)
                    variant_source = attempt_dir / "candidate.c"
                    variant_source.write_text(variant["source"], encoding="utf-8")
                    result = verify_source(
                        source=variant_source,
                        metadata=metadata,
                        out_dir=attempt_dir,
                        clang=clang,
                        clang_args=[*base_args, *profile_args],
                        timeout=timeout,
                        object_compile=True,
                        clang_target=clang_target,
                        code_compare=True,
                        objcopy=objcopy,
                        objdump=objdump,
                    )
                    attempt = summarize_attempt(meta, variant, profile_args, variant_source, result)
                    attempts_out.write(json.dumps(attempt, sort_keys=True) + "\n")
                    attempts_written += 1
                    if is_code_match(attempt.get("codeCompareStatus")):
                        matched_attempts.append(attempt)
                        if attempt.get("semanticSource"):
                            semantic_matched_attempts.append(attempt)
                    if best is None or int(attempt.get("score") or 0) > int(best.get("score") or 0):
                        best = attempt

            function_results.append(
                {
                    "name": meta.get("name") or source.stem,
                    "address": meta.get("address"),
                    "source": str(source),
                    "metadata": str(metadata),
                    "variantCount": len(variants),
                    "profileCount": len(profiles),
                    "attempts": len(variants) * len(profiles),
                    "matched": bool(matched_attempts),
                    "semanticMatched": bool(semantic_matched_attempts),
                    "matchedAttempts": matched_attempts[:5],
                    "semanticMatchedAttempts": semantic_matched_attempts[:5],
                    "bestAttempt": best,
                }
            )

    matched_functions = sum(1 for row in function_results if row.get("matched"))
    semantic_matched_functions = sum(1 for row in function_results if row.get("semanticMatched"))
    report = {
        "schema": "mizuchi.recovered-source-sweep.v1",
        "status": sweep_status(len(function_results), matched_functions, semantic_matched_functions),
        "package": str(package_dir),
        "manifest": str(manifest_path),
        "outDir": str(sweep_dir),
        "attemptsPath": str(attempts_path),
        "functions": len(function_results),
        "matchedFunctions": matched_functions,
        "semanticMatchedFunctions": semantic_matched_functions,
        "attempts": attempts_written,
        "clangProfiles": profiles,
        "baseClangArgs": base_args,
        "results": function_results,
        "claimBoundary": "semantic source matches require generated C-shape candidates to match code bytes; inline-assembly fallback matches are code recovery evidence, not semantic C decompilation",
    }
    atomic_write_json(sweep_dir / "sweep.json", report)
    return report


def generate_source_variants(source: str, meta: dict[str, Any], max_variants: int) -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = [{"name": "decompiler-original", "source": source, "sourceKind": "decompiler-c", "semanticSource": True}]
    prototypes = infer_stdcall_prototypes(source, str(meta.get("name") or ""))
    if prototypes:
        variants.append({"name": "stdcall-dllimport-prototypes", "source": prototypes + "\n\n" + source, "sourceKind": "decompiler-c-with-prototypes", "semanticSource": True})

    relational = normalize_positive_relations(source)
    if relational != source:
        variants.append({"name": "positive-relation-normalized", "source": relational, "sourceKind": "decompiler-c-normalized", "semanticSource": True})
        if prototypes:
            variants.append({"name": "stdcall-dllimport-prototypes-positive-relation-normalized", "source": prototypes + "\n\n" + relational, "sourceKind": "decompiler-c-with-prototypes-normalized", "semanticSource": True})

    for repaired in outparam_alias_variants(source):
        variants.append(repaired)
        if prototypes:
            variants.append(
                {
                    "name": f"stdcall-dllimport-prototypes-{repaired['name']}",
                    "source": prototypes + "\n\n" + repaired["source"],
                    "sourceKind": "decompiler-c-with-prototypes-outparam-repair",
                    "semanticSource": True,
                }
            )

    inline_asm = target_slice_inline_asm_variant(meta)
    if inline_asm is not None:
        variants.append(inline_asm)

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for variant in variants:
        digest = hashlib.sha256(variant["source"].encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        deduped.append(variant)
        if len(deduped) >= max(1, max_variants):
            break
    return deduped


def target_slice_inline_asm_variant(meta: dict[str, Any]) -> dict[str, Any] | None:
    target_slice = meta.get("targetSlice")
    if isinstance(target_slice, dict):
        target_slice = {**target_slice, "metadataPath": str(meta.get("_metadataPath") or "")}
    verified = verify_target_slice(target_slice)
    if verified.get("status") != "complete":
        return None
    bytes_path = verified.get("resolvedBytesPath")
    if not bytes_path:
        return None
    body = strip_trailing_padding(Path(str(bytes_path)).read_bytes())
    instructions = decode_x86_subset(body)
    if instructions is None:
        return None
    name = c_identifier(str(meta.get("name") or "recovered_function"))
    lines = [
        "/*",
        " * Generated inline-assembly fallback from the packaged target slice.",
        " * This is code-byte recovery evidence, not semantic C decompilation.",
        " * Do not promote this variant as semantic recovered C.",
        " */",
        f"__declspec(naked) void {name}(void) {{",
        "  __asm {",
    ]
    for line in instructions:
        if line.endswith(":"):
            lines.append(f"  {line}")
        else:
            lines.append(f"    {line}")
    lines.extend(["  }", "}", ""])
    return {"name": "target-slice-inline-asm", "source": "\n".join(lines), "sourceKind": "target-slice-inline-asm", "semanticSource": False}


def decode_x86_subset(data: bytes) -> list[str] | None:
    decoded: list[dict[str, Any]] = []
    labels: set[int] = set()
    offset = 0
    while offset < len(data):
        start = offset
        opcode = data[offset]
        instruction: str | None = None
        target: int | None = None
        size = 1

        if opcode == 0x50:
            instruction = "push eax"
        elif opcode == 0x51:
            instruction = "push ecx"
        elif opcode == 0x56:
            instruction = "push esi"
        elif opcode == 0x59:
            instruction = "pop ecx"
        elif opcode == 0x5E:
            instruction = "pop esi"
        elif opcode == 0x90:
            instruction = "nop"
        elif opcode == 0xCC:
            instruction = "int 3"
        elif opcode == 0xC3:
            instruction = "ret"
        elif opcode == 0x6A and offset + 1 < len(data):
            imm = data[offset + 1]
            instruction = f"push {format_imm8(imm)}"
            size = 2
        elif opcode == 0xA1 and offset + 4 < len(data):
            imm = read_u32(data, offset + 1)
            instruction = f"mov eax, dword ptr [0x{imm:08x}]"
            size = 5
        elif opcode == 0xA3 and offset + 4 < len(data):
            imm = read_u32(data, offset + 1)
            instruction = f"mov dword ptr [0x{imm:08x}], eax"
            size = 5
        elif opcode == 0x8B and offset + 5 < len(data) and data[offset + 1] == 0x35:
            imm = read_u32(data, offset + 2)
            instruction = f"mov esi, dword ptr [0x{imm:08x}]"
            size = 6
        elif opcode == 0x8B and offset + 3 < len(data) and data[offset + 1 : offset + 3] == b"\x44\x24":
            disp = data[offset + 3]
            instruction = f"mov eax, dword ptr [esp + {format_imm8(disp)}]"
            size = 4
        elif opcode == 0x8D and offset + 3 < len(data) and data[offset + 1 : offset + 3] == b"\x44\x24":
            disp = data[offset + 3]
            instruction = f"lea eax, [esp + {format_imm8(disp)}]"
            size = 4
        elif opcode == 0xC7 and offset + 7 < len(data) and data[offset + 1 : offset + 3] == b"\x44\x24":
            disp = data[offset + 3]
            imm = read_u32(data, offset + 4)
            instruction = f"mov dword ptr [esp + {format_imm8(disp)}], 0x{imm:x}"
            size = 8
        elif opcode == 0x85 and offset + 1 < len(data) and data[offset + 1] == 0xC0:
            instruction = "test eax, eax"
            size = 2
        elif opcode == 0x83 and offset + 2 < len(data) and data[offset + 1] == 0xF8:
            instruction = f"cmp eax, {format_imm8(data[offset + 2])}"
            size = 3
        elif opcode in {0x74, 0x7C, 0x7E} and offset + 1 < len(data):
            mnemonic = {0x74: "je", 0x7C: "jl", 0x7E: "jle"}[opcode]
            target = offset + 2 + signed_i8(data[offset + 1])
            instruction = f"{mnemonic} L_{target:04x}"
            labels.add(target)
            size = 2
        elif opcode == 0xFF and offset + 1 < len(data) and data[offset + 1] == 0xD6:
            instruction = "call esi"
            size = 2
        elif opcode == 0xFF and offset + 5 < len(data) and data[offset + 1] == 0x15:
            imm = read_u32(data, offset + 2)
            instruction = f"call dword ptr [0x{imm:08x}]"
            size = 6

        if instruction is None:
            return None
        decoded.append({"offset": start, "size": size, "instruction": instruction, "target": target})
        offset += size

    if any(label < 0 or label > len(data) for label in labels):
        return None
    lines: list[str] = []
    for row in decoded:
        if row["offset"] in labels:
            lines.append(f"L_{int(row['offset']):04x}:")
        lines.append(str(row["instruction"]))
    if len(data) in labels:
        lines.append(f"L_{len(data):04x}:")
    return lines


def read_u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "little")


def signed_i8(value: int) -> int:
    return value - 0x100 if value & 0x80 else value


def format_imm8(value: int) -> str:
    return str(value) if value < 10 else f"0x{value:x}"


def c_identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"recovered_{cleaned}"
    return cleaned


def infer_stdcall_prototypes(source: str, function_name: str) -> str:
    calls = find_calls(source)
    prototypes = []
    for name, arity in sorted(calls.items()):
        if name == function_name or name in CONTROL_CALLS:
            continue
        if not looks_external_call(name):
            continue
        args = "void" if arity == 0 else ",".join(["unsigned int"] * arity)
        prototypes.append(f"__declspec(dllimport) int __stdcall {name}({args});")
    return "\n".join(prototypes)


def find_calls(source: str) -> dict[str, int]:
    calls: dict[str, int] = {}
    pattern = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    for match in pattern.finditer(source):
        name = match.group(1)
        end = find_matching_paren(source, match.end() - 1)
        if end is None:
            continue
        args = source[match.end() : end]
        calls[name] = max(calls.get(name, 0), count_call_args(args))
    return calls


def looks_external_call(name: str) -> bool:
    if name.startswith(("sub_", "FUN_", "func_")):
        return False
    return bool(re.search(r"[A-Z]", name))


def find_matching_paren(source: str, open_index: int) -> int | None:
    depth = 0
    for index in range(open_index, len(source)):
        char = source[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def count_call_args(args: str) -> int:
    stripped = args.strip()
    if not stripped or stripped == "void":
        return 0
    depth = 0
    count = 1
    for char in stripped:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            count += 1
    return count


def normalize_positive_relations(source: str) -> str:
    return re.sub(r"\b0\s*<\s*([A-Za-z_][A-Za-z0-9_]*)\b", r"\1 >= 1", source)


def outparam_alias_variants(source: str) -> list[dict[str, str]]:
    stack_vars = sorted(set(re.findall(r"&\s*(uStack_[A-Za-z0-9_]+)", source)))
    compared_vars = sorted(set(re.findall(r"\b0\s*<\s*([A-Za-z_][A-Za-z0-9_]*)\b", source)))
    variants = []
    for stack_var in stack_vars[:3]:
        for compared_var in compared_vars[:3]:
            if stack_var == compared_var:
                continue
            replaced = replace_condition_value_uses(source, compared_var, stack_var)
            if replaced != source:
                variants.append(
                    {
                        "name": f"outparam-alias-{compared_var}-to-{stack_var}",
                        "source": replaced,
                        "sourceKind": "decompiler-c-outparam-repair",
                        "semanticSource": True,
                    }
                )
    return variants


def replace_condition_value_uses(source: str, old: str, new: str) -> str:
    escaped = re.escape(old)
    replaced = re.sub(rf"\b0\s*<\s*{escaped}\b", f"0 < {new}", source)
    replaced = re.sub(rf"=\s*{escaped}\s*;", f"= {new};", replaced)
    return replaced


def summarize_attempt(
    meta: dict[str, Any],
    variant: dict[str, str],
    profile_args: list[str],
    variant_source: Path,
    result: dict[str, Any],
) -> dict[str, Any]:
    code_compare = result.get("codeCompare") or {}
    attempt = {
        "schema": "mizuchi.recovered-source-sweep-attempt.v1",
        "name": meta.get("name") or result.get("name"),
        "address": meta.get("address") or result.get("address"),
        "variant": variant["name"],
        "sourceKind": variant.get("sourceKind", "decompiler-c"),
        "semanticSource": bool(variant.get("semanticSource", True)),
        "profileArgs": profile_args,
        "source": str(variant_source),
        "status": result.get("status"),
        "syntaxStatus": (result.get("syntax") or {}).get("status"),
        "objectStatus": (result.get("object") or {}).get("status"),
        "codeCompareStatus": code_compare.get("status"),
        "candidateSize": code_compare.get("candidateSize"),
        "targetSize": code_compare.get("targetSize"),
        "targetBodySize": code_compare.get("targetBodySize"),
        "firstDifference": code_compare.get("firstRelocationMaskedTargetPaddingTrimmedDifference")
        or code_compare.get("firstRelocationMaskedDifference")
        or code_compare.get("firstRawDifference"),
        "candidateDisassembly": code_compare.get("candidateDisassembly"),
        "targetDisassembly": code_compare.get("targetDisassembly"),
    }
    attempt["score"] = score_attempt(attempt)
    return attempt


def sweep_status(total_functions: int, matched_functions: int, semantic_matched_functions: int) -> str:
    if total_functions == 0:
        return "empty"
    if semantic_matched_functions == total_functions:
        return "matched"
    if matched_functions == total_functions:
        return "code-matched-nonsemantic-fallback"
    if matched_functions:
        return "partial-code-match"
    return "no-full-match"


def score_attempt(attempt: dict[str, Any]) -> int:
    status = attempt.get("codeCompareStatus")
    if status == "match":
        return 1_000_000
    if status == "target-padding-trimmed-match":
        return 950_000
    if status == "relocation-masked-match":
        return 900_000
    if status == "relocation-masked-target-padding-trimmed-match":
        return 850_000
    diff = attempt.get("firstDifference") or {}
    offset = int(diff.get("offset") or 0)
    candidate_size = int(attempt.get("candidateSize") or 0)
    target_body_size = int(attempt.get("targetBodySize") or attempt.get("targetSize") or 0)
    size_penalty = abs(candidate_size - target_body_size)
    return max(0, offset * 100 - size_penalty)


def safe_attempt_id(meta: dict[str, Any], variant: str, profile_args: list[str], variant_index: int, profile_index: int) -> str:
    name = str(meta.get("name") or "function")
    address = meta.get("address")
    suffix = f"{int(address):08x}" if isinstance(address, int) else hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    profile = "_".join(arg.strip("-").replace("=", "-") for arg in profile_args) or "default"
    raw = f"{name}_{suffix}_{variant_index:02d}_{profile_index:02d}_{variant}_{profile}"
    return re.sub(r"[^A-Za-z0-9_.+-]+", "_", raw).strip("._")[:160]


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
