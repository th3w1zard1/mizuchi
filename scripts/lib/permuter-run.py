#!/usr/bin/env python3
"""Run decomp-permuter for a ReconstructKit prompt folder (Cursor-native bridge)."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLCHAIN = {
    "gba": (["arm-none-eabi-nm"], ["arm-none-eabi-objdump"], ["-drz"], "gcc"),
    "nds": (["arm-none-eabi-nm"], ["arm-none-eabi-objdump"], ["-drz"], "gcc"),
    "n3ds": (["arm-none-eabi-nm"], ["arm-none-eabi-objdump"], ["-drz"], "gcc"),
    "n64": (
        ["mips-linux-gnu-nm", "mips64-linux-gnu-nm", "mips64-elf-nm"],
        ["mips-linux-gnu-objdump", "mips64-linux-gnu-objdump", "mips64-elf-objdump"],
        ["-drz", "-m", "mips:4300"],
        "ido",
    ),
    "ps1": (
        ["mips-linux-gnu-nm", "mips64-linux-gnu-nm", "mips64-elf-nm"],
        ["mips-linux-gnu-objdump", "mips64-linux-gnu-objdump", "mips64-elf-objdump"],
        ["-drz", "-m", "mips:4300"],
        "ido",
    ),
    "ps2": (
        ["mips-linux-gnu-nm", "mips64-linux-gnu-nm", "mips64-elf-nm"],
        ["mips-linux-gnu-objdump", "mips64-linux-gnu-objdump", "mips64-elf-objdump"],
        ["-drz", "-m", "mips:4300"],
        "ido",
    ),
    "psp": (
        ["mips-linux-gnu-nm", "mips64-linux-gnu-nm", "mips64-elf-nm"],
        ["mips-linux-gnu-objdump", "mips64-linux-gnu-objdump", "mips64-elf-objdump"],
        ["-drz", "-m", "mips:4300"],
        "ido",
    ),
    "irix": (
        ["mips-linux-gnu-nm", "mips64-linux-gnu-nm", "mips64-elf-nm"],
        ["mips-linux-gnu-objdump", "mips64-linux-gnu-objdump", "mips64-elf-objdump"],
        ["-drz", "-m", "mips:4300"],
        "ido",
    ),
    "gc": (
        ["powerpc-eabi-nm"],
        ["powerpc-eabi-objdump"],
        ["-dr", "-EB", "-mpowerpc", "-M", "broadway"],
        "mwcc",
    ),
    "wii": (
        ["powerpc-eabi-nm"],
        ["powerpc-eabi-objdump"],
        ["-dr", "-EB", "-mpowerpc", "-M", "broadway"],
        "mwcc",
    ),
    "saturn": (["sh-elf-nm"], ["sh-elf-objdump"], ["-drz"], "gcc"),
    "dreamcast": (["sh-elf-nm"], ["sh-elf-objdump"], ["-drz"], "gcc"),
}

DEFAULT_TOOLCHAIN = (["nm"], ["objdump"], ["-drz"], "gcc")


def find_on_path(candidates: list[str]) -> str | None:
    for name in candidates:
        if shutil.which(name):
            return name
    return None


def read_yaml_field(path: Path, field: str) -> str:
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("settings.yaml root must be a mapping")
        value = data.get(field)
        if value is None or value == "":
            raise ValueError(f"settings.yaml missing required field: {field}")
        return str(value)
    except ImportError:
        text = path.read_text(encoding="utf-8")
        if field == "asm":
            m = re.search(r"^asm:\s*\|\s*\n((?:[ \t].*\n?)*)", text, re.MULTILINE)
            if not m:
                raise ValueError("settings.yaml missing asm block")
            lines = []
            for line in m.group(1).splitlines():
                if line.startswith("  "):
                    lines.append(line[2:])
                elif line.startswith("\t"):
                    lines.append(line[1:])
                else:
                    lines.append(line)
            return "\n".join(lines).rstrip("\n") + "\n"
        m = re.search(rf"^{re.escape(field)}:\s*(.+)$", text, re.MULTILINE)
        if not m:
            raise ValueError(f"settings.yaml missing field: {field}")
        return m.group(1).strip().strip('"').strip("'")


def expand_templates(template: str, mapping: dict[str, str]) -> str:
    out = template
    for key, value in mapping.items():
        out = out.replace(f"{{{{{key}}}}}", value)
    return out


def write_objdump_wrapper(path: Path, function_name: str, nm_candidates: list[str], objdump_candidates: list[str]) -> None:
    nm_list = " ".join(f'"{c}"' for c in nm_candidates)
    od_list = " ".join(f'"{c}"' for c in objdump_candidates)
    content = f"""#!/bin/bash
FUNC_NAME="{function_name}"
NM_CANDIDATES=({nm_list})
OBJDUMP_CANDIDATES=({od_list})
NM_CMD=""
for candidate in "${{NM_CANDIDATES[@]}}"; do
  if command -v "$candidate" &>/dev/null; then NM_CMD="$candidate"; break; fi
