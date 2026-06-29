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
    candidate_functions: list[dict[str, Any]] | None = None,
    server_url: str | None = None,
    mode: str = "auto",
) -> dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seed_candidates = select_seed_candidates(candidate_functions or [], limit)
    sequence = build_list_sequence(binary_path, limit, seed_candidates)
    command, env, cwd = build_command(
        run_dir=run_dir,
        server_url=server_url,
        mode=mode,
        sequence=sequence,
    )
    proc = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False, timeout=timeout)
    parsed = parse_cli_json(proc.stdout)
    facts = facts_from_tool_seq(parsed, binary_path)
    decompile_summary: dict[str, Any] = {
        "attempted": len(seed_candidates) if seed_candidates else 0,
        "returnCode": proc.returncode if seed_candidates else None,
    }
    if facts and proc.returncode == 0 and not seed_candidates:
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
        "seedCandidates": len(seed_candidates),
        "mode": mode,
        "serverUrl": server_url,
        "command": redact_command(command),
        "decompile": decompile_summary,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def build_list_sequence(binary_path: Path, limit: int, seed_candidates: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
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
    ]
    if seed_candidates:
        calls.append(
            {
                "name": "execute-script",
                "arguments": {
                    "program_path": f"/{program_name}",
                    "programPath": f"/{program_name}",
                    "timeout": 120,
                    "code": build_seed_script(seed_candidates),
                },
            }
        )
    calls.append(
        {
            "name": "list-functions",
            "arguments": {
                "program_path": f"/{program_name}",
                "programPath": f"/{program_name}",
                "limit": limit,
                "format": "json",
            },
        }
    )
    if seed_candidates:
        calls.extend(build_decompile_sequence(program_name, [str(row["name"]) for row in seed_candidates]))
    return calls


def select_seed_candidates(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()
    eligible: list[tuple[int, dict[str, Any]]] = []
    for row in candidates:
        address = row.get("address")
        if address is None:
            continue
        if str(row.get("source")) == "executable-range":
            continue
        if str(row.get("confidence")) == "low":
            continue
        try:
            address_int = int(address)
        except (TypeError, ValueError):
            continue
        if address_int in seen:
            continue
        seen.add(address_int)
        eligible.append((address_int, row))
    eligible.sort(key=lambda item: item[0])
    for index, (address_int, row) in enumerate(eligible[:limit]):
        name = normalize_seed_name(str(row.get("name") or f"sub_{address_int:x}"), address_int)
        next_address = eligible[index + 1][0] if index + 1 < min(len(eligible), limit) else None
        end_address = address_int + 0x3ff
        if next_address is not None and next_address > address_int:
            end_address = min(end_address, next_address - 1)
        selected.append({"name": name, "address": address_int, "endAddress": end_address})
    return selected


def normalize_seed_name(name: str, address: int) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"sub_{address:x}"
    return cleaned[:80]


def build_seed_script(candidates: list[dict[str, Any]]) -> str:
    payload = json.dumps(candidates, sort_keys=True)
    return f"""
from ghidra.program.model.symbol import SourceType
from ghidra.program.model.address import AddressSet

candidates = {payload}
fm = currentProgram.getFunctionManager()
listing = currentProgram.getListing()
memory = currentProgram.getMemory()
summary = {{"attempted": 0, "created": 0, "existing": 0, "disassembled": 0, "skipped": 0, "errors": []}}

tx = currentProgram.startTransaction("mizuchi-seed-functions")
try:
    for row in candidates:
        summary["attempted"] += 1
        address_int = int(row["address"])
        name = str(row["name"])
        try:
            addr = toAddr("0x%08x" % address_int)
            end_addr = toAddr("0x%08x" % int(row.get("endAddress", address_int)))
            if addr is None or not memory.contains(addr):
                summary["skipped"] += 1
                continue
            if end_addr is None or not memory.contains(end_addr) or end_addr.compareTo(addr) < 0:
                end_addr = addr
            existing = fm.getFunctionAt(addr)
            if existing is not None:
                summary["existing"] += 1
                continue
            containing = fm.getFunctionContaining(addr)
            if containing is not None and containing.getEntryPoint() != addr:
                summary["skipped"] += 1
                continue
            if listing.getInstructionAt(addr) is None:
                try:
                    if disassemble(addr):
                        summary["disassembled"] += 1
                except Exception:
                    pass
            func = fm.getFunctionAt(addr)
            if func is None:
                try:
                    func = createFunction(addr, name)
                except Exception:
                    body = AddressSet(addr, end_addr)
                    func = fm.createFunction(name, addr, body, SourceType.USER_DEFINED)
            if func is not None:
                summary["created"] += 1
        except Exception as exc:
            summary["errors"].append({{"address": hex(address_int), "name": name, "error": str(exc)}})
            if len(summary["errors"]) > 10:
                break
finally:
    currentProgram.endTransaction(tx, True)

__result__ = summary
""".strip()


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
