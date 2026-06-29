"""Source-shape and compiler-profile sweeps for recovered-source packages."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .package_verify import is_code_match, resolve_manifest_path, resolve_package_path, verify_source
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
            original = source.read_text(encoding="utf-8", errors="replace") if source.exists() else ""
            variants = generate_source_variants(original, meta, max_variants_per_function)
            best: dict[str, Any] | None = None
            matched_attempts: list[dict[str, Any]] = []

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
                    "matchedAttempts": matched_attempts[:5],
                    "bestAttempt": best,
                }
            )

    matched_functions = sum(1 for row in function_results if row.get("matched"))
    report = {
        "schema": "mizuchi.recovered-source-sweep.v1",
        "status": "matched" if function_results and matched_functions == len(function_results) else "no-full-match",
        "package": str(package_dir),
        "manifest": str(manifest_path),
        "outDir": str(sweep_dir),
        "attemptsPath": str(attempts_path),
        "functions": len(function_results),
        "matchedFunctions": matched_functions,
        "attempts": attempts_written,
        "clangProfiles": profiles,
        "baseClangArgs": base_args,
        "results": function_results,
        "claimBoundary": "sweep attempts are automatically generated source-shape/compiler hypotheses; only code-byte matches are candidates for later objdiff promotion",
    }
    atomic_write_json(sweep_dir / "sweep.json", report)
    return report


def generate_source_variants(source: str, meta: dict[str, Any], max_variants: int) -> list[dict[str, str]]:
    variants: list[dict[str, str]] = [{"name": "decompiler-original", "source": source}]
    prototypes = infer_stdcall_prototypes(source, str(meta.get("name") or ""))
    if prototypes:
        variants.append({"name": "stdcall-dllimport-prototypes", "source": prototypes + "\n\n" + source})

    relational = normalize_positive_relations(source)
    if relational != source:
        variants.append({"name": "positive-relation-normalized", "source": relational})
        if prototypes:
            variants.append({"name": "stdcall-dllimport-prototypes-positive-relation-normalized", "source": prototypes + "\n\n" + relational})

    for repaired in outparam_alias_variants(source):
        variants.append(repaired)
        if prototypes:
            variants.append({"name": f"stdcall-dllimport-prototypes-{repaired['name']}", "source": prototypes + "\n\n" + repaired["source"]})

    deduped: list[dict[str, str]] = []
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
                variants.append({"name": f"outparam-alias-{compared_var}-to-{stack_var}", "source": replaced})
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