done
if [ -z "$NM_CMD" ]; then echo "No nm found" >&2; exit 1; fi
OBJDUMP_CMD=""
for candidate in "${{OBJDUMP_CANDIDATES[@]}}"; do
  if command -v "$candidate" &>/dev/null; then OBJDUMP_CMD="$candidate"; break; fi
done
if [ -z "$OBJDUMP_CMD" ]; then echo "No objdump found" >&2; exit 1; fi
ARGS=("$@")
OBJ_FILE="${{ARGS[${{#ARGS[@]}}-1]}}"
OBJDUMP_ARGS=("${{ARGS[@]:0:${{#ARGS[@]}}-1}}")
NM_OUTPUT=$("$NM_CMD" --numeric-sort "$OBJ_FILE" 2>/dev/null | grep " T ")
if [ -z "$NM_OUTPUT" ]; then exec "$OBJDUMP_CMD" "${{OBJDUMP_ARGS[@]}}" "$OBJ_FILE"; fi
FUNC_LINE=$(echo "$NM_OUTPUT" | grep " T $FUNC_NAME$")
if [ -z "$FUNC_LINE" ]; then exec "$OBJDUMP_CMD" "${{OBJDUMP_ARGS[@]}}" "$OBJ_FILE"; fi
START_ADDR=$(echo "$FUNC_LINE" | awk '{{print $1}}')
NEXT_ADDR=$(echo "$NM_OUTPUT" | awk -v addr="$START_ADDR" 'found && $1 != addr {{ print $1; exit }} $1 == addr {{ found = 1 }}')
if [ -n "$NEXT_ADDR" ]; then
  exec "$OBJDUMP_CMD" "${{OBJDUMP_ARGS[@]}}" --start-address="0x$START_ADDR" --stop-address="0x$NEXT_ADDR" "$OBJ_FILE"
else
  exec "$OBJDUMP_CMD" "${{OBJDUMP_ARGS[@]}}" --start-address="0x$START_ADDR" "$OBJ_FILE"
fi
"""
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def write_compile_sh(path: Path, project_root: Path, compiler_script: str, function_name: str) -> None:
    rendered = (
        compiler_script.replace("{{cFilePath}}", '"$TMPDIR/preprocessed.c"')
        .replace("{{objFilePath}}", '"$OBJFILE"')
        .replace("{{functionName}}", function_name)
    )
    content = f"""#!/bin/bash
