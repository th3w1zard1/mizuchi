"""Verification helpers for recovered-source packages."""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .state import atomic_write_json


TYPE_SHIM = """
typedef unsigned char byte;
typedef unsigned char undefined;
typedef unsigned char undefined1;
typedef unsigned short undefined2;
typedef unsigned int undefined4;
typedef unsigned long long undefined8;
typedef unsigned int uint;
typedef unsigned long ulong;
typedef int code();
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
STACK_SYMBOL_RE = re.compile(r"\bstack0x[0-9A-Fa-f]+\b")


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="mizuchi-verify-run-") as tmp:
        stdout_path = Path(tmp) / "stdout.txt"
        stderr_path = Path(tmp) / "stderr.txt"
        with stdout_path.open("w+", encoding="utf-8", errors="replace") as stdout_file, stderr_path.open(
            "w+",
            encoding="utf-8",
            errors="replace",
        ) as stderr_file:
            proc = subprocess.Popen(
                command,
                cwd=cwd,
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
                return subprocess.CompletedProcess(command, 124, stdout, f"{stderr}\n{message}".strip())
            return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)


def child_processes(pid: int) -> list[int]:
    proc = subprocess.run(["pgrep", "-P", str(pid)], text=True, capture_output=True, check=False)
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


def verify_recovered_source_package(
    package: Path,
    *,
    out_dir: Path | None = None,
    compiler: str = "clang",
    clang: str = "clang",
    clang_args: list[str] | None = None,
    timeout: int = 30,
    object_compile: bool = True,
    clang_target: str | None = "i686-pc-windows-msvc",
    msvc_root: Path | None = None,
    wine: str = "wine",
    wineprefix: Path | None = None,
    code_compare: bool = False,
    objcopy: str = "objcopy",
    objdump: str = "objdump",
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
            compiler=compiler,
            clang=clang,
            clang_args=clang_args or [],
            timeout=timeout,
            object_compile=object_compile,
            clang_target=clang_target,
            msvc_root=msvc_root,
            wine=wine,
            wineprefix=wineprefix,
            code_compare=code_compare,
            objcopy=objcopy,
            objdump=objdump,
        )
        results.append(result)

    syntax_ok = sum(1 for row in results if row.get("syntax", {}).get("status") == "ok")
    object_ok = sum(1 for row in results if row.get("object", {}).get("status") == "ok")
    object_attempted = sum(1 for row in results if row.get("object", {}).get("status") not in {None, "not-run"})
    target_slices_ok = sum(1 for row in results if row.get("targetSlice", {}).get("status") == "complete")
    code_attempted = sum(1 for row in results if row.get("codeCompare", {}).get("status") not in {None, "not-run"})
    code_raw_matched = sum(1 for row in results if is_code_match(row.get("codeCompare", {}).get("status"), raw_only=True))
    code_masked_matched = sum(1 for row in results if is_code_match(row.get("codeCompare", {}).get("status"), raw_only=False) and not is_code_match(row.get("codeCompare", {}).get("status"), raw_only=True))
    aggregate_status = verification_status(
        len(results),
        syntax_ok,
        object_attempted,
        object_ok,
        code_compare=code_compare,
        code_attempted=code_attempted,
        code_raw_matched=code_raw_matched,
        code_masked_matched=code_masked_matched,
    )
    verification_tier = verification_tier_for_package_status(aggregate_status, "not-run")
    report = {
        "schema": "mizuchi.recovered-source-verification.v1",
        "status": aggregate_status,
        "verificationTier": verification_tier,
        "acceptanceGate": acceptance_gate_for_tier(verification_tier),
        "package": str(package_dir),
        "manifest": str(manifest_path),
        "verifier": "clang-generated-shim",
        "attempted": len(results),
        "syntaxOk": syntax_ok,
        "syntaxFailed": len(results) - syntax_ok,
        "objectCompileAttempted": object_attempted,
        "objectCompileOk": object_ok,
        "objectCompileFailed": object_attempted - object_ok,
        "targetSlicesOk": target_slices_ok,
        "targetSlicesMissing": len(results) - target_slices_ok,
        "codeCompareAttempted": code_attempted,
        "codeCompareRawMatched": code_raw_matched,
        "codeCompareRelocationMaskedMatched": code_masked_matched,
        "codeCompareMismatched": code_attempted - code_raw_matched - code_masked_matched,
        "objdiff": {
            "status": "not-run",
            "reason": objdiff_blocker_reason(target_slices_ok, len(results)),
        },
        "results": results,
        "claimBoundary": "syntax/object/code-byte success is not objdiff semantic source parity unless the objdiff gate also accepts the candidate",
    }
    atomic_write_json(verify_dir / "verification.json", report)
    return report


def verify_source(
    *,
    source: Path,
    metadata: Path,
    out_dir: Path,
    compiler: str,
    clang: str,
    clang_args: list[str],
    timeout: int,
    object_compile: bool,
    clang_target: str | None,
    msvc_root: Path | None,
    wine: str,
    wineprefix: Path | None,
    code_compare: bool,
    objcopy: str,
    objdump: str,
) -> dict[str, Any]:
    stem = source.stem
    syntax_stdout = out_dir / f"{stem}.syntax.stdout.txt"
    syntax_stderr = out_dir / f"{stem}.syntax.stderr.txt"
    object_stdout = out_dir / f"{stem}.object.stdout.txt"
    object_stderr = out_dir / f"{stem}.object.stderr.txt"
    object_path = out_dir / (f"{stem}.obj" if compiler == "msvc" else f"{stem}.o")
    meta = read_json(metadata)
    original = source.read_text(encoding="utf-8", errors="replace") if source.exists() else ""
    source_language = source_language_for(source, meta)
    effective_args = effective_compiler_args(clang_args, compiler, source_language, meta)
    work_c = out_dir / (f"{stem}.verify.cpp" if source_language == "c++" else f"{stem}.verify.c")
    target_slice_meta = meta.get("targetSlice")
    if isinstance(target_slice_meta, dict):
        target_slice_meta = {**target_slice_meta, "metadataPath": str(metadata)}
    target_slice = verify_target_slice(target_slice_meta)
    shim = build_shim(original)
    work_c.write_text(shim + "\n\n" + original, encoding="utf-8")
    if compiler == "msvc":
        syntax_command: list[str] = []
        object_result = compile_with_msvc(
            source=work_c,
            object_path=object_path,
            out_dir=out_dir,
            stem=stem,
            args=effective_args,
            timeout=timeout,
            msvc_root=msvc_root,
            wine=wine,
            wineprefix=wineprefix,
        ) if object_compile else {"status": "not-run", "reason": "object_compile disabled"}
        if object_compile and object_result.get("status") != "ok":
            cached_object = locate_cached_object(metadata, original)
            if cached_object is not None:
                shutil.copy2(cached_object, object_path)
                object_result = {
                    "status": "ok",
                    "returnCode": 0,
                    "command": ["cached", str(cached_object)],
                    "object": str(object_path),
                    "stdout": "",
                    "stderr": "",
                    "stderrTail": "",
                    "cacheHit": True,
                    "cacheSource": str(cached_object),
                }
        syntax_status = "ok" if object_result.get("status") == "ok" else "failed"
        syntax_return_code = object_result.get("returnCode", 1)
        syntax_stdout.write_text("", encoding="utf-8")
        syntax_stderr.write_text(
            str(object_result.get("stderrTail") or object_result.get("reason") or object_result.get("stderr") or ""),
            encoding="utf-8",
        )
    else:
        base_command = clang_command(clang, work_c, clang_target, effective_args, source_language)
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
        syntax_status = "ok" if syntax_proc.returncode == 0 else "failed"
        syntax_return_code = syntax_proc.returncode

        object_result = {"status": "not-run", "reason": "object_compile disabled"}
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
    code_compare_result = {"status": "not-run", "reason": "code_compare disabled"}
    if code_compare:
        code_compare_result = compare_object_code_to_target(
            object_result=object_result,
            target_slice=target_slice,
            out_dir=out_dir,
            stem=stem,
            objcopy=objcopy,
            objdump=objdump,
            timeout=timeout,
        )

    row_status = "object-ok" if object_result.get("status") == "ok" else ("syntax-ok" if syntax_status == "ok" else "syntax-failed")
    objdiff_block = {
        "status": "not-run",
        "reason": objdiff_blocker_reason(1 if target_slice.get("status") == "complete" else 0, 1),
    }
    verification_tier = verification_tier_for_result(
        row_status=row_status,
        object_result=object_result,
        code_compare_result=code_compare_result,
        objdiff_result=objdiff_block,
    )
    result = {
        "name": meta.get("name") or source.stem,
        "address": meta.get("address"),
        "source": str(source),
        "metadata": str(metadata),
        "verifySource": str(work_c),
        "targetSlice": target_slice,
        "compiler": compiler,
        "sourceLanguage": source_language,
        "compilerArgs": effective_args,
        "status": row_status,
        "verificationTier": verification_tier,
        "acceptanceGate": acceptance_gate_for_tier(verification_tier),
        "syntax": {
            "status": syntax_status,
            "returnCode": syntax_return_code,
            "command": syntax_command,
            "stdout": str(syntax_stdout),
            "stderr": str(syntax_stderr),
            "stderrTail": syntax_stderr.read_text(encoding="utf-8", errors="replace")[-2000:],
        },
        "object": object_result,
        "codeCompare": code_compare_result,
        "objdiff": objdiff_block,
    }
    return result


def clang_command(clang: str, source: Path, clang_target: str | None, clang_args: list[str], source_language: str = "c") -> list[str]:
    command = [
        clang,
        "-x",
        "c++" if source_language == "c++" else "c",
        "-std=gnu++98" if source_language == "c++" else "-std=gnu89",
        "-Wno-everything",
        "-fno-builtin",
    ]
    if clang_target:
        command.extend(["-target", clang_target])
    command.extend(clang_args)
    command.append(str(source))
    return command


def source_language_for(source: Path, meta: dict[str, Any]) -> str:
    language = str(meta.get("sourceLanguage") or "").lower()
    if language in {"c++", "cpp", "cxx"}:
        return "c++"
    if source.suffix.lower() in {".cc", ".cpp", ".cxx"}:
        return "c++"
    return "c"


def effective_compiler_args(base_args: list[str], compiler: str, source_language: str, meta: dict[str, Any]) -> list[str]:
    args = list(base_args)
    hints = meta.get("compilerProfileHints")
    if isinstance(hints, dict):
        hinted_compiler = str(hints.get("compiler") or "").lower()
        hinted_args = hints.get("args")
        if (not hinted_compiler or hinted_compiler == compiler) and isinstance(hinted_args, list):
            for item in hinted_args:
                if isinstance(item, str) and item and item not in args:
                    args.append(item)
    if compiler == "msvc" and source_language == "c++" and not any(arg.upper() == "/TP" for arg in args):
        args.append("/TP")
    return args


def locate_cached_object(metadata: Path, source_text: str) -> Path | None:
    package_dir = metadata.parent.parent
    attempts_dir = package_dir / "sweep" / "attempts"
    if not attempts_dir.exists():
        return None
    source_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    for candidate_source in attempts_dir.rglob("candidate.c"):
        try:
            candidate_hash = hashlib.sha256(candidate_source.read_text(encoding="utf-8", errors="replace").encode("utf-8")).hexdigest()
        except OSError:
            continue
        if candidate_hash != source_hash:
            continue
        for name in ("candidate.obj", "candidate.o", "out.obj"):
            candidate_object = candidate_source.parent / name
            if candidate_object.exists():
                return candidate_object
    return None


def compile_with_msvc(
    *,
    source: Path,
    object_path: Path,
    out_dir: Path,
    stem: str,
    args: list[str],
    timeout: int,
    msvc_root: Path | None,
    wine: str,
    wineprefix: Path | None,
) -> dict[str, Any]:
    root = resolve_msvc_root(msvc_root)
    cl_exe = root / "bin" / "cl.exe"
    ml_exe = root / "bin" / "ml.exe"
    stdout_path = out_dir / f"{stem}.object.stdout.txt"
    stderr_path = out_dir / f"{stem}.object.stderr.txt"
    artifact_work_dir = out_dir / f"{stem}.msvc-work"
    if artifact_work_dir.exists():
        shutil.rmtree(artifact_work_dir)
    artifact_work_dir.mkdir(parents=True, exist_ok=True)
    if not cl_exe.exists():
        message = f"cl.exe not found at {cl_exe}; pass --msvc-root or set VC_ROOT"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text(message, encoding="utf-8")
        return {"status": "failed", "returnCode": 3, "reason": message, "stdout": str(stdout_path), "stderr": str(stderr_path), "stderrTail": message}

    source_suffix = source.suffix.lower()
    is_assembler = source_suffix == ".asm"
    if is_assembler and not ml_exe.exists():
        message = f"ml.exe not found at {ml_exe}; pass --msvc-root or set VC_ROOT"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text(message, encoding="utf-8")
        return {"status": "failed", "returnCode": 3, "reason": message, "stdout": str(stdout_path), "stderr": str(stderr_path), "stderrTail": message}

    source_text = source.read_text(encoding="utf-8", errors="replace")
    source_name = "in.asm" if is_assembler else "in.c"
    (artifact_work_dir / source_name).write_text(source_text, encoding="utf-8")
    out_name = "out.obj"
    if is_assembler:
        command = [wine, str(ml_exe), "/nologo", "/c", f"/Fo{out_name}", source_name]
    else:
        command = [wine, str(cl_exe), "/nologo", "/c", *args, f"/Fo{out_name}", source_name]
    env = msvc_environment(root, wineprefix)
    with tempfile.TemporaryDirectory(prefix=f"mizuchi-msvc-{stem}-") as tmp:
        work_dir = Path(tmp)
        local_source = work_dir / source_name
        local_source.write_text(source_text, encoding="utf-8")
        proc = run_command(command, cwd=work_dir, env=env, timeout=timeout)
        produced = work_dir / out_name
        if produced.exists():
            shutil.copy2(produced, artifact_work_dir / out_name)
        if proc.returncode == 0 and produced.exists():
            shutil.copy2(produced, object_path)
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
        "compilerRoot": str(root),
        "wineprefix": env.get("WINEPREFIX"),
        "artifactWorkDir": str(artifact_work_dir),
    }


def resolve_msvc_root(msvc_root: Path | None) -> Path:
    if msvc_root is not None:
        return msvc_root
    env_root = os.environ.get("VC_ROOT")
    if env_root:
        return Path(env_root)
    return Path("/run/media/brunner56/MyBook/MizuchiSource/toolchains/msvc8.0-main")


def msvc_environment(msvc_root: Path, wineprefix: Path | None) -> dict[str, str]:
    env = dict(os.environ)
    prefix = wineprefix or Path(env.get("WINEPREFIX") or "target/toolchain-acquire/vctoolkit2003/wineprefix")
    env["WINEPREFIX"] = str(prefix.expanduser().resolve())
    env["WINEDEBUG"] = env.get("WINEDEBUG", "-all")
    env["WINEPATH"] = str(msvc_root / "bin")
    include_dirs = [msvc_root / "include", msvc_root / "PlatformSDK" / "include"]
    env["INCLUDE"] = ";".join(wine_unix_path(path) for path in include_dirs if path.exists())
    return env


def wine_unix_path(path: Path) -> str:
    return "Z:" + str(path).replace("/", "\\")


def verification_status(
    total: int,
    syntax_ok: int,
    object_attempted: int,
    object_ok: int,
    *,
    code_compare: bool = False,
    code_attempted: int = 0,
    code_raw_matched: int = 0,
    code_masked_matched: int = 0,
) -> str:
    if total == 0:
        return "empty"
    if syntax_ok != total:
        return "syntax-failed"
    if object_attempted and object_ok != object_attempted:
        return "object-failed"
    if code_compare:
        if code_attempted != total:
            return "code-compare-incomplete"
        if code_raw_matched == total:
            return "code-match"
        if code_raw_matched + code_masked_matched == total:
            return "code-relocation-masked-match"
        return "code-mismatch"
    if object_attempted:
        return "object-ok"
    return "syntax-ok"


def is_code_match(status: Any, *, raw_only: bool = False) -> bool:
    if raw_only:
        return status in {"match", "target-padding-trimmed-match"}
    return status in {
        "match",
        "relocation-masked-match",
        "target-padding-trimmed-match",
        "relocation-masked-target-padding-trimmed-match",
    }


def verify_target_slice(target_slice: Any) -> dict[str, Any]:
    if not isinstance(target_slice, dict):
        return {"status": "missing", "reason": "metadata has no targetSlice object"}
    bytes_path = target_slice.get("packagedBytesPath") or target_slice.get("bytesPath")
    if not bytes_path:
        return {**target_slice, "status": "missing-bytes-path"}
    path = Path(str(bytes_path))
    if not path.is_absolute() and not path.exists():
        metadata_path = target_slice.get("metadataPath")
        if metadata_path:
            metadata_dir = Path(str(metadata_path)).parent
            for candidate in (metadata_dir / path.name, metadata_dir / path):
                if candidate.exists():
                    path = candidate
                    break
    if not path.exists():
        return {**target_slice, "status": "missing-bytes-file", "resolvedBytesPath": str(path)}
    digest = file_sha256(path)
    expected = target_slice.get("bytesSha256")
    if expected and digest != expected:
        return {**target_slice, "status": "sha256-mismatch", "resolvedBytesPath": str(path), "actualSha256": digest}
    return {**target_slice, "status": "complete", "resolvedBytesPath": str(path), "actualSha256": digest}


def compare_object_code_to_target(
    *,
    object_result: dict[str, Any],
    target_slice: dict[str, Any],
    out_dir: Path,
    stem: str,
    objcopy: str,
    objdump: str,
    timeout: int,
) -> dict[str, Any]:
    object_path_value = object_result.get("object")
    if object_result.get("status") != "ok" or not object_path_value:
        return {"status": "not-run", "reason": "object tier did not produce an object file"}
    if target_slice.get("status") != "complete":
        return {"status": "not-run", "reason": "target slice is not complete", "targetSliceStatus": target_slice.get("status")}

    object_path = Path(str(object_path_value))
    target_path = Path(str(target_slice["resolvedBytesPath"]))
    candidate_text = out_dir / f"{stem}.text.bin"
    objcopy_stdout = out_dir / f"{stem}.objcopy.stdout.txt"
    objcopy_stderr = out_dir / f"{stem}.objcopy.stderr.txt"
    reloc_stdout = out_dir / f"{stem}.relocations.stdout.txt"
    reloc_stderr = out_dir / f"{stem}.relocations.stderr.txt"
    candidate_disasm = out_dir / f"{stem}.candidate.disasm.txt"
    target_disasm = out_dir / f"{stem}.target.disasm.txt"

    objcopy_proc = subprocess.run(
        [objcopy, "-O", "binary", "-j", ".text", str(object_path), str(candidate_text)],
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    objcopy_stdout.write_text(objcopy_proc.stdout, encoding="utf-8")
    objcopy_stderr.write_text(objcopy_proc.stderr, encoding="utf-8")
    if objcopy_proc.returncode != 0 or not candidate_text.exists():
        return {
            "status": "failed",
            "reason": "could not extract .text from candidate object",
            "returnCode": objcopy_proc.returncode,
            "stdout": str(objcopy_stdout),
            "stderr": str(objcopy_stderr),
            "stderrTail": objcopy_proc.stderr[-2000:],
        }

    reloc_proc = subprocess.run([objdump, "-r", str(object_path)], text=True, capture_output=True, check=False, timeout=timeout)
    reloc_stdout.write_text(reloc_proc.stdout, encoding="utf-8")
    reloc_stderr.write_text(reloc_proc.stderr, encoding="utf-8")
    write_disassembly(objdump, object_path, candidate_disasm, timeout, candidate=True)
    write_disassembly(objdump, target_path, target_disasm, timeout, candidate=False)

    candidate_bytes = candidate_text.read_bytes()
    target_bytes = target_path.read_bytes()
    relocations = parse_relocations(reloc_proc.stdout)
    mask = relocation_mask(relocations, len(candidate_bytes), len(target_bytes))
    target_body_bytes = strip_trailing_padding(target_bytes)
    target_body_mask = {index for index in mask if index < len(target_body_bytes)}
    raw_match = candidate_bytes == target_bytes
    masked_match = len(candidate_bytes) == len(target_bytes) and all(
        left == right or index in mask for index, (left, right) in enumerate(zip(candidate_bytes, target_bytes))
    )
    body_match = candidate_bytes == target_body_bytes
    masked_body_match = len(candidate_bytes) == len(target_body_bytes) and all(
        left == right or index in target_body_mask for index, (left, right) in enumerate(zip(candidate_bytes, target_body_bytes))
    )
    if raw_match:
        status = "match"
    elif masked_match:
        status = "relocation-masked-match"
    elif body_match:
        status = "target-padding-trimmed-match"
    elif masked_body_match:
        status = "relocation-masked-target-padding-trimmed-match"
    else:
        status = "mismatch"
    return {
        "status": status,
        "method": "raw-and-relocation-masked-text-section-compare",
        "candidateText": str(candidate_text),
        "targetBytes": str(target_path),
        "candidateSize": len(candidate_bytes),
        "targetSize": len(target_bytes),
        "targetBodySize": len(target_body_bytes),
        "targetTrailingPaddingBytes": len(target_bytes) - len(target_body_bytes),
        "candidateSha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "targetSha256": hashlib.sha256(target_bytes).hexdigest(),
        "targetBodySha256": hashlib.sha256(target_body_bytes).hexdigest(),
        "rawMatch": raw_match,
        "relocationMaskedMatch": masked_match,
        "targetPaddingTrimmedMatch": body_match,
        "relocationMaskedTargetPaddingTrimmedMatch": masked_body_match,
        "firstRawDifference": first_difference(candidate_bytes, target_bytes),
        "firstRelocationMaskedDifference": first_difference(candidate_bytes, target_bytes, mask),
        "firstTargetPaddingTrimmedDifference": first_difference(candidate_bytes, target_body_bytes),
        "firstRelocationMaskedTargetPaddingTrimmedDifference": first_difference(candidate_bytes, target_body_bytes, target_body_mask),
        "relocationMaskBytes": len(mask),
        "relocations": relocations[:50],
        "relocationCount": len(relocations),
        "objcopy": {"returnCode": objcopy_proc.returncode, "stdout": str(objcopy_stdout), "stderr": str(objcopy_stderr)},
        "relocationDump": {"returnCode": reloc_proc.returncode, "stdout": str(reloc_stdout), "stderr": str(reloc_stderr)},
        "candidateDisassembly": str(candidate_disasm),
        "targetDisassembly": str(target_disasm),
        "claimBoundary": "this compares candidate object .text bytes to target code-slice bytes; it is weaker than objdiff because target relocation symbols and full compiler/linker context are unavailable",
    }


def write_disassembly(objdump: str, path: Path, out_path: Path, timeout: int, *, candidate: bool) -> None:
    command = [objdump, "-dr", "-Mintel", str(path)] if candidate else [objdump, "-b", "binary", "-m", "i386", "-M", "intel", "-D", str(path)]
    proc = subprocess.run(command, text=True, capture_output=True, check=False, timeout=timeout)
    out_path.write_text(proc.stdout + proc.stderr, encoding="utf-8")


def parse_relocations(text: str) -> list[dict[str, Any]]:
    relocations: list[dict[str, Any]] = []
    pattern = re.compile(r"^\s*([0-9A-Fa-f]+)\s+(\S+)\s+(.+?)\s*$")
    for line in text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        offset_text, kind, symbol = match.groups()
        try:
            offset = int(offset_text, 16)
        except ValueError:
            continue
        relocations.append({"offset": offset, "type": kind, "symbol": symbol.strip(), "size": relocation_size(kind)})
    return relocations


def relocation_size(kind: str) -> int:
    upper = kind.upper()
    if "64" in upper:
        return 8
    if "16" in upper:
        return 2
    return 4


def strip_trailing_padding(data: bytes) -> bytes:
    end = len(data)
    while end > 0 and data[end - 1] in {0x90, 0xCC}:
        end -= 1
    return data[:end]


def relocation_mask(relocations: list[dict[str, Any]], candidate_size: int, target_size: int) -> set[int]:
    limit = min(candidate_size, target_size)
    mask: set[int] = set()
    for relocation in relocations:
        offset = int(relocation.get("offset") or 0)
        size = int(relocation.get("size") or 4)
        for index in range(offset, min(offset + size, limit)):
            mask.add(index)
    return mask


def first_difference(left: bytes, right: bytes, mask: set[int] | None = None) -> dict[str, Any] | None:
    masked = mask or set()
    for index, (left_byte, right_byte) in enumerate(zip(left, right)):
        if index in masked:
            continue
        if left_byte != right_byte:
            return {"offset": index, "candidateByte": f"{left_byte:02x}", "targetByte": f"{right_byte:02x}"}
    if len(left) != len(right):
        return {"offset": min(len(left), len(right)), "candidateSize": len(left), "targetSize": len(right), "reason": "size-mismatch"}
    return None


def objdiff_blocker_reason(target_slices_ok: int, total: int) -> str:
    if total == 0:
        return "package contains no generated source candidates"
    if target_slices_ok == 0:
        return "package contains generated source candidates but no target function slices to compare"
    return "target slices are present, but no compiler profile, relocation model, or objdiff-compatible target object is available yet"


def verification_tier_for_package_status(package_status: str, objdiff_status: Any) -> str:
    if objdiff_status == "matched":
        return "target-object-objdiff"
    mapping = {
        "empty": "generated",
        "syntax-failed": "generated",
        "syntax-ok": "generated",
        "object-failed": "generated",
        "object-ok": "object-compilable",
        "code-compare-incomplete": "code-slice",
        "code-mismatch": "code-slice",
        "code-match": "code-slice",
        "code-relocation-masked-match": "relocation-aware-code-slice",
    }
    return mapping.get(package_status, "generated")


def verification_tier_for_result(
    *,
    row_status: str,
    object_result: dict[str, Any],
    code_compare_result: dict[str, Any],
    objdiff_result: dict[str, Any],
) -> str:
    if objdiff_result.get("status") == "matched":
        return "target-object-objdiff"
    code_status = code_compare_result.get("status")
    if code_status in {
        "relocation-masked-match",
        "relocation-masked-target-padding-trimmed-match",
    }:
        return "relocation-aware-code-slice"
    if code_status in {"match", "target-padding-trimmed-match"}:
        return "code-slice"
    if code_status not in {None, "not-run"}:
        return "code-slice"
    if object_result.get("status") == "ok" or row_status == "object-ok":
        return "object-compilable"
    if row_status == "syntax-ok":
        return "generated"
    return "generated"


def acceptance_gate_for_tier(tier: str) -> str:
    gates = {
        "generated": "compile candidate with recorded compiler profile",
        "object-compilable": "compare candidate object against packaged target slice or relocation-aware target object",
        "code-slice": "promote to relocation-aware target object and run objdiff zero",
        "relocation-aware-code-slice": "run objdiff zero against full target object with relocation context",
        "target-object-objdiff": "accepted source parity at function/object tier",
    }
    return gates.get(tier, "unknown verification tier")


def build_shim(source: str) -> str:
    declarations = []
    for name in sorted(set(GLOBAL_RE.findall(source))):
        declarations.append(f"extern int {name};")
    for name in sorted(set(STACK_SYMBOL_RE.findall(source))):
        declarations.append(f"extern undefined4 {name};")
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


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
