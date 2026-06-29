"""Source-shape and compiler-profile sweeps for recovered-source packages."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from .package_verify import GLOBAL_RE, is_code_match, resolve_manifest_path, resolve_msvc_root, resolve_package_path, strip_trailing_padding, verify_source, verify_target_slice
from .state import atomic_write_json


DEFAULT_CLANG_PROFILES = [
    [],
    ["-O1"],
    ["-O2"],
    ["-Os"],
]

DEFAULT_MSVC_PROFILES = [
    ["/O2", "/GS-", "/Oy"],
    ["/O1", "/GS-", "/Oy"],
    ["/Od", "/GS-", "/Oy"],
]

CONTROL_CALLS = {"if", "for", "while", "switch", "return", "sizeof"}


def sweep_recovered_source_package(
    package: Path,
    *,
    out_dir: Path | None = None,
    compiler: str = "clang",
    clang: str = "clang",
    clang_args: list[str] | None = None,
    clang_profiles: list[list[str]] | None = None,
    timeout: int = 30,
    clang_target: str | None = "i686-pc-windows-msvc",
    msvc_root: Path | None = None,
    wine: str = "wine",
    wineprefix: Path | None = None,
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
    previous_attempts = load_attempt_cache(attempts_path)

    profiles = clang_profiles or default_profiles_for_compiler(compiler)
    base_args = clang_args or []
    function_results: list[dict[str, Any]] = []
    attempts_written = 0
    attempts_compiled = 0
    attempts_reused = 0

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
            semantic_match_found = False

            for variant_index, variant in enumerate(variants):
                for profile_index, profile_args in enumerate(profiles):
                    attempt_id = safe_attempt_id(meta, variant["name"], profile_args, variant_index, profile_index)
                    attempt_dir = sweep_dir / "attempts" / attempt_id
                    attempt_dir.mkdir(parents=True, exist_ok=True)
                    variant_source = attempt_dir / "candidate.c"
                    variant_source.write_text(variant["source"], encoding="utf-8")
                    cache_key = attempt_cache_key(
                        meta=meta,
                        variant=variant,
                        compiler=compiler,
                        clang=clang,
                        msvc_root=msvc_root,
                        wine=wine,
                        wineprefix=wineprefix,
                        compiler_args=[*base_args, *profile_args],
                        clang_target=clang_target,
                    )
                    cached = previous_attempts.get(cache_key)
                    if cached is not None:
                        attempt = dict(cached)
                        attempt["cacheHit"] = True
                        attempt["source"] = str(variant_source)
                        attempts_reused += 1
                    else:
                        result = verify_source(
                            source=variant_source,
                            metadata=metadata,
                            out_dir=attempt_dir,
                            compiler=compiler,
                            clang=clang,
                            clang_args=[*base_args, *profile_args],
                            timeout=timeout,
                            object_compile=True,
                            clang_target=clang_target,
                            msvc_root=msvc_root,
                            wine=wine,
                            wineprefix=wineprefix,
                            code_compare=True,
                            objcopy=objcopy,
                            objdump=objdump,
                        )
                        attempt = summarize_attempt(meta, variant, profile_args, variant_source, result)
                        attempt["cacheHit"] = False
                        attempts_compiled += 1
                    attempt["attemptKey"] = cache_key
                    attempt["sourceSha256"] = source_sha256(variant["source"])
                    attempt["compiler"] = compiler
                    attempt["compilerArgs"] = [*base_args, *profile_args]
                    attempt["clangTarget"] = clang_target
                    attempt["targetSliceSha256"] = target_slice_sha256(meta)
                    attempts_out.write(json.dumps(attempt, sort_keys=True) + "\n")
                    attempts_written += 1
                    if is_code_match(attempt.get("codeCompareStatus")):
                        matched_attempts.append(attempt)
                        if attempt.get("semanticSource"):
                            semantic_matched_attempts.append(attempt)
                            semantic_match_found = True
                    if best is None or int(attempt.get("score") or 0) > int(best.get("score") or 0):
                        best = attempt
                    if semantic_match_found:
                        break
                if semantic_match_found:
                    break

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
        "attemptsCompiled": attempts_compiled,
        "attemptsReused": attempts_reused,
        "compilerProfiles": profiles,
        "clangProfiles": profiles,
        "compiler": compiler,
        "baseCompilerArgs": base_args,
        "baseClangArgs": base_args,
        "results": function_results,
        "claimBoundary": "semantic source matches require generated C-shape candidates to match code bytes; inline-assembly fallback matches are code recovery evidence, not semantic C decompilation",
    }
    atomic_write_json(sweep_dir / "sweep.json", report)
    return report


def load_attempt_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    attempts: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = row.get("attemptKey")
        if not isinstance(key, str) or not key:
            key = legacy_attempt_cache_key(row)
        if key:
            attempts[key] = row
    return attempts


def legacy_attempt_cache_key(row: dict[str, Any]) -> str | None:
    source = row.get("source")
    source_hash = None
    if isinstance(source, str) and Path(source).exists():
        try:
            source_hash = hashlib.sha256(Path(source).read_bytes()).hexdigest()
        except OSError:
            source_hash = None
    if source_hash is None:
        return None
    payload = {
        "name": row.get("name"),
        "address": row.get("address"),
        "variant": row.get("variant"),
        "sourceKind": row.get("sourceKind"),
        "semanticSource": row.get("semanticSource"),
        "sourceSha256": source_hash,
        "profileArgs": row.get("profileArgs") or [],
        "targetBodySize": row.get("targetBodySize"),
        "targetSize": row.get("targetSize"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def attempt_cache_key(
    *,
    meta: dict[str, Any],
    variant: dict[str, Any],
    compiler: str,
    clang: str,
    msvc_root: Path | None,
    wine: str,
    wineprefix: Path | None,
    compiler_args: list[str],
    clang_target: str | None,
) -> str:
    payload = {
        "schema": "mizuchi.recovered-source-sweep-attempt-key.v1",
        "name": meta.get("name"),
        "address": meta.get("address"),
        "variant": variant.get("name"),
        "sourceKind": variant.get("sourceKind"),
        "semanticSource": bool(variant.get("semanticSource", True)),
        "sourceSha256": source_sha256(str(variant.get("source") or "")),
        "compiler": compiler,
        "compilerTool": compiler_tool_identity(compiler, clang, msvc_root, wine, wineprefix),
        "compilerArgs": compiler_args,
        "clangTarget": clang_target,
        "targetSliceSha256": target_slice_sha256(meta),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def compiler_tool_identity(compiler: str, clang: str, msvc_root: Path | None, wine: str, wineprefix: Path | None) -> dict[str, str | None]:
    if compiler == "msvc":
        resolved_prefix = wineprefix or Path(os.environ.get("WINEPREFIX") or "target/toolchain-acquire/vctoolkit2003/wineprefix")
        return {
            "msvcRoot": str(resolve_msvc_root(msvc_root)),
            "wine": wine,
            "wineprefix": str(resolved_prefix.expanduser().resolve()),
        }
    return {"clang": clang}


def source_sha256(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def target_slice_sha256(meta: dict[str, Any]) -> str | None:
    target_slice = meta.get("targetSlice")
    if isinstance(target_slice, dict):
        expected = target_slice.get("bytesSha256")
        if expected:
            return str(expected)
        packaged = target_slice.get("packagedBytesPath") or target_slice.get("bytesPath")
        if packaged:
            path = Path(str(packaged))
            if not path.is_absolute() and not path.exists() and meta.get("_metadataPath"):
                path = Path(str(meta["_metadataPath"])).parent / path.name
            if path.exists():
                return hashlib.sha256(path.read_bytes()).hexdigest()
    return None


def default_profiles_for_compiler(compiler: str) -> list[list[str]]:
    return DEFAULT_MSVC_PROFILES if compiler == "msvc" else DEFAULT_CLANG_PROFILES


def generate_source_variants(source: str, meta: dict[str, Any], max_variants: int) -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = []

    def add_semantic_variant(variant: dict[str, Any]) -> None:
        variants.append(variant)
        for recovered in signed_stack_type_variants(variant["source"]):
            variants.append(
                {
                    "name": f"{variant['name']}-{recovered['name']}",
                    "source": recovered["source"],
                    "sourceKind": f"{variant.get('sourceKind', 'decompiler-c')}-type-recovered",
                    "semanticSource": True,
                }
            )

    add_semantic_variant({"name": "decompiler-original", "source": source, "sourceKind": "decompiler-c", "semanticSource": True})
    prototypes = infer_stdcall_prototypes(source, str(meta.get("name") or ""))
    if prototypes:
        add_semantic_variant({"name": "stdcall-dllimport-prototypes", "source": prototypes + "\n\n" + source, "sourceKind": "decompiler-c-with-prototypes", "semanticSource": True})

    byte_offset = normalize_byte_offset_pointer_arithmetic(source)
    if byte_offset != source:
        add_semantic_variant({"name": "byte-offset-pointer-normalized", "source": byte_offset, "sourceKind": "decompiler-c-normalized", "semanticSource": True})
        if prototypes:
            add_semantic_variant({"name": "stdcall-dllimport-prototypes-byte-offset-pointer-normalized", "source": prototypes + "\n\n" + byte_offset, "sourceKind": "decompiler-c-with-prototypes-normalized", "semanticSource": True})

    relational = normalize_positive_relations(source)
    if relational != source:
        add_semantic_variant({"name": "positive-relation-normalized", "source": relational, "sourceKind": "decompiler-c-normalized", "semanticSource": True})
        if prototypes:
            add_semantic_variant({"name": "stdcall-dllimport-prototypes-positive-relation-normalized", "source": prototypes + "\n\n" + relational, "sourceKind": "decompiler-c-with-prototypes-normalized", "semanticSource": True})

    for lifted_variant in slice_lifted_c_variants(source, meta):
        add_semantic_variant(lifted_variant)

    for repaired in outparam_alias_variants(source):
        add_semantic_variant(repaired)
        normalized_repaired_source = normalize_positive_relations(repaired["source"])
        if normalized_repaired_source != repaired["source"]:
            add_semantic_variant(
                {
                    "name": f"{repaired['name']}-positive-relation-normalized",
                    "source": normalized_repaired_source,
                    "sourceKind": "decompiler-c-outparam-repair-normalized",
                    "semanticSource": True,
                }
            )
        if prototypes:
            prototyped_repaired = {
                "name": f"stdcall-dllimport-prototypes-{repaired['name']}",
                "source": prototypes + "\n\n" + repaired["source"],
                "sourceKind": "decompiler-c-with-prototypes-outparam-repair",
                "semanticSource": True,
            }
            add_semantic_variant(prototyped_repaired)
            if normalized_repaired_source != repaired["source"]:
                add_semantic_variant(
                    {
                        "name": f"stdcall-dllimport-prototypes-{repaired['name']}-positive-relation-normalized",
                        "source": prototypes + "\n\n" + normalized_repaired_source,
                        "sourceKind": "decompiler-c-with-prototypes-outparam-repair-normalized",
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


def signed_stack_type_variants(source: str) -> list[dict[str, str]]:
    variants = []
    for var in signed_candidate_stack_vars(source):
        declaration = re.compile(rf"\b(?:undefined4|uint|unsigned\s+int)\s+{re.escape(var)}\s*;")
        replaced = declaration.sub(f"int {var};", source, count=1)
        if replaced != source:
            variants.append(
                {
                    "name": f"signed-stack-{var}",
                    "source": replaced,
                }
            )
    return variants


def signed_candidate_stack_vars(source: str) -> list[str]:
    address_taken = set(re.findall(r"&\s*(uStack_[A-Za-z0-9_]+)", source))
    compared = set(re.findall(r"\b(uStack_[A-Za-z0-9_]+)\s*>=\s*1\b", source))
    compared.update(re.findall(r"\b0\s*<\s*(uStack_[A-Za-z0-9_]+)\b", source))
    compared.update(re.findall(r"\b(uStack_[A-Za-z0-9_]+)\s*>\s*0\b", source))
    return sorted(address_taken & compared)


def slice_lifted_c_variants(source: str, meta: dict[str, Any]) -> list[dict[str, Any]]:
    target_slice = meta.get("targetSlice")
    if isinstance(target_slice, dict):
        target_slice = {**target_slice, "metadataPath": str(meta.get("_metadataPath") or "")}
    verified = verify_target_slice(target_slice)
    if verified.get("status") != "complete":
        return []
    bytes_path = verified.get("resolvedBytesPath")
    if not bytes_path:
        return []
    body = strip_trailing_padding(Path(str(bytes_path)).read_bytes())
    rows = decode_x86_rows(body)
    if rows is None:
        return []
    prototypes = infer_stdcall_prototypes(source, str(meta.get("name") or ""))
    function_name = c_identifier(str(meta.get("name") or "recovered_function"))
    import_name = first_external_call_name(source)
    global_name = first_global_name(source)
    stack_name = first_stack_address_var(source) or "local_4"
    variants: list[dict[str, Any]] = []
    if import_name and global_name:
        simple = lift_global_threshold_call(rows, function_name, import_name, global_name, prototypes)
        if simple:
            variants.append(simple)
        outparam = lift_outparam_global_store(rows, function_name, import_name, global_name, stack_name, prototypes)
        if outparam:
            variants.append(outparam)
    return variants


def first_external_call_name(source: str) -> str | None:
    for name, _arity in sorted(find_calls(source).items()):
        if looks_external_call(name):
            return name
    return None


def first_global_name(source: str) -> str | None:
    globals_found = [name for name in sorted(set(GLOBAL_RE.findall(source))) if name.startswith(("iRam", "uRam", "DAT", "g_"))]
    return globals_found[0] if globals_found else None


def first_stack_address_var(source: str) -> str | None:
    names = sorted(set(re.findall(r"&\s*([A-Za-z_][A-Za-z0-9_]*)", source)))
    for name in names:
        if name.startswith(("uStack_", "iStack_", "local_", "stack_")):
            return name
    return names[0] if names else None


def lift_global_threshold_call(rows: list[dict[str, Any]], function_name: str, import_name: str, global_name: str, prototypes: str) -> dict[str, Any] | None:
    load_index = find_row(rows, op="mov", dst="eax", src_kind="mem_abs")
    if load_index is None:
        return None
    cmp_index = find_row(rows, op="cmp", dst="eax", start=load_index + 1)
    branch_index = find_branch_after_cmp(rows, cmp_index)
    call_index = find_row(rows, op="call", start=(branch_index or cmp_index) + 1 if cmp_index is not None else 0)
    if cmp_index is None or branch_index is None or call_index is None:
        return None
    threshold = rows[cmp_index].get("imm")
    condition = compare_condition("eax", rows[cmp_index].get("cmp"), rows[branch_index].get("op"))
    args = call_args_before(rows, call_index, {"eax": global_name})
    if threshold is None or condition is None or len(args) < 1:
        return None
    lines = [
        prototypes,
        f"void {function_name}(void)",
        "{",
        f"  if ({global_name} {condition} {format_c_int(int(threshold))}) {{",
        f"    {import_name}({','.join(args)});",
        "  }",
        "  return;",
        "}",
        "",
    ]
    return {"name": "slice-lifted-global-threshold-call", "source": "\n".join(line for line in lines if line != ""), "sourceKind": "slice-lifted-c", "semanticSource": True}


def lift_outparam_global_store(
    rows: list[dict[str, Any]],
    function_name: str,
    import_name: str,
    global_name: str,
    stack_name: str,
    prototypes: str,
) -> dict[str, Any] | None:
    calls = [index for index, row in enumerate(rows) if row.get("op") == "call"]
    if len(calls) < 2:
        return None
    first_call, second_call = calls[0], calls[1]
    stack_disp = stack_disp_loaded_before_cmp(rows, first_call, second_call)
    cmp_index = find_row(rows, op="cmp", dst="eax", start=first_call + 1)
    branch_index = find_branch_after_cmp(rows, cmp_index)
    test_index = find_row(rows, op="test", dst="eax", start=first_call + 1)
    fail_branch_index = find_row(rows, op="je", start=(test_index or first_call) + 1)
    store_index = find_row(rows, op="mov", dst_kind="mem_abs", src="eax", start=(branch_index or first_call) + 1)
    if stack_disp is None or cmp_index is None or branch_index is None or test_index is None or fail_branch_index is None or store_index is None:
        return None
    first_args = call_args_before(rows, first_call, {"eax": f"(unsigned int)&{stack_name}"})
    second_args = call_args_before(rows, second_call, {"eax": stack_name})
    threshold = rows[cmp_index].get("imm")
    condition = compare_condition(stack_name, rows[cmp_index].get("cmp"), rows[branch_index].get("op"))
    if len(first_args) < 1 or len(second_args) < 1 or threshold is None or condition is None:
        return None
    lines = [
        prototypes,
        f"void {function_name}(void)",
        "{",
        "  int iVar1;",
        f"  int {stack_name};",
        "",
        f"  {stack_name} = 0;",
        f"  iVar1 = {import_name}({','.join(first_args)});",
        f"  if ((iVar1 != 0) && ({stack_name} {condition} {format_c_int(int(threshold))})) {{",
        f"    {global_name} = {stack_name};",
        f"    {import_name}({','.join(second_args)});",
        "  }",
        "  return;",
        "}",
        "",
    ]
    return {"name": "slice-lifted-outparam-global-store", "source": "\n".join(lines), "sourceKind": "slice-lifted-c", "semanticSource": True}


def stack_disp_loaded_before_cmp(rows: list[dict[str, Any]], start: int, end: int) -> int | None:
    for row in rows[start:end]:
        if row.get("op") == "mov" and row.get("dst") == "eax" and row.get("src_kind") == "stack":
            return int(row.get("disp") or 0)
    return None


def find_row(rows: list[dict[str, Any]], *, op: str, start: int = 0, dst: str | None = None, src: str | None = None, dst_kind: str | None = None, src_kind: str | None = None) -> int | None:
    for index, row in enumerate(rows[start:], start=start):
        if row.get("op") != op:
            continue
        if dst is not None and row.get("dst") != dst:
            continue
        if src is not None and row.get("src") != src:
            continue
        if dst_kind is not None and row.get("dst_kind") != dst_kind:
            continue
        if src_kind is not None and row.get("src_kind") != src_kind:
            continue
        return index
    return None


def find_branch_after_cmp(rows: list[dict[str, Any]], cmp_index: int | None) -> int | None:
    if cmp_index is None:
        return None
    for index in range(cmp_index + 1, min(cmp_index + 4, len(rows))):
        if rows[index].get("op") in {"jl", "jle", "jg", "jge", "je", "jne"}:
            return index
    return None


def compare_condition(value: str, threshold: Any, branch_op: Any) -> str | None:
    _ = value
    if threshold is None:
        return None
    return {
        "jl": ">=",
        "jle": ">",
        "jg": "<=",
        "jge": "<",
        "je": "!=",
        "jne": "==",
    }.get(str(branch_op))


def call_args_before(rows: list[dict[str, Any]], call_index: int, register_values: dict[str, str]) -> list[str]:
    pushes: list[str] = []
    eax_value: str | None = None
    for row in rows[:call_index]:
        op = row.get("op")
        if op == "lea" and row.get("dst") == "eax" and row.get("src_kind") == "stack":
            eax_value = register_values.get("eax", "eax")
        elif op == "mov":
            if row.get("dst") == "eax" and row.get("src_kind") in {"stack", "mem_abs"}:
                eax_value = register_values.get("eax", "eax")
            elif row.get("dst_kind") == "stack" or (row.get("dst_kind") == "mem_abs" and row.get("src") == "eax"):
                pass
            else:
                pushes.clear()
                eax_value = None
        elif op == "push":
            pushes.append(push_arg_expression(row.get("value"), eax_value, register_values))
        elif op == "call":
            pushes.clear()
            eax_value = None
        elif op not in {"nop"} and pushes:
            pushes.clear()
    return list(reversed(pushes))


def push_arg_expression(value: Any, eax_value: str | None, register_values: dict[str, str]) -> str:
    if value == "eax":
        return eax_value or register_values.get("eax") or "eax"
    if isinstance(value, int):
        return format_c_int(value)
    return str(value)


def format_c_int(value: int) -> str:
    return "0" if value == 0 else f"0x{value:x}"


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
    data = Path(str(bytes_path)).read_bytes()
    body = strip_trailing_padding(data)
    instructions = decode_x86_subset(body)
    if instructions is None:
        return target_slice_byte_emit_variant(meta, data)
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


def target_slice_byte_emit_variant(meta: dict[str, Any], data: bytes) -> dict[str, Any]:
    name = c_identifier(str(meta.get("name") or "recovered_function"))
    lines = [
        "/*",
        " * Generated byte-emitting inline-assembly fallback from the packaged target slice.",
        " * This preserves code bytes as compilable plaintext source when the semantic C",
        " * variants and mnemonic inline-asm decoder do not cover the instruction shape.",
        " * Do not promote this variant as semantic recovered C.",
        " */",
        f"__declspec(naked) void {name}(void) {{",
        "  __asm {",
    ]
    for offset in range(0, len(data), 8):
        chunk = data[offset : offset + 8]
        hex_bytes = " ".join(f"{byte:02x}" for byte in chunk)
        lines.append(f"    /* {offset:04x}: {hex_bytes} */")
        for byte in chunk:
            lines.append(f"    _emit 0x{byte:02x}")
    lines.extend(["  }", "}", ""])
    return {"name": "target-slice-byte-emit-inline-asm", "source": "\n".join(lines), "sourceKind": "target-slice-inline-asm-byte-emit", "semanticSource": False}


def decode_x86_rows(data: bytes) -> list[dict[str, Any]] | None:
    decoded: list[dict[str, Any]] = []
    labels: set[int] = set()
    offset = 0
    while offset < len(data):
        start = offset
        opcode = data[offset]
        row: dict[str, Any] | None = None
        size = 1

        if opcode in {0x50, 0x51, 0x56, 0x59, 0x5E}:
            reg = {0x50: "eax", 0x51: "ecx", 0x56: "esi", 0x59: "ecx", 0x5E: "esi"}[opcode]
            row = {"op": "push" if opcode in {0x50, 0x51, 0x56} else "pop", "value": reg}
        elif opcode == 0x90:
            row = {"op": "nop"}
        elif opcode == 0xCC:
            row = {"op": "int3"}
        elif opcode == 0xC3:
            row = {"op": "ret"}
        elif opcode == 0x6A and offset + 1 < len(data):
            row = {"op": "push", "value": signed_i8(data[offset + 1])}
            size = 2
        elif opcode == 0xA1 and offset + 4 < len(data):
            imm = read_u32(data, offset + 1)
            row = {"op": "mov", "dst": "eax", "dst_kind": "reg", "src": imm, "src_kind": "mem_abs"}
            size = 5
        elif opcode == 0xA3 and offset + 4 < len(data):
            imm = read_u32(data, offset + 1)
            row = {"op": "mov", "dst": imm, "dst_kind": "mem_abs", "src": "eax", "src_kind": "reg"}
            size = 5
        elif opcode == 0x8B and offset + 5 < len(data) and data[offset + 1] == 0x35:
            imm = read_u32(data, offset + 2)
            row = {"op": "mov", "dst": "esi", "dst_kind": "reg", "src": imm, "src_kind": "mem_abs"}
            size = 6
        elif opcode == 0x8B and offset + 3 < len(data) and data[offset + 1 : offset + 3] == b"\x44\x24":
            disp = data[offset + 3]
            row = {"op": "mov", "dst": "eax", "dst_kind": "reg", "src": disp, "src_kind": "stack", "disp": disp}
            size = 4
        elif opcode == 0x8D and offset + 3 < len(data) and data[offset + 1 : offset + 3] == b"\x44\x24":
            disp = data[offset + 3]
            row = {"op": "lea", "dst": "eax", "dst_kind": "reg", "src": disp, "src_kind": "stack", "disp": disp}
            size = 4
        elif opcode == 0xC7 and offset + 7 < len(data) and data[offset + 1 : offset + 3] == b"\x44\x24":
            disp = data[offset + 3]
            imm = read_u32(data, offset + 4)
            row = {"op": "mov", "dst": disp, "dst_kind": "stack", "src": imm, "src_kind": "imm", "disp": disp}
            size = 8
        elif opcode == 0x85 and offset + 1 < len(data) and data[offset + 1] == 0xC0:
            row = {"op": "test", "dst": "eax", "src": "eax"}
            size = 2
        elif opcode == 0x83 and offset + 2 < len(data) and data[offset + 1] == 0xF8:
            imm = signed_i8(data[offset + 2])
            row = {"op": "cmp", "dst": "eax", "src": imm, "src_kind": "imm", "cmp": imm, "imm": imm}
            size = 3
        elif opcode in {0x74, 0x75, 0x7C, 0x7D, 0x7E, 0x7F} and offset + 1 < len(data):
            mnemonic = {0x74: "je", 0x75: "jne", 0x7C: "jl", 0x7D: "jge", 0x7E: "jle", 0x7F: "jg"}[opcode]
            target = offset + 2 + signed_i8(data[offset + 1])
            row = {"op": mnemonic, "target": target}
            labels.add(target)
            size = 2
        elif opcode == 0xFF and offset + 1 < len(data) and data[offset + 1] == 0xD6:
            row = {"op": "call", "target": "esi", "target_kind": "reg"}
            size = 2
        elif opcode == 0xFF and offset + 5 < len(data) and data[offset + 1] == 0x15:
            imm = read_u32(data, offset + 2)
            row = {"op": "call", "target": imm, "target_kind": "mem_abs"}
            size = 6

        if row is None:
            return None
        decoded.append({"offset": start, "size": size, **row})
        offset += size

    if any(label < 0 or label > len(data) for label in labels):
        return None
    return decoded


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


def normalize_byte_offset_pointer_arithmetic(source: str) -> str:
    """Preserve Ghidra's byte-offset global-address idiom under C pointer rules."""

    type_name = r"(?:unsigned\s+int|signed\s+int|unsigned\s+long|signed\s+long|undefined[0-9]+|uint|int|ulong|long|ushort|short|byte|uchar|char|float|double|[A-Za-z_][A-Za-z0-9_]*)"
    global_name = r"(?:UNK|DAT|PTR|LAB|iRam|uRam|g_)[A-Za-z0-9_]*"
    pattern = re.compile(
        rf"\*\s*\(\s*(?P<type>{type_name})\s*\*\s*\)\s*"
        rf"\(\s*&\s*(?P<global>{global_name})\s*\+\s*(?P<index>[A-Za-z_][A-Za-z0-9_]*)\s*\*\s*(?P<scale>0x[0-9A-Fa-f]+|\d+)\s*\)",
        re.MULTILINE,
    )

    def replace(match: re.Match[str]) -> str:
        cast_type = re.sub(r"\s+", " ", match.group("type")).strip()
        return f"*({cast_type} *)((char *)&{match.group('global')} + {match.group('index')} * {match.group('scale')})"

    return pattern.sub(replace, source)


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