set -e
CFILE="$(realpath "$1")"
OBJFILE="$(realpath "$3")"
TMPDIR="$(mktemp -d)"
perl -0777 -pe 's|/\\*.*?\\*/||gs' "$CFILE" > "$TMPDIR/stripped.c"
cpp -P "$TMPDIR/stripped.c" "$TMPDIR/preprocessed.c"
cd "{project_root}"
{rendered}
rm -rf "$TMPDIR"
"""
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def parse_events(stdout: str) -> tuple[int, int, bool]:
    base_score = -1
    best_score = -1
    perfect = False
    for line in stdout.splitlines():
        m = re.search(r"base score = (\d+)", line)
        if m:
            base_score = int(m.group(1))
            best_score = base_score
        m = re.search(r"found a better score! \((\d+)", line)
        if m:
            val = int(m.group(1))
            if best_score < 0 or val < best_score:
                best_score = val
            if val == 0:
                perfect = True
        m = re.search(r"new best score! \((\d+)", line)
        if m:
            val = int(m.group(1))
            if best_score < 0 or val < best_score:
                best_score = val
            if val == 0:
                perfect = True
    return base_score, best_score, perfect


def read_best_output(work_dir: Path, best_score: int) -> str | None:
    if best_score < 0:
        return None
    candidates = sorted(work_dir.glob(f"output-{best_score}-*"))
    if not candidates:
        return None
    source = candidates[0] / "source.c"
    if source.is_file():
        return source.read_text(encoding="utf-8")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run decomp-permuter for a prompt folder")
    parser.add_argument("--prompt-dir", required=True)
    parser.add_argument("--base-c", default="")
    parser.add_argument("--permuter-dir", default=os.environ.get("PERMUTER_DIR", ""))
    parser.add_argument("--python", default=os.environ.get("PERMUTER_PYTHON", ""))
    parser.add_argument("--config", default="")
    parser.add_argument("--max-iterations", type=int, default=0)
    parser.add_argument("--timeout-ms", type=int, default=0)
    parser.add_argument("--flags", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    prompt_dir = Path(args.prompt_dir).resolve()
    settings_path = prompt_dir / "settings.yaml"
    if not settings_path.is_file():
        print(f"Missing {settings_path}", file=sys.stderr)
        return 2

    function_name = read_yaml_field(settings_path, "functionName")
    target_object_path = Path(read_yaml_field(settings_path, "targetObjectPath"))
    if not target_object_path.is_absolute():
        target_object_path = (prompt_dir / target_object_path).resolve()
    if not target_object_path.is_file():
        print(f"Target object not found: {target_object_path}", file=sys.stderr)
        return 3

    base_candidates = []
    if args.base_c:
        base_candidates.append(Path(args.base_c))
    for name in ("build/m2c.c", "candidate.c"):
        p = prompt_dir / name
        if p.suffix == ".c" and p.is_file():
            base_candidates.append(p)
    base_c = next((p for p in base_candidates if p.is_file()), None)
    if base_c is None:
        print("No base C file found (run m2c first or pass --base-c)", file=sys.stderr)
        return 4

    project_root = prompt_dir.parent.parent
    config_path = Path(args.config) if args.config else project_root / "reconkit.yaml"
    if not config_path.is_file():
        config_path = project_root / "reconkit.example.yaml"

    target = "x86"
    compiler_script = 'bash ./scripts/compile-placeholder.sh "{{cFilePath}}" "{{objFilePath}}"'
    max_iterations = args.max_iterations
    timeout_ms = args.timeout_ms
    flags: list[str] = []

    if config_path.is_file():
        try:
            import yaml  # type: ignore

            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            global_cfg = cfg.get("global", {}) or {}
            target = str(global_cfg.get("target", target))
            compiler_script = str(global_cfg.get("compilerScript", compiler_script))
            perm_cfg = (cfg.get("plugins", {}) or {}).get("decomp-permuter", {}) or {}
            if max_iterations <= 0:
                max_iterations = int(perm_cfg.get("maxIterations", 500))
            if timeout_ms <= 0:
                timeout_ms = int(perm_cfg.get("timeoutMs", 300000))
            flags = [str(x) for x in (perm_cfg.get("flags") or [])]
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: could not parse config: {exc}", file=sys.stderr)

    if args.flags.strip():
        flags.extend(args.flags.split())

    permuter_dir = Path(args.permuter_dir) if args.permuter_dir else project_root / "vendor" / "decomp-permuter"
    if not permuter_dir.is_dir():
        print(f"decomp-permuter not found at {permuter_dir} (set PERMUTER_DIR)", file=sys.stderr)
        return 5

    python_bin = args.python or str(permuter_dir / ".venv" / "bin" / "python")
    if not Path(python_bin).is_file():
        python_bin = shutil.which("python3") or "python3"

    nm_c, od_c, od_flags, compiler_type = TOOLCHAIN.get(target, DEFAULT_TOOLCHAIN)
    context_path = project_root / "context" / "ctx.h"
    context_content = context_path.read_text(encoding="utf-8") if context_path.is_file() else ""

    work_dir = Path(tempfile.mkdtemp(prefix="reconkit-permuter-"))
    try:
        (work_dir / "context.h").write_text(context_content, encoding="utf-8")
        c_code = base_c.read_text(encoding="utf-8")
        (work_dir / "base.c").write_text(f'#include "context.h"\n{c_code}', encoding="utf-8")
        shutil.copy2(target_object_path, work_dir / "target.o")
        wrapper = work_dir / "objdump_wrapper.sh"
        write_objdump_wrapper(wrapper, function_name, nm_c, od_c)
        settings = (
            f'func_name = "{function_name}"\n'
            f'compiler_type = "{compiler_type}"\n'
            f'objdump_command = "{wrapper} {" ".join(od_flags)}"\n'
        )
        (work_dir / "settings.toml").write_text(settings, encoding="utf-8")
        write_compile_sh(work_dir / "compile.sh", project_root, compiler_script, function_name)

        cmd = [python_bin, str(permuter_dir / "permuter.py"), *flags, str(work_dir)]
        print(f"Running: {' '.join(cmd)}", file=sys.stderr)
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(permuter_dir),
                capture_output=True,
                text=True,
                timeout=max(1, timeout_ms // 1000),
                check=False,
            )
        except subprocess.TimeoutExpired:
            print("permuter timed out", file=sys.stderr)
            return 6

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        if stdout:
            print(stdout, end="")
        if stderr:
            print(stderr, end="", file=sys.stderr)

        base_score, best_score, perfect = parse_events(stdout + "\n" + stderr)
        if base_score < 0:
            print(f"permuter failed (exit {proc.returncode})", file=sys.stderr)
            return 7

        print(f"base_score={base_score} best_score={best_score} perfect={perfect}", file=sys.stderr)

        if best_score < base_score:
            best_code = read_best_output(work_dir, best_score)
            if best_code:
                out_path = Path(args.out) if args.out else prompt_dir / "build" / "permuter-best.c"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(best_code, encoding="utf-8")
                print(f"Wrote improved code to {out_path}", file=sys.stderr)

        return 0 if perfect or best_score == 0 else 8
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
