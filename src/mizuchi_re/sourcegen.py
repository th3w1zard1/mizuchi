"""Automatic source-candidate task generation.

This module does not synthesize hand-written C. It packages machine-derived
inputs and, when available, writes decompiler-produced C text as unverified
candidate source for later compiler/objdiff gates.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


def generate_source_candidates(
    *,
    target: dict[str, Any],
    function_candidates: dict[str, Any],
    out_dir: Path,
    inventory: dict[str, Any] | None = None,
    function_facts_jsonl: Path | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    slices_dir = out_dir / "target-slices"
    facts = load_function_facts(function_facts_jsonl) if function_facts_jsonl else {}
    original_candidates = list(function_candidates.get("candidates", []))
    all_candidates = [row for row in original_candidates if is_recoverable_candidate(row)]
    start = max(0, offset)
    candidates = all_candidates[start : start + max(limit, 0)]

    tasks_path = out_dir / "tasks.jsonl"
    generated_count = 0
    fresh_generated_count = 0
    reused_generated_count = 0
    target_slice_count = 0
    task_count = 0
    by_status: dict[str, int] = {}

    with tasks_path.open("w", encoding="utf-8") as tasks:
        for row in candidates:
            if is_range_alias(row, facts):
                continue
            fact = match_fact(row, facts)
            task = build_task(target, row, fact)
            target_slice = build_target_slice(inventory or {}, row, fact)
            if target_slice.get("status") == "complete":
                slices_dir.mkdir(parents=True, exist_ok=True)
                slice_path = slices_dir / f"{safe_task_id(task)}.target.bin"
                slice_bytes = bytes.fromhex(str(target_slice.pop("bytesHex")))
                slice_path.write_bytes(slice_bytes)
                target_slice["bytesPath"] = str(slice_path)
                target_slice_count += 1
            task["targetSlice"] = target_slice
            case_dir = out_dir / safe_task_id(task)
            if fact and fact.get("decompiled"):
                case_dir.mkdir(parents=True, exist_ok=True)
                source = str(fact["decompiled"]).rstrip() + "\n"
                source_path = case_dir / "candidate.c"
                source_path.write_text(source, encoding="utf-8")
                task.update(
                    {
                        "status": "generated-unverified",
                        "source": str(source_path),
                        "sourceSha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                        "sourceOrigin": "AgentDecompile decompiler output; automatically exported, not manually authored",
                        "acceptanceGate": "compile with selected compiler profile and objdiff-zero against target slice",
                    }
                )
                write_json(case_dir / "candidate.json", task)
                generated_count += 1
                fresh_generated_count += 1
            elif (case_dir / "candidate.c").exists():
                source_path = case_dir / "candidate.c"
                source = source_path.read_text(encoding="utf-8", errors="replace")
                task.update(
                    {
                        "status": "generated-unverified",
                        "source": str(source_path),
                        "sourceSha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                        "sourceOrigin": "existing automatic task-local candidate reused because the current tool pass produced no decompiler C",
                        "acceptanceGate": "compile with selected compiler profile and objdiff-zero against target slice",
                    }
                )
                task.setdefault("automaticInputs", ["function-facts"])
                if "existing-candidate-file" not in task["automaticInputs"]:
                    task["automaticInputs"].append("existing-candidate-file")
                write_json(case_dir / "candidate.json", task)
                generated_count += 1
                reused_generated_count += 1
            tasks.write(json.dumps(task, sort_keys=True) + "\n")
            task_count += 1
            by_status[str(task["status"])] = by_status.get(str(task["status"]), 0) + 1

    if not function_facts_jsonl:
        status = "blocked"
        blockers = ["no decompiler/function-facts JSONL provided or generated"]
    elif not facts:
        status = "blocked"
        blockers = [f"function-facts JSONL was empty or unreadable: {function_facts_jsonl}"]
    elif generated_count == 0:
        status = "queued-no-source"
        blockers = ["function facts were present, but no decompiler C text matched current candidates"]
    else:
        status = "generated-unverified"
        blockers = []

    return {
        "schema": "mizuchi.source-generation.v1",
        "status": status,
        "target": target,
        "tasks": str(tasks_path),
        "taskCount": task_count,
        "generatedSourceCandidates": generated_count,
        "freshGeneratedSourceCandidates": fresh_generated_count,
        "reusedSourceCandidates": reused_generated_count,
        "targetSlices": target_slice_count,
        "candidateOffset": start,
        "candidateLimit": max(limit, 0),
        "candidateTotal": len(all_candidates),
        "originalCandidateTotal": len(original_candidates),
        "functionFacts": str(function_facts_jsonl) if function_facts_jsonl else None,
        "byStatus": dict(sorted(by_status.items())),
        "blockers": blockers,
        "claimBoundary": "generated candidates are not recovered source until compiler and objdiff gates accept them",
    }


def load_function_facts(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    facts: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        for key in fact_keys(row):
            facts[key] = row
    return facts


def fact_keys(row: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    if row.get("entryOffset") is not None:
        keys.append(f"address:{int(row['entryOffset'])}")
        keys.append(f"rva:{int(row['entryOffset'])}")
    if row.get("entry"):
        try:
            keys.append(f"address:{int(str(row['entry']), 16)}")
        except ValueError:
            keys.append(f"entry:{row['entry']}")
    if row.get("name"):
        keys.append(f"name:{row['name']}")
    return keys


def match_fact(candidate: dict[str, Any], facts: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    possible = []
    if candidate.get("address") is not None:
        possible.append(f"address:{int(candidate['address'])}")
    if candidate.get("rva") is not None:
        possible.append(f"rva:{int(candidate['rva'])}")
    if candidate.get("name"):
        possible.append(f"name:{candidate['name']}")
    for key in possible:
        if key in facts:
            return facts[key]
    return None


def is_range_alias(candidate: dict[str, Any], facts: dict[str, dict[str, Any]]) -> bool:
    if candidate.get("source") != "executable-range" or candidate.get("address") is None:
        return False
    return f"address:{int(candidate['address'])}" in facts


def is_recoverable_candidate(candidate: dict[str, Any]) -> bool:
    if candidate.get("address") is None:
        return False
    if candidate.get("source") == "executable-range":
        return False
    if candidate.get("confidence") == "low":
        return False
    return True


def build_task(target: dict[str, Any], candidate: dict[str, Any], fact: dict[str, Any] | None) -> dict[str, Any]:
    fact_binary = fact.get("binaryPath") if fact else None
    task = {
        "schema": "mizuchi.source-task.v1",
        "status": "waiting-for-automatic-source-generator",
        "targetStableId": target.get("stableId"),
        "binaryPath": target.get("binaryPath"),
        "analysisBinaryPath": fact_binary or target.get("binaryPath"),
        "name": candidate.get("name"),
        "address": candidate.get("address"),
        "rva": candidate.get("rva"),
        "size": candidate.get("size"),
        "confidence": candidate.get("confidence"),
        "boundarySource": candidate.get("source"),
        "sourceOrigin": "not generated yet; requires decompiler/model/programmatic generator output",
        "manualSourceAllowed": False,
    }
    if fact:
        task["functionFact"] = {
            "name": fact.get("name"),
            "entry": fact.get("entry"),
            "entryOffset": fact.get("entryOffset"),
            "bodyBytes": fact.get("bodyBytes"),
            "instructionCount": fact.get("instructionCount"),
            "bytesSha256": hashlib.sha256(str(fact.get("bytes") or "").encode("utf-8")).hexdigest(),
            "hasAsm": bool(fact.get("asm")),
            "hasDecompilerOutput": bool(fact.get("decompiled")),
        }
        task["automaticInputs"] = ["function-facts"]
        if fact.get("asm"):
            task["automaticInputs"].append("asm")
        if fact.get("decompiled"):
            task["automaticInputs"].append("decompiler-output")
    return task


def build_target_slice(inventory: dict[str, Any], candidate: dict[str, Any], fact: dict[str, Any] | None) -> dict[str, Any]:
    target = inventory.get("target") or {}
    binary_path = Path(str((fact or {}).get("binaryPath") or target.get("binaryPath") or ""))
    image_base = int(inventory.get("imageBase") or 0)
    address = coerce_int(candidate.get("address"))
    rva = coerce_int(candidate.get("rva"))
    if rva is None and address is not None and image_base:
        rva = address - image_base
    if address is None and rva is not None and image_base:
        address = image_base + rva
    size = coerce_int((fact or {}).get("bodyBytes")) or coerce_int(candidate.get("size")) or 0
    if not binary_path.exists():
        return {
            "status": "missing-binary",
            "reason": f"analysis binary not found: {binary_path}",
            "analysisBinaryPath": str(binary_path),
            "address": address,
            "rva": rva,
            "size": size,
        }
    if rva is None:
        return {"status": "missing-rva", "analysisBinaryPath": str(binary_path), "address": address, "size": size}
    if size <= 0:
        return {"status": "missing-size", "analysisBinaryPath": str(binary_path), "address": address, "rva": rva, "size": size}
    section = section_for_rva(inventory, rva)
    if section is None:
        return {
            "status": "rva-outside-code",
            "analysisBinaryPath": str(binary_path),
            "address": address,
            "rva": rva,
            "size": size,
        }
    section_rva = int(section.get("rva") or 0)
    section_file_offset = int(section.get("fileOffset") or 0)
    section_file_size = int(section.get("fileSize") or section.get("size") or 0)
    file_offset = section_file_offset + (rva - section_rva)
    if file_offset < section_file_offset or file_offset + size > section_file_offset + section_file_size:
        return {
            "status": "slice-outside-section",
            "analysisBinaryPath": str(binary_path),
            "address": address,
            "rva": rva,
            "size": size,
            "fileOffset": file_offset,
            "section": section.get("name"),
        }
    with binary_path.open("rb") as fh:
        fh.seek(file_offset)
        data = fh.read(size)
    return {
        "status": "complete",
        "analysisBinaryPath": str(binary_path),
        "address": address,
        "rva": rva,
        "size": len(data),
        "fileOffset": file_offset,
        "section": section.get("name"),
        "bytesSha256": hashlib.sha256(data).hexdigest(),
        "bytesHex": data.hex(),
        "claimBoundary": "target slice bytes are acquisition evidence only; source parity still requires compiling candidate source and comparing code/relocations under a compiler profile",
    }


def section_for_rva(inventory: dict[str, Any], rva: int) -> dict[str, Any] | None:
    for section in inventory.get("codeRanges", []):
        start = int(section.get("rva") or 0)
        size = int(section.get("size") or section.get("fileSize") or 0)
        if start <= rva < start + size:
            return section
    return None


def coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_task_id(task: dict[str, Any]) -> str:
    name = str(task.get("name") or "sub")
    addr = task.get("address") if task.get("address") is not None else task.get("rva")
    suffix = f"{int(addr):x}" if addr is not None else hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    cleaned = re.sub(r"[^A-Za-z0-9_.+-]+", "_", name).strip("._") or "sub"
    return f"{cleaned}_{suffix}"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
