"""Tool and host capability inspection."""

from __future__ import annotations

import os
import shutil
import subprocess
import sysconfig
from pathlib import Path
from typing import Any


DEFAULT_STEAMLESS = Path("target/steamless-release/extracted/Steamless.CLI.exe")
STEAMLESS_ENV = "MIZUCHI_STEAMLESS_CLI"


def inspect_tool(name: str, command: list[str] | None = None) -> dict[str, Any]:
    path = shutil.which(name)
    result: dict[str, Any] = {"name": name, "path": path, "available": path is not None}
    if path and command:
        proc = subprocess.run(command, text=True, capture_output=True, check=False, timeout=10)
        result.update(
            {
                "returnCode": proc.returncode,
                "stdout": proc.stdout.strip()[:500],
                "stderr": proc.stderr.strip()[:500],
            }
        )
    return result


def inspect_executable(name: str, path: Path, command: list[str] | None = None) -> dict[str, Any]:
    available = path.exists() and os.access(path, os.X_OK)
    result: dict[str, Any] = {"name": name, "path": str(path) if available else None, "available": available}
    if available and command:
        proc = subprocess.run(command, text=True, capture_output=True, check=False, timeout=10)
        result.update(
            {
                "returnCode": proc.returncode,
                "stdout": proc.stdout.strip()[:500],
                "stderr": proc.stderr.strip()[:500],
            }
        )
    return result


def inspect_capabilities(repo_root: Path) -> dict[str, Any]:
    steamless = resolve_steamless_cli(repo_root)
    tools = {
        "python": inspect_tool("python3", ["python3", "--version"]),
        "clang": inspect_tool("clang", ["clang", "--version"]),
        "objdiff": inspect_tool("objdiff", ["objdiff", "--version"]),
        "objdump": inspect_tool("objdump", ["objdump", "--version"]),
        "objcopy": inspect_tool("objcopy", ["objcopy", "--version"]),
        "wine": inspect_tool("wine", ["wine", "--version"]),
        "mono": inspect_tool("mono", ["mono", "--version"]),
        "uv": inspect_tool("uv", ["uv", "--version"]),
    }
    local = {
        "oneShotSource": resolve_script_asset(repo_root, "one-shot-source.py") is not None,
        "oneShotSourcePath": str(resolve_script_asset(repo_root, "one-shot-source.py") or ""),
        "sourceParityOneShot": (repo_root / "scripts/source-parity-one-shot.py").exists(),
        "swkotorInventorySlice": (repo_root / "scripts/swkotor-inventory-slice.py").exists(),
        "verifyObjdiff": (repo_root / "scripts/lib/verify-objdiff.sh").exists(),
        "steamlessCli": steamless is not None,
        "steamlessCliPath": str(steamless) if steamless else None,
    }
    return {
        "schema": "mizuchi.capabilities.v1",
        "tools": tools,
        "localSurfaces": local,
    }


def resolve_steamless_cli(repo_root: Path, configured: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if configured is not None:
        candidates.append(configured)
    env_path = os.environ.get(STEAMLESS_ENV)
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend(
        [
            Path.cwd() / DEFAULT_STEAMLESS,
            repo_root / DEFAULT_STEAMLESS,
        ]
    )
    for candidate in candidates:
        expanded = candidate.expanduser()
        if expanded.exists():
            return expanded.resolve()
    return None


def resolve_script_asset(repo_root: Path, script_name: str) -> Path | None:
    candidates = [
        repo_root / "scripts" / script_name,
        Path(sysconfig.get_path("data")) / "share" / "mizuchi-re" / "scripts" / script_name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None
