"""AgentDecompile CLI adapter.

AgentDecompile is the Ghidra acquisition layer. Mizuchi consumes the function
list/decompiler facts it returns, then keeps matching/verification separate.
"""

from __future__ import annotations

import json
import os
import re
import shutil
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
    offset: int = 0,
    candidate_functions: list[dict[str, Any]] | None = None,
    batch_size: int = 25,
    server_url: str | None = None,
    mode: str = "auto",
) -> dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seed_candidates = select_seed_candidates(candidate_functions or [], limit, offset)
    if seed_candidates:
        return run_seeded_agentdecompile_analysis(
            binary_path=binary_path,
            out_path=out_path,
            run_dir=run_dir,
            seed_candidates=seed_candidates,
            timeout=timeout,
            offset=offset,
            batch_size=batch_size,
            server_url=server_url,
            mode=mode,
        )

    sequence = build_list_sequence(binary_path, limit)
    clean_local_backend_state(run_dir, mode=mode, server_url=server_url)
    command, env, cwd = build_command(
        run_dir=run_dir,
        server_url=server_url,
        mode=mode,
        sequence=sequence,
    )
    try:
        proc = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        write_facts(out_path, [])
        return {
            "tool": "agentdecompile",
            "status": "failed",
            "reason": "timeout",
            "timeout": timeout,
            "returnCode": None,
            "factsPath": str(out_path),
            "functionsFound": 0,
            "seedCandidates": len(seed_candidates),
            "candidateOffset": max(0, offset),
            "mode": mode,
            "serverUrl": server_url,
            "command": redact_command(command),
            "stdout": timeout_text(exc.stdout),
            "stderr": timeout_text(exc.stderr),
        }
    parsed = parse_cli_json(proc.stdout)
    facts = facts_from_tool_seq(parsed, binary_path)
    decompile_summary: dict[str, Any] = {
        "attempted": len(seed_candidates) if seed_candidates else 0,
        "returnCode": proc.returncode,
    }
    if facts and proc.returncode == 0 and not seed_candidates:
        decompile_summary = {
            "attempted": 0,
            "returnCode": proc.returncode,
            "reason": "skipped because unseeded list-only mode",
            "stdout": proc.stdout[-2000:],
            "stderr": proc.stderr[-2000:],
        }
    write_facts(out_path, facts)
    return {
        "tool": "agentdecompile",
        "status": "complete" if proc.returncode == 0 and facts else "failed",
        "returnCode": proc.returncode,
        "factsPath": str(out_path),
        "functionsFound": len(facts),
        "seedCandidates": len(seed_candidates),
        "candidateOffset": max(0, offset),
        "mode": mode,
        "serverUrl": server_url,
        "command": redact_command(command),
        "decompile": decompile_summary,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def run_seeded_agentdecompile_analysis(
    *,
    binary_path: Path,
    out_path: Path,
    run_dir: Path,
    seed_candidates: list[dict[str, Any]],
    timeout: int,
    offset: int,
    batch_size: int,
    server_url: str | None,
    mode: str,
) -> dict[str, Any]:
    facts: list[dict[str, Any]] = []
    batches: list[dict[str, Any]] = []
    stdout_tail = ""
    stderr_tail = ""
    return_codes: list[int] = []
    chunk_size = max(1, batch_size)

    for index, chunk in enumerate(chunks(seed_candidates, chunk_size), start=1):
        sequence = build_list_sequence(binary_path, len(chunk), chunk)
        batch_run_dir = run_dir / "agentdecompile-batches" / f"batch-{index:04d}"
        clean_local_backend_state(batch_run_dir, mode=mode, server_url=server_url)
        command, env, cwd = build_command(
            run_dir=batch_run_dir,
            server_url=server_url,
            mode=mode,
            sequence=sequence,
        )
        try:
            proc = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            stdout_tail = (stdout_tail + "\n" + timeout_text(exc.stdout))[-4000:]
            stderr_tail = (stderr_tail + "\n" + timeout_text(exc.stderr))[-4000:]
            batches.append(
                {
                    "index": index,
                    "seedCandidates": len(chunk),
                    "factsFound": 0,
                    "decompiled": 0,
                    "returnCode": None,
                    "status": "timeout",
                    "timeout": timeout,
                    "command": redact_command(command),
                }
            )
            break
        parsed = parse_cli_json(proc.stdout)
        batch_facts = seeded_facts_from_tool_seq(parsed, binary_path, chunk)
        facts.extend(batch_facts)
        stdout_tail = (stdout_tail + "\n" + proc.stdout)[-4000:]
        stderr_tail = (stderr_tail + "\n" + proc.stderr)[-4000:]
        return_codes.append(proc.returncode)
        batches.append(
            {
                "index": index,
                "seedCandidates": len(chunk),
                "factsFound": len(batch_facts),
                "decompiled": sum(1 for row in batch_facts if row.get("decompiled")),
                "returnCode": proc.returncode,
                "command": redact_command(command),
            }
        )

    write_facts(out_path, facts)

    nonzero = [code for code in return_codes if code != 0]
    timed_out = any(batch.get("status") == "timeout" for batch in batches)
    return_code = nonzero[-1] if nonzero else (return_codes[-1] if return_codes else None if timed_out else 1)
    return {
        "tool": "agentdecompile",
        "status": "complete" if return_code == 0 and facts and not timed_out else "failed",
        "reason": "timeout" if timed_out else None,
        "returnCode": return_code,
        "factsPath": str(out_path),
        "functionsFound": len(facts),
        "seedCandidates": len(seed_candidates),
        "candidateOffset": max(0, offset),
        "mode": mode,
        "serverUrl": server_url,
        "command": batches[-1]["command"] if batches else [],
        "decompile": {
            "attempted": len(seed_candidates),
            "returnCode": return_code,
            "batchSize": chunk_size,
            "batches": len(batches),
            "decompiled": sum(1 for row in facts if row.get("decompiled")),
        },
        "batches": batches,
        "stdout": stdout_tail,
        "stderr": stderr_tail,
    }


def chunks(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def write_facts(path: Path, facts: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for fact in facts:
            fh.write(json.dumps(fact, sort_keys=True) + "\n")


def timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")[-4000:]
    return value[-4000:]


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


def select_seed_candidates(candidates: list[dict[str, Any]], limit: int, offset: int = 0) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()
    eligible: list[tuple[int, dict[str, Any]]] = []
    for row in candidates:
        address = row.get("address", row.get("entryOffset"))
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
    start = max(0, offset)
    stop = start + max(0, limit)
    window = eligible[start:stop]
    for index, (address_int, row) in enumerate(window):
        name = normalize_seed_name(str(row.get("name") or f"sub_{address_int:x}"), address_int)
        absolute_index = start + index
        next_address = eligible[absolute_index + 1][0] if absolute_index + 1 < len(eligible) else None
        body_bytes = parse_address(row.get("bodyBytes") or row.get("size"))
        end_address = address_int + max(1, body_bytes or 0x400) - 1
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
    effective_mode = mode
    if mode == "auto" and not server_url:
        effective_mode = "local"
    ghidra_root = Path(os.environ.get("AGENTDECOMPILE_GHIDRA_INSTALL_DIR", str(DEFAULT_GHIDRA))).resolve()
    if effective_mode == "local" and not ghidra_root.exists():
        raise SystemExit(f"--local mode requested but GHIDRA not found at {ghidra_root}")
    env = {
        **os.environ,
        "UV_CACHE_DIR": str((ROOT / "target/uv-cache").resolve()),
        "GHIDRA_INSTALL_DIR": str(ghidra_root),
        "CC": os.environ.get("CC", "gcc"),
        "CXX": os.environ.get("CXX", "g++"),
        "HOME": str((run_dir / "agentdecompile-home").resolve()),
        "XDG_CONFIG_HOME": str((run_dir / "agentdecompile-config").resolve()),
        "XDG_CACHE_HOME": str((run_dir / "agentdecompile-cache").resolve()),
        "TMPDIR": str((run_dir / "agentdecompile-tmp").resolve()),
    }
    for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TMPDIR", "UV_CACHE_DIR"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    command = [
        "uvx",
        "--from",
        AGENTDECOMPILE_FROM,
        "agentdecompile-cli",
        "-f",
        "json",
    ]
    if effective_mode == "local":
        command.extend(["--local", "--local-project-path", str((run_dir / "agentdecompile-project").resolve())])
    elif server_url:
        command.extend(["--server-url", server_url])
    command.extend(["tool-seq", json.dumps(sequence)])
    return command, env, ROOT


def clean_local_backend_state(run_dir: Path, *, mode: str, server_url: str | None) -> None:
    if mode != "local" and server_url:
        return
    for name in ("agentdecompile-project", "agentdecompile-home", "agentdecompile-config", "agentdecompile-cache", "agentdecompile-tmp"):
        path = run_dir / name
        if path.exists():
            shutil.rmtree(path)


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


def parse_address(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        try:
            return int(s, 0)
        except ValueError:
            try:
                return int(s, 16)
            except ValueError:
                return None
    try:
        return int(value)
    except (TypeError, ValueError):
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
        address = parse_address(row.get("address"))
        if address is None:
            continue
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


def seeded_facts_from_tool_seq(parsed: Any, binary_path: Path, seeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decompiled = decompiled_from_tool_seq(parsed)
    facts: list[dict[str, Any]] = []
    for seed in seeds:
        name = str(seed["name"])
        address = int(seed["address"])
        end_address = int(seed.get("endAddress") or address)
        body_bytes = max(1, end_address - address + 1)
        facts.append(
            {
                "name": name,
                "entry": f"{address:08x}",
                "entryOffset": address,
                "bodyBytes": body_bytes,
                "instructionCount": 0,
                "bytes": "",
                "asm": "",
                "decompiled": decompiled.get(name, ""),
                "source": "agentdecompile-seeded",
                "binaryPath": str(binary_path),
            }
        )
    return facts


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
