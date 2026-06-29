"""AgentDecompile CLI adapter.

AgentDecompile is the Ghidra acquisition layer. Mizuchi consumes the function
list/decompiler facts it returns, then keeps matching/verification separate.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_GHIDRA = Path("/home/brunner56/.local/opt/ghidra/current")
AGENTDECOMPILE_FROM = "git+https://github.com/bolabaden/agentdecompile"
ROOT = Path(__file__).resolve().parents[2]


def run_agentdecompile_analysis(
    *,
    binary_path: Path,
    out_path: Path,
    run_dir: Path,
    limit: int,
    timeout: int,
    server_url: str | None = None,
    mode: str = "auto",
) -> dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sequence = build_list_sequence(binary_path, limit)
    command, env, cwd = build_command(
        run_dir=run_dir,
        server_url=server_url,
        mode=mode,
        sequence=sequence,
    )
    proc = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False, timeout=timeout)
    parsed = parse_cli_json(proc.stdout)
    facts = facts_from_tool_seq(parsed, binary_path)
    decompile_summary: dict[str, Any] = {"attempted": 0, "returnCode": None}
    if facts and proc.returncode == 0:
        decompile_sequence = build_decompile_sequence(binary_path.name, [str(row["name"]) for row in facts])
        decompile_command, decompile_env, decompile_cwd = build_command(
            run_dir=run_dir,
            server_url=server_url,
            mode=mode,
            sequence=decompile_sequence,
        )
        decompile_proc = subprocess.run(
            decompile_command,
            cwd=decompile_cwd,
            env=decompile_env,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        decompiled = decompiled_from_tool_seq(parse_cli_json(decompile_proc.stdout))
        for row in facts:
            row["decompiled"] = decompiled.get(str(row["name"]), "")
        decompile_summary = {
            "attempted": len(decompile_sequence),
            "returnCode": decompile_proc.returncode,
            "stdout": decompile_proc.stdout[-2000:],
            "stderr": decompile_proc.stderr[-2000:],
        }
    with out_path.open("w", encoding="utf-8") as fh:
        for fact in facts:
            fh.write(json.dumps(fact, sort_keys=True) + "\n")
    return {
        "tool": "agentdecompile",
        "status": "complete" if proc.returncode == 0 and facts else "failed",
        "returnCode": proc.returncode,
        "factsPath": str(out_path),
        "functionsFound": len(facts),
        "mode": mode,
        "serverUrl": server_url,
        "command": redact_command(command),
        "decompile": decompile_summary,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def build_list_sequence(binary_path: Path, limit: int) -> list[dict[str, Any]]:
    program_name = binary_path.name
    calls: list[dict[str, Any]] = [
        {
            "name": "import-binary",
            "arguments": {
                "binary_path": str(binary_path),
                "path": str(binary_path),
                "enable_version_control": False,
                "enableVersionControl": False,
                "format": "json",
            },
        },
        {
            "name": "list-functions",
            "arguments": {
                "program_path": f"/{program_name}",
                "programPath": f"/{program_name}",
                "limit": limit,
                "format": "json",
            },
        },
    ]
    return calls


def build_decompile_sequence(binary_name: str, names: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "name": "decompile-function",
            "arguments": {
                "function": name,
                "program_path": f"/{binary_name}",
                "programPath": f"/{binary_name}",
                "format": "json",
            },
        }
        for name in names
    ]


def build_command(
    *,
    run_dir: Path,
    server_url: str | None,
    mode: str,
    sequence: list[dict[str, Any]],
) -> tuple[list[str], dict[str, str], Path]:
    env = {
        **os.environ,
        "UV_CACHE_DIR": str((ROOT / "target/uv-cache").resolve()),
        "GHIDRA_INSTALL_DIR": str(DEFAULT_GHIDRA),
        "HOME": str((run_dir / "agentdecompile-home").resolve()),
        "XDG_CONFIG_HOME": str((run_dir / "agentdecompile-config").resolve()),
        "XDG_CACHE_HOME": str((run_dir / "agentdecompile-cache").resolve()),
        "TMPDIR": str((run_dir / "agentdecompile-tmp").resolve()),
    }
    for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TMPDIR", "UV_CACHE_DIR"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    command = [
        "uvx",
        "--refresh",
        "--python",
        "3.13",
        "--from",
        AGENTDECOMPILE_FROM,
        "agentdecompile-cli",
        "-f",
        "json",
    ]
    if mode == "local":
        command.extend(["--local", "--local-project-path", str((run_dir / "agentdecompile-project").resolve())])
    elif server_url:
        command.extend(["--server-url", server_url])
    command.extend(["tool-seq", json.dumps(sequence)])
    return command, env, ROOT


def parse_cli_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\{.*\}|\[.*\])\s*$", stripped, re.S)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None


def facts_from_tool_seq(parsed: Any, binary_path: Path) -> list[dict[str, Any]]:
    payloads = extract_payloads(parsed)
    functions: list[dict[str, Any]] = []
    decompiled_by_name: dict[str, str] = {}
    for payload in payloads:
        if isinstance(payload, dict) and isinstance(payload.get("results"), list):
            for row in payload["results"]:
                if isinstance(row, dict) and row.get("name") and row.get("address"):
                    functions.append(row)
        if isinstance(payload, dict):
            name = payload.get("name") or payload.get("function") or payload.get("functionName")
            code = payload.get("decompiled") or payload.get("c") or payload.get("code") or payload.get("pseudocode")
            if name and code:
                decompiled_by_name[str(name)] = str(code)
    facts = []
    for row in functions:
        address = int(str(row.get("address")), 16)
        name = str(row.get("name"))
        facts.append(
            {
                "name": name,
                "entry": str(row.get("address")),
                "entryOffset": address,
                "bodyBytes": int(row.get("size") or 0),
                "instructionCount": 0,
                "bytes": "",
                "asm": "",
                "decompiled": decompiled_by_name.get(name, ""),
                "source": "agentdecompile",
                "binaryPath": str(binary_path),
            }
        )
    return facts


def decompiled_from_tool_seq(parsed: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for payload in extract_payloads(parsed):
        if not isinstance(payload, dict):
            continue
        name = payload.get("name") or payload.get("function") or payload.get("functionName")
        code = payload.get("decompiled") or payload.get("c") or payload.get("code") or payload.get("pseudocode")
        if name and code:
            out[str(name)] = str(code)
    return out


def extract_payloads(value: Any) -> list[Any]:
    payloads: list[Any] = []
    if isinstance(value, list):
        for item in value:
            payloads.extend(extract_payloads(item))
    elif isinstance(value, dict):
        payloads.append(value)
        for key in ("steps", "result", "results", "content", "data", "output", "text"):
            if key in value:
                payloads.extend(extract_payloads(value[key]))
    elif isinstance(value, str):
        try:
            payloads.extend(extract_payloads(json.loads(value)))
        except json.JSONDecodeError:
            pass
    return payloads


def redact_command(command: list[str]) -> list[str]:
    return ["<json-sequence>" if item.startswith("[{") else item for item in command]
