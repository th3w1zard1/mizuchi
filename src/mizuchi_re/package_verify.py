"""Verification helpers for recovered-source packages."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .state import atomic_write_json


TYPE_SHIM = """
typedef unsigned char byte;
typedef unsigned char undefined;
typedef unsigned short undefined2;
typedef unsigned int undefined4;
typedef unsigned long long undefined8;
typedef unsigned int uint;
typedef unsigned long ulong;
typedef int BOOL;
typedef void *HANDLE;
typedef void *HWND;
typedef const char *LPCSTR;
typedef char *LPSTR;
#ifndef NULL
#define NULL ((void*)0)
#endif
""".strip()


GLOBAL_RE = re.compile(r"\b(?:DAT|UNK|PTR|iRam|uRam|bRam|sRam|wRam|dRam|qRam|fRam|g_|s_)[A-Za-z0-9_]*\b")


def verify_recovered_source_package(
    package: Path,
    *,
    out_dir: Path | None = None,
    clang: str = "clang",
    timeout: int = 30,
    object_compile: bool = True,
    clang_target: str | None = "i686-pc-windows-msvc",
) -> dict[str, Any]:
    manifest_path = resolve_manifest_path(package)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    package_dir = manifest_path.parent
    verify_dir = out_dir or package_dir / "verification"
    verify_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for fn in manifest.get("functions", []):
        source = resolve_package_path(package_dir, fn.get("source"))
        metadata = resolve_package_path(package_dir, fn.get("metadata"))
        result = verify_source(
            source=source,
            metadata=metadata,
            out_dir=verify_dir,
            clang=clang,
            timeout=timeout,
            object_compile=object_compile,
            clang_target=clang_target,
        )
        results.append(result)

    syntax_ok = sum(1 for row in results if row.get("syntax", {}).get("status") == "ok")
    object_ok = sum(1 for row in results if row.get("object", {}).get("status") == "ok")
    object_attempted = sum(1 for row in results if row.get("object", {}).get("status") not in {None, "not-run"})
    report = {
        "schema": "mizuchi.recovered-source-verification.v1",
        "status": verification_status(len(results), syntax_ok, object_attempted, object_ok),
        "package": str(package_dir),
        "manifest": str(manifest_path),
        "verifier": "clang-generated-shim",
        "attempted": len(results),
        "syntaxOk": syntax_ok,
        "syntaxFailed": len(results) - syntax_ok,
        "objectCompileAttempted": object_attempted,
        "objectCompileOk": object_ok,
        "objectCompileFailed": object_attempted - object_ok,
        "objdiff": {
            "status": "not-run",
            "reason": "package contains generated source candidates but no target function object/slice and compiler profile to compare",
        },
        "results": results,
        "claimBoundary": "syntax/object success only proves compiler acceptance with generated shims; it is not objdiff or semantic source parity",
    }
    atomic_write_json(verify_dir / "verification.json", report)
    return report


def verify_source(
    *,
    source: Path,
    metadata: Path,
    out_dir: Path,
    clang: str,
    timeout: int,
    object_compile: bool,
    clang_target: str | None,
) -> dict[str, Any]:
    stem = source.stem
    work_c = out_dir / f"{stem}.verify.c"
    syntax_stdout = out_dir / f"{stem}.syntax.stdout.txt"
    syntax_stderr = out_dir / f"{stem}.syntax.stderr.txt"
    object_stdout = out_dir / f"{stem}.object.stdout.txt"
    object_stderr = out_dir / f"{stem}.object.stderr.txt"
    object_path = out_dir / f"{stem}.o"
    meta = read_json(metadata)
    original = source.read_text(encoding="utf-8", errors="replace") if source.exists() else ""
    shim = build_shim(original)
    work_c.write_text(shim + "\n\n" + original, encoding="utf-8")
    base_command = clang_command(clang, work_c, clang_target)
    syntax_command = [*base_command, "-fsyntax-only"]
    syntax_proc = subprocess.run(
        syntax_command,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    syntax_stdout.write_text(syntax_proc.stdout, encoding="utf-8")
    syntax_stderr.write_text(syntax_proc.stderr, encoding="utf-8")

    object_result: dict[str, Any] = {"status": "not-run", "reason": "object_compile disabled"}
    if object_compile and syntax_proc.returncode == 0:
        object_command = [*base_command, "-c", "-o", str(object_path)]
        object_proc = subprocess.run(
            object_command,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        object_stdout.write_text(object_proc.stdout, encoding="utf-8")
        object_stderr.write_text(object_proc.stderr, encoding="utf-8")
        object_result = {
            "status": "ok" if object_proc.returncode == 0 and object_path.exists() else "failed",
            "returnCode": object_proc.returncode,
            "command": object_command,
            "object": str(object_path) if object_path.exists() else None,
            "stdout": str(object_stdout),
            "stderr": str(object_stderr),
            "stderrTail": object_proc.stderr[-2000:],
        }
    elif object_compile:
        object_result = {"status": "not-run", "reason": "syntax tier failed"}

    result = {
        "name": meta.get("name") or source.stem,
        "address": meta.get("address"),
        "source": str(source),
        "metadata": str(metadata),
        "verifySource": str(work_c),
        "status": "object-ok" if object_result.get("status") == "ok" else ("syntax-ok" if syntax_proc.returncode == 0 else "syntax-failed"),
        "syntax": {
            "status": "ok" if syntax_proc.returncode == 0 else "failed",
            "returnCode": syntax_proc.returncode,
            "command": syntax_command,
            "stdout": str(syntax_stdout),
            "stderr": str(syntax_stderr),
            "stderrTail": syntax_proc.stderr[-2000:],
        },
        "object": object_result,
        "objdiff": {
            "status": "not-run",
            "reason": "missing target function object/slice and compiler profile",
        },
    }
    return result


def clang_command(clang: str, source: Path, clang_target: str | None) -> list[str]:
    command = [
        clang,
        "-x",
        "c",
        "-std=gnu89",
        "-Wno-everything",
        "-fno-builtin",
    ]
    if clang_target:
        command.extend(["-target", clang_target])
    command.append(str(source))
    return command


def verification_status(total: int, syntax_ok: int, object_attempted: int, object_ok: int) -> str:
    if total == 0:
        return "empty"
    if syntax_ok != total:
        return "syntax-failed"
    if object_attempted and object_ok != object_attempted:
        return "object-failed"
    if object_attempted:
        return "object-ok"
    return "syntax-ok"


def build_shim(source: str) -> str:
    declarations = []
    for name in sorted(set(GLOBAL_RE.findall(source))):
        declarations.append(f"extern int {name};")
    return TYPE_SHIM + ("\n" + "\n".join(declarations) if declarations else "")


def resolve_manifest_path(package: Path) -> Path:
    if package.is_dir():
        return package / "manifest.json"
    return package


def resolve_package_path(package_dir: Path, value: Any) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    if path.exists():
        return path
    return package_dir / path


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
