"""Function-boundary candidate discovery from binary inventory."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


def discover_function_candidates(inventory: dict[str, Any]) -> dict[str, Any]:
    fmt = inventory.get("format")
    if fmt == "elf":
        candidates = elf_function_candidates(inventory)
    elif fmt == "pe":
        candidates = pe_function_candidates(inventory)
    elif fmt == "macho":
        candidates = macho_function_candidates(inventory)
    else:
        candidates = []
    candidates = dedupe_same_address_candidates(candidates)
    candidates = sorted(candidates, key=lambda row: (int(row.get("address") or row.get("rva") or 0), row.get("name", "")))
    return {
        "schema": "reconkit.function-candidates.v1",
        "format": fmt,
        "target": inventory.get("target"),
        "status": "complete" if inventory.get("status") == "complete" else "inventory-incomplete",
        "candidates": candidates,
        "summary": summarize_candidates(candidates),
        "claimBoundary": "function candidates are recovery inputs, not proven source or verified function boundaries",
    }


def analyze_function_candidates_with_objdump(existing: dict[str, Any], binary_path: Path, timeout: int) -> dict[str, Any]:
    proc = subprocess.run(
        ["objdump", "-d", str(binary_path)],
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    labels = parse_objdump_labels(proc.stdout)
    candidates = list(existing.get("candidates", []))
    existing_addresses = {int(row.get("address") or 0) for row in candidates if row.get("address") is not None}
    added = 0
    alias_labels = 0
    for label in labels:
        address = int(label["address"])
        row = {
            "name": label["name"],
            "address": address,
            "size": 0,
            "source": "objdump-label",
            "confidence": "medium",
            "evidence": {"sources": ["objdump-label"], "section": label.get("section")},
        }
        candidates.append(row)
        if address in existing_addresses:
            alias_labels += 1
        else:
            existing_addresses.add(address)
            added += 1

    candidates = dedupe_same_address_candidates(candidates)
    candidates = sorted(candidates, key=lambda row: (int(row.get("address") or row.get("rva") or 0), row.get("name", "")))
    return {
        **existing,
        "candidates": candidates,
        "summary": summarize_candidates(candidates),
        "toolAnalysis": {
            "tool": "objdump",
            "status": "complete" if proc.returncode == 0 else "failed",
            "returnCode": proc.returncode,
            "labelsFound": len(labels),
            "candidatesAdded": added,
            "aliasLabelsAdded": alias_labels,
            "stderr": proc.stderr[-4000:],
        },
    }


def parse_function_facts(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows.append(row)
    return rows


def parse_objdump_labels(text: str) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    current_section = None
    section_re = re.compile(r"^Disassembly of section (.+):$")
    label_re = re.compile(r"^([0-9a-fA-F]+) <([^>]+)>:$")
    for line in text.splitlines():
        section_match = section_re.match(line.strip())
        if section_match:
            current_section = section_match.group(1)
            continue
        label_match = label_re.match(line.strip())
        if not label_match:
            continue
        name = label_match.group(2)
        address = int(label_match.group(1), 16)
        if name.startswith(".") and "@" not in name:
            continue
        labels.append({"address": address, "name": normalize_label_name(name, address), "section": current_section})
    return labels


def normalize_label_name(name: str, address: int) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_@.$+-]", "_", name)
    return cleaned or f"sub_{address:x}"


def dedupe_same_address_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_address: dict[int, list[dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []
    for row in candidates:
        if row.get("source") == "executable-range":
            passthrough.append(row)
            continue
        address = optional_int(row.get("address"))
        if address is None:
            passthrough.append(row)
            continue
        by_address.setdefault(address, []).append(row)

    merged: list[dict[str, Any]] = list(passthrough)
    for address, rows in by_address.items():
        if len(rows) == 1:
            merged.append(rows[0])
            continue
        ordered = sorted(rows, key=candidate_merge_priority)
        primary = dict(ordered[0])
        sources = sorted(
            {
                str(source)
                for row in rows
                for source in list((row.get("evidence") or {}).get("sources") or []) + ([row.get("source")] if row.get("source") else [])
                if source
            }
        )
        aliases = deduped_aliases_for_rows(ordered)
        best_size = max((optional_int(row.get("size")) or 0 for row in rows), default=0)
        if best_size > 0 and (optional_int(primary.get("size")) or 0) <= 0:
            primary["size"] = best_size
        primary["confidence"] = max((str(row.get("confidence") or "") for row in rows), key=confidence_rank, default=primary.get("confidence"))
        evidence = dict(primary.get("evidence") or {})
        evidence["sources"] = sources
        evidence["aliases"] = aliases
        evidence["deduplicatedAddress"] = f"0x{address:x}"
        evidence["duplicateCount"] = len(aliases)
        evidence["duplicateCandidateCount"] = len(rows)
        primary["evidence"] = evidence
        primary["aliasCount"] = len(aliases)
        primary["aliasNames"] = [str(row.get("name")) for row in ordered if row.get("name") and row.get("name") != primary.get("name")]
        merged.append(primary)
    return merged


def deduped_aliases_for_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aliases: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        entries = [
            {
                "name": row.get("name"),
                "source": row.get("source"),
                "confidence": row.get("confidence"),
                "size": row.get("size"),
                "entry": row.get("entry"),
            }
        ]
        for alias in (row.get("evidence") or {}).get("aliases") or []:
            if isinstance(alias, dict):
                entries.append(
                    {
                        "name": alias.get("name"),
                        "source": alias.get("source"),
                        "confidence": alias.get("confidence"),
                        "size": alias.get("size"),
                        "entry": alias.get("entry"),
                    }
                )
        for entry in entries:
            key = (
                str(entry.get("name") or ""),
                str(entry.get("source") or ""),
                str(entry.get("confidence") or ""),
                str(entry.get("entry") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            aliases.append(entry)
    return aliases


def candidate_merge_priority(row: dict[str, Any]) -> tuple[int, int, int, str]:
    source = str(row.get("source") or "")
    confidence = str(row.get("confidence") or "")
    name = str(row.get("name") or "")
    synthetic_name = name.startswith(("entry_", "range_"))
    return (
        source_priority(source),
        -confidence_rank(confidence),
        1 if synthetic_name else 0,
        name,
    )


def source_priority(source: str) -> int:
    priorities = {
        "pe-export": 0,
        "macho-symbol": 0,
        "elf-symbol": 0,
        "objdump-label": 1,
        "x86-prologue": 2,
        "x86-call-target": 3,
        "x86-post-ret-alignment": 4,
        "entrypoint": 5,
    }
    return priorities.get(source, 10)


def optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            return None
    return None


def elf_function_candidates(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for sym in inventory.get("symbols", []):
        if sym.get("type") != 2 or sym.get("sectionIndex") == 0 or sym.get("value") is None:
            continue
        candidates.append(
            {
                "name": sym.get("name") or f"sub_{int(sym['value']):x}",
                "address": int(sym["value"]),
                "size": int(sym.get("size") or 0),
                "source": "elf-symbol",
                "confidence": "high" if int(sym.get("size") or 0) > 0 else "medium",
                "symbolTable": sym.get("table"),
            }
        )

    entry = inventory.get("entryVa")
    if entry is not None and not any(int(row["address"]) == int(entry) for row in candidates):
        candidates.append(
            {
                "name": f"entry_{int(entry):x}",
                "address": int(entry),
                "size": 0,
                "source": "entrypoint",
                "confidence": "medium",
            }
        )
    return candidates


def pe_function_candidates(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    image_base = int(inventory.get("imageBase") or 0)
    entry_rva = inventory.get("entryRva")
    if entry_rva is not None:
        candidates.append(
            {
                "name": f"entry_{int(entry_rva):x}",
                "rva": int(entry_rva),
                "address": image_base + int(entry_rva),
                "size": 0,
                "source": "entrypoint",
                "confidence": "medium",
            }
        )
    for export in inventory.get("exports", []):
        if export.get("forwarded"):
            continue
        rva = export.get("rva")
        if rva is None:
            continue
        rva = int(rva)
        name = str(export.get("name") or f"export_{int(export.get('ordinal') or rva):x}")
        candidates.append(
            {
                "name": normalize_label_name(name, image_base + rva),
                "rva": rva,
                "address": image_base + rva,
                "size": 0,
                "source": "pe-export",
                "confidence": "high",
                "ordinal": export.get("ordinal"),
                "evidence": {
                    "sources": ["pe-export"],
                    "name": export.get("name"),
                    "ordinal": export.get("ordinal"),
                    "nameRva": export.get("nameRva"),
                },
            }
        )
    candidates.extend(x86_boundary_candidates(inventory, image_base=image_base))

    # Without symbols or disassembly, PE executable ranges are only regions to
    # scan. They are emitted as low-confidence boundary seeds for later analysis.
    for code_range in inventory.get("codeRanges", []):
        rva = int(code_range.get("rva") or 0)
        size = int(code_range.get("size") or 0)
        candidates.append(
            {
                "name": f"range_{code_range.get('name', 'code')}_{rva:x}",
                "rva": rva,
                "address": image_base + rva,
                "size": size,
                "source": "executable-range",
                "confidence": "low",
                "section": code_range.get("name"),
                "fileOffset": code_range.get("fileOffset"),
            }
        )
    return candidates


def macho_function_candidates(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for sym in inventory.get("symbols", []):
        if sym.get("type") != 2 or sym.get("sectionIndex") == 0 or sym.get("value") is None:
            continue
        address = int(sym["value"])
        candidates.append(
            {
                "name": normalize_label_name(strip_macho_symbol_prefix(str(sym.get("name") or f"sub_{address:x}")), address),
                "address": address,
                "rva": address,
                "size": int(sym.get("size") or 0),
                "source": "macho-symbol",
                "confidence": "high" if int(sym.get("size") or 0) > 0 else "medium",
                "symbolTable": sym.get("table"),
                "section": sym.get("section"),
                "segment": sym.get("segment"),
            }
        )

    entry = inventory.get("entryVa")
    if entry is not None and not any(int(row["address"]) == int(entry) for row in candidates):
        candidates.append(
            {
                "name": f"entry_{int(entry):x}",
                "address": int(entry),
                "rva": int(entry),
                "size": 0,
                "source": "entrypoint",
                "confidence": "medium",
            }
        )
    return candidates


def strip_macho_symbol_prefix(name: str) -> str:
    if name.startswith("_") and len(name) > 1:
        return name[1:]
    return name


def x86_boundary_candidates(inventory: dict[str, Any], *, image_base: int) -> list[dict[str, Any]]:
    target = inventory.get("target") or {}
    binary_path = target.get("binaryPath")
    if not binary_path:
        return []
    arch = str(target.get("architectureHint") or "")
    if arch not in {"x86", "x86_64"}:
        return []
    try:
        data = Path(binary_path).read_bytes()
    except OSError:
        return []

    starts: dict[int, dict[str, Any]] = {}
    for code_range in inventory.get("codeRanges", []):
        base_rva = int(code_range.get("rva") or 0)
        file_offset = int(code_range.get("fileOffset") or 0)
        file_size = int(code_range.get("fileSize") or code_range.get("size") or 0)
        section = str(code_range.get("name") or "code")
        blob = data[file_offset : file_offset + file_size]
        for rel in scan_x86_prologues(blob):
            add_candidate(starts, base_rva + rel, "x86-prologue", "medium", section)
        for rel in scan_x86_call_targets(blob, base_rva):
            add_candidate(starts, rel, "x86-call-target", "medium", section)
        for rel in scan_after_ret_alignment(blob):
            add_candidate(starts, base_rva + rel, "x86-post-ret-alignment", "low", section)

    return [
        {
            "name": f"sub_{rva:x}",
            "rva": rva,
            "address": image_base + rva,
            "size": 0,
            "source": row["source"],
            "confidence": row["confidence"],
            "section": row["section"],
            "evidence": row["evidence"],
        }
        for rva, row in starts.items()
    ]


def add_candidate(candidates: dict[int, dict[str, Any]], rva: int, source: str, confidence: str, section: str) -> None:
    current = candidates.get(rva)
    evidence = {"sources": [source]}
    if current is None:
        candidates[rva] = {"source": source, "confidence": confidence, "section": section, "evidence": evidence}
        return
    if source not in current["evidence"]["sources"]:
        current["evidence"]["sources"].append(source)
    if confidence_rank(confidence) > confidence_rank(current["confidence"]):
        current["confidence"] = confidence
        current["source"] = source


def confidence_rank(value: str) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(value, 0)


def scan_x86_prologues(blob: bytes) -> list[int]:
    starts: list[int] = []
    needles = [
        b"\x55\x8b\xec",
        b"\x55\x89\xe5",
        b"\x53\x56\x57",
        b"\x56\x8b\xf1",
        b"\x57\x8b\xf9",
    ]
    for offset in range(0, max(0, len(blob) - 3)):
        if any(blob.startswith(needle, offset) for needle in needles):
            starts.append(offset)
    return starts


def scan_x86_call_targets(blob: bytes, base_rva: int) -> list[int]:
    targets: list[int] = []
    for offset in range(0, max(0, len(blob) - 5)):
        opcode = blob[offset]
        if opcode not in {0xE8, 0xE9}:
            continue
        rel = int.from_bytes(blob[offset + 1 : offset + 5], "little", signed=True)
        target = base_rva + offset + 5 + rel
        if base_rva <= target < base_rva + len(blob):
            targets.append(target)
    return targets


def scan_after_ret_alignment(blob: bytes) -> list[int]:
    starts: list[int] = []
    ret_opcodes = {0xC3, 0xCB, 0xC2, 0xCA}
    for offset in range(0, max(0, len(blob) - 8)):
        opcode = blob[offset]
        if opcode not in ret_opcodes:
            continue
        ret_size = 3 if opcode in {0xC2, 0xCA} else 1
        cursor = offset + ret_size
        while cursor < len(blob) and blob[cursor] in {0x90, 0xCC, 0x00}:
            cursor += 1
        if cursor < len(blob) and looks_like_x86_start(blob, cursor):
            starts.append(cursor)
    return starts


def looks_like_x86_start(blob: bytes, offset: int) -> bool:
    return blob.startswith((b"\x55\x8b\xec", b"\x55\x89\xe5", b"\x56\x8b\xf1", b"\x57\x8b\xf9"), offset)


def summarize_candidates(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, int] = {}
    by_confidence: dict[str, int] = {}
    for row in candidates:
        by_source[str(row.get("source"))] = by_source.get(str(row.get("source")), 0) + 1
        by_confidence[str(row.get("confidence"))] = by_confidence.get(str(row.get("confidence")), 0) + 1
    return {
        "candidateCount": len(candidates),
        "bySource": dict(sorted(by_source.items())),
        "byConfidence": dict(sorted(by_confidence.items())),
    }


def write_function_candidates(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
