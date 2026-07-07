#!/usr/bin/env python3
"""Generate source in one shot and emit an authority receipt."""

from __future__ import annotations

import argparse
import datetime as _datetime
import gzip
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BINARY_SOURCE_TOOL = ROOT / "scripts" / "binary-source-roundtrip.py"
AUTHORITY_TOOL = ROOT / "scripts" / "source-authority-report.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


binary_source = load_module("reconkit_binary_source_roundtrip", BINARY_SOURCE_TOOL)
authority = load_module("reconkit_source_authority_report", AUTHORITY_TOOL)
proof_mod = load_module("reconkit_one_shot_source_proof", ROOT / "scripts" / "one-shot-source-proof.py")
archive_verify_mod = load_module(
    "reconkit_one_shot_source_archive_verify",
    ROOT / "scripts" / "one-shot-source-archive-verify.py",
)
deliverable_verify_mod = load_module(
    "reconkit_one_shot_source_deliverable_verify",
    ROOT / "scripts" / "one-shot-source-deliverable-verify.py",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_tree_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file() and not path.is_symlink())


def tree_file_rows(root: Path, rel_root: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in iter_tree_files(root):
        rel = path.relative_to(root).as_posix()
        rows.append(
            {
                "path": f"{rel_root}/{rel}",
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return rows


def extend_tree_manifest(rows: list[dict[str, Any]], digest: hashlib._Hash | None, root: Path, rel_root: str) -> None:
    if not root.exists():
        return
    for row in tree_file_rows(root, rel_root):
        rows.append(row)
        if digest is not None:
            digest.update(str(row["path"]).encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(row["size"]).encode("ascii"))
            digest.update(b"\0")
            digest.update(str(row["sha256"]).encode("ascii"))
            digest.update(b"\n")


def tool_info(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    version = ""
    if path:
        try:
            proc = subprocess.run([path, "--version"], text=True, capture_output=True, check=False, timeout=10)
            version = (proc.stdout or proc.stderr).splitlines()[0] if (proc.stdout or proc.stderr) else ""
        except Exception as exc:
            version = f"version-error: {exc}"
    return {"path": path, "version": version}


def run_optional(args: list[str], timeout: int, cwd: Path) -> dict[str, Any]:
    tool = shutil.which(args[0])
    if tool is None:
        return {"available": False, "command": args, "returnCode": None, "stdout": "", "stderr": ""}
    try:
        proc = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {
            "available": True,
            "command": args,
            "returnCode": 124,
            "stdout": "",
            "stderr": f"command timed out after {timeout}s",
        }
    return {
        "available": True,
        "command": args,
        "returnCode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def parse_readelf_sections(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("[") or "]" not in stripped:
            continue
        parts = stripped.replace("[", " ").replace("]", " ").split()
        if len(parts) < 6 or not parts[0].isdigit():
            continue
        sections.append(
            {
                "index": int(parts[0]),
                "name": parts[1],
                "type": parts[2],
                "address": parts[3],
                "offset": parts[4],
                "size": parts[5],
            }
        )
    return sections


def parse_readelf_symbols(text: str) -> dict[str, Any]:
    total = 0
    functions: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or ":" not in stripped:
            continue
        parts = stripped.split()
        if len(parts) < 8 or not parts[0].rstrip(":").isdigit():
            continue
        total += 1
        if parts[3] == "FUNC":
            functions.append(
                {
                    "name": parts[7],
                    "value": parts[1],
                    "size": parts[2],
                    "binding": parts[4],
                    "visibility": parts[5],
                    "section": parts[6],
                }
            )
    return {"symbolCount": total, "functionSymbolCount": len(functions), "functionSymbols": functions[:200]}


def parse_json_stdout(result: dict[str, Any]) -> Any:
    if result.get("returnCode") != 0 or not result.get("stdout"):
        return None
    try:
        return json.loads(str(result.get("stdout")))
    except json.JSONDecodeError:
        return None


def parse_rabin_sections(result: dict[str, Any]) -> list[dict[str, Any]]:
    data = parse_json_stdout(result)
    sections = data.get("sections") if isinstance(data, dict) else None
    if not isinstance(sections, list):
        return []
    out: list[dict[str, Any]] = []
    for index, item in enumerate(sections):
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "index": index,
                "name": item.get("name"),
                "type": "PE",
                "address": f"{int(item.get('vaddr') or 0):x}",
                "offset": f"{int(item.get('paddr') or 0):x}",
                "size": f"{int(item.get('size') or item.get('vsize') or 0):x}",
                "vsize": int(item.get("vsize") or 0),
                "perm": item.get("perm"),
            }
        )
    return out


def parse_r2_functions(result: dict[str, Any], sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    data = parse_json_stdout(result)
    if not isinstance(data, list):
        return []
    executable_sections = []
    for section in sections:
        perm = str(section.get("perm") or "")
        if "x" not in perm:
            continue
        address = parse_int_value(str(section.get("address") or ""), 16)
        size = parse_int_value(str(section.get("size") or ""), 16)
        offset = parse_int_value(str(section.get("offset") or ""), 16)
        if address is None or size is None or offset is None or size <= 0:
            continue
        executable_sections.append((section, address, size, offset))
    functions: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        offset = item.get("offset")
        size = item.get("realsz") or item.get("size")
        if not name or not isinstance(offset, int) or not isinstance(size, int) or size <= 0:
            continue
        section_hit = None
        for section, start, section_size, _section_offset in executable_sections:
            if start <= offset < start + section_size and offset + size <= start + section_size:
                section_hit = section
                break
        if section_hit is None:
            continue
        key = (name, offset)
        if key in seen:
            continue
        seen.add(key)
        functions.append(
            {
                "name": name,
                "value": f"{offset:x}",
                "size": str(size),
                "binding": "analysis",
                "visibility": "unknown",
                "section": str(section_hit.get("index")),
                "sectionName": section_hit.get("name"),
                "source": "radare2-aflj",
                "type": item.get("type"),
                "calltype": item.get("calltype"),
                "nbbs": item.get("nbbs"),
                "ninstrs": item.get("ninstrs"),
                "edges": item.get("edges"),
            }
        )
    return functions


def write_binary_evidence(out_dir: Path, binary: Path, timeout: int) -> dict[str, Any]:
    evidence_binary = out_dir / "original.bin" if (out_dir / "original.bin").exists() else binary
    data = evidence_binary.read_bytes()
    magic = data[:16].hex()
    file_proc = run_optional(["file", "-b", "original.bin" if evidence_binary.parent == out_dir else str(evidence_binary)], timeout, out_dir)
    readelf_header = run_optional(["readelf", "-h", "original.bin" if evidence_binary.parent == out_dir else str(evidence_binary)], timeout, out_dir)
    readelf_sections = run_optional(["readelf", "-W", "-S", "original.bin" if evidence_binary.parent == out_dir else str(evidence_binary)], timeout, out_dir)
    readelf_symbols = run_optional(["readelf", "-W", "-s", "original.bin" if evidence_binary.parent == out_dir else str(evidence_binary)], timeout, out_dir)
    rabin_info = run_optional(["rabin2", "-I", "-j", "original.bin" if evidence_binary.parent == out_dir else str(evidence_binary)], timeout, out_dir)
    rabin_sections = run_optional(["rabin2", "-S", "-j", "original.bin" if evidence_binary.parent == out_dir else str(evidence_binary)], timeout, out_dir)
    # PE binaries in this workspace can hide most reachable code from radare2's
    # lighter `aaa` pass. The deeper pass and analysis toggles are still only
    # boundary hints; later slice replay decides which candidates are usable.
    r2_functions = run_optional(
        [
            "r2",
            "-2",
            "-q",
            "-e",
            "anal.hasnext=true",
            "-e",
            "anal.jmp.indir=true",
            "-e",
            "anal.pushret=true",
            "-e",
            "anal.types.constraint=false",
            "-c",
            "aaaa",
            "-c",
            "aflj",
            "-c",
            "q",
            "original.bin" if evidence_binary.parent == out_dir else str(evidence_binary),
        ],
        timeout,
        out_dir,
    )
    sections = parse_readelf_sections(readelf_sections["stdout"]) if readelf_sections.get("returnCode") == 0 else []
    symbols = parse_readelf_symbols(readelf_symbols["stdout"]) if readelf_symbols.get("returnCode") == 0 else {
        "symbolCount": 0,
        "functionSymbolCount": 0,
        "functionSymbols": [],
    }
    is_elf = data.startswith(b"\x7fELF")
    rabin_info_json = parse_json_stdout(rabin_info)
    rabin_info_obj = rabin_info_json.get("info") if isinstance(rabin_info_json, dict) else {}
    is_pe = isinstance(rabin_info_obj, dict) and rabin_info_obj.get("bintype") == "pe"
    if is_pe:
        sections = parse_rabin_sections(rabin_sections)
        pe_functions = parse_r2_functions(r2_functions, sections)
        symbols = {
            "symbolCount": len(pe_functions),
            "functionSymbolCount": len(pe_functions),
            "functionSymbols": pe_functions,
        }
    function_source = "readelf-symbol-table" if is_elf else ("radare2-aflj" if is_pe else None)
    doc = {
        "schema": "reconkit.one-shot-source-binary-evidence.v1",
        "status": "recorded",
        "original": {
            "path": "original.bin" if evidence_binary.parent == out_dir else str(binary),
            "sha256": sha256_file(evidence_binary),
            "size": evidence_binary.stat().st_size,
            "magicHexPrefix": magic,
        },
        "format": {
            "kind": "elf" if is_elf else ("pe" if is_pe else "unknown-or-non-elf"),
            "fileSummary": file_proc.get("stdout", "").strip(),
            "readelfHeaderAvailable": readelf_header.get("returnCode") == 0,
            "rabin2InfoAvailable": rabin_info.get("returnCode") == 0,
            "rabin2": rabin_info_obj if isinstance(rabin_info_obj, dict) else {},
        },
        "sections": {
            "available": bool(sections),
            "count": len(sections),
            "items": sections[:200],
        },
        "symbols": {
            "available": readelf_symbols.get("returnCode") == 0,
            **symbols,
        },
        "functionBoundaryHints": {
            "status": "hints-present" if symbols.get("functionSymbolCount") else "absent",
            "source": function_source if symbols.get("functionSymbolCount") else None,
            "count": symbols.get("functionSymbolCount"),
            "verifiedAgainstSource": False,
        },
        "tools": {
            "file": {"available": file_proc.get("available"), "returnCode": file_proc.get("returnCode")},
            "readelfHeader": {"available": readelf_header.get("available"), "returnCode": readelf_header.get("returnCode")},
            "readelfSections": {"available": readelf_sections.get("available"), "returnCode": readelf_sections.get("returnCode")},
            "readelfSymbols": {"available": readelf_symbols.get("available"), "returnCode": readelf_symbols.get("returnCode")},
            "rabin2Info": {"available": rabin_info.get("available"), "returnCode": rabin_info.get("returnCode")},
            "rabin2Sections": {"available": rabin_sections.get("available"), "returnCode": rabin_sections.get("returnCode")},
            "radare2Functions": {"available": r2_functions.get("available"), "returnCode": r2_functions.get("returnCode")},
        },
        "claimBoundary": (
            "This is binary-analysis evidence for future semantic recovery. Symbol and section data are hints only "
            "until mapped to source and verified with per-slice byte identity."
        ),
    }
    (out_dir / "BINARY_EVIDENCE.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc


def write_function_boundary_candidates(out_dir: Path) -> dict[str, Any]:
    binary_evidence = json.loads((out_dir / "BINARY_EVIDENCE.json").read_text()) if (out_dir / "BINARY_EVIDENCE.json").exists() else {}
    symbols = binary_evidence.get("symbols") if isinstance(binary_evidence.get("symbols"), dict) else {}
    function_symbols = symbols.get("functionSymbols") if isinstance(symbols.get("functionSymbols"), list) else []
    candidates = []
    for item in function_symbols:
        if not isinstance(item, dict):
            continue
        candidates.append(
            {
                "name": item.get("name"),
                "address": item.get("value"),
                "size": item.get("size"),
                "section": item.get("section"),
                "sectionName": item.get("sectionName"),
                "binding": item.get("binding"),
                "source": item.get("source") or "readelf-symbol-table",
                "type": item.get("type"),
                "calltype": item.get("calltype"),
                "nbbs": item.get("nbbs"),
                "ninstrs": item.get("ninstrs"),
                "edges": item.get("edges"),
                "verifiedAgainstSource": False,
                "semanticStatus": "boundary-hint-only",
            }
        )
    doc = {
        "schema": "reconkit.one-shot-source-function-boundary-candidates.v1",
        "status": "hints-present" if candidates else "absent",
        "source": "BINARY_EVIDENCE.json",
        "binaryEvidenceSha256": sha256_file(out_dir / "BINARY_EVIDENCE.json") if (out_dir / "BINARY_EVIDENCE.json").exists() else None,
        "candidateCount": len(candidates),
        "verifiedAgainstSource": False,
        "candidates": candidates,
        "claimBoundary": (
            "These are function-boundary candidates from binary metadata. They are not semantic recovery proof "
            "until mapped to source and verified with per-function byte identity."
        ),
    }
    (out_dir / "FUNCTION_BOUNDARY_CANDIDATES.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc


def parse_int_value(value: object, base: int = 10) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(value, base)
    except ValueError:
        return None


def write_function_byte_slices(out_dir: Path) -> dict[str, Any]:
    binary_evidence = json.loads((out_dir / "BINARY_EVIDENCE.json").read_text()) if (out_dir / "BINARY_EVIDENCE.json").exists() else {}
    boundary_candidates = (
        json.loads((out_dir / "FUNCTION_BOUNDARY_CANDIDATES.json").read_text())
        if (out_dir / "FUNCTION_BOUNDARY_CANDIDATES.json").exists()
        else {}
    )
    function_byte_slices = (
        json.loads((out_dir / "FUNCTION_BYTE_SLICES.json").read_text())
        if (out_dir / "FUNCTION_BYTE_SLICES.json").exists()
        else {}
    )
    function_slice_sources = (
        json.loads((out_dir / "FUNCTION_SLICE_SOURCES.json").read_text())
        if (out_dir / "FUNCTION_SLICE_SOURCES.json").exists()
        else {}
    )
    reconstruction_tasks = (
        json.loads((out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").read_text())
        if (out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").exists()
        else {}
    )
    original_path = out_dir / "original.bin"
    original = original_path.read_bytes() if original_path.exists() else b""
    sections_doc = binary_evidence.get("sections") if isinstance(binary_evidence.get("sections"), dict) else {}
    sections = sections_doc.get("items") if isinstance(sections_doc.get("items"), list) else []
    sections_by_index = {section.get("index"): section for section in sections if isinstance(section, dict)}
    slices: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for candidate in boundary_candidates.get("candidates", []) if isinstance(boundary_candidates.get("candidates"), list) else []:
        if not isinstance(candidate, dict):
            continue
        section_index = parse_int_value(candidate.get("section"))
        symbol_size = parse_int_value(candidate.get("size"))
        symbol_addr = parse_int_value(candidate.get("address"), 16)
        if section_index is None or symbol_size is None or symbol_addr is None or symbol_size <= 0:
            skipped.append({"name": candidate.get("name"), "reason": "no-resolvable-section-address-or-size"})
            continue
        section = sections_by_index.get(section_index)
        if not isinstance(section, dict):
            skipped.append({"name": candidate.get("name"), "reason": "section-not-found", "section": candidate.get("section")})
            continue
        section_addr = parse_int_value(section.get("address"), 16)
        section_offset = parse_int_value(section.get("offset"), 16)
        section_size = parse_int_value(section.get("size"), 16)
        if section_addr is None or section_offset is None or section_size is None:
            skipped.append({"name": candidate.get("name"), "reason": "section-address-offset-size-unresolved"})
            continue
        relative = symbol_addr - section_addr
        if relative < 0 or relative + symbol_size > section_size:
            skipped.append({"name": candidate.get("name"), "reason": "symbol-outside-section"})
            continue
        file_offset = section_offset + relative
        if file_offset < 0 or file_offset + symbol_size > len(original):
            skipped.append({"name": candidate.get("name"), "reason": "slice-outside-file"})
            continue
        data = original[file_offset : file_offset + symbol_size]
        slices.append(
            {
                "name": candidate.get("name"),
                "section": candidate.get("section"),
                "sectionName": section.get("name"),
                "address": candidate.get("address"),
                "size": symbol_size,
                "fileOffset": file_offset,
                "sha256": hashlib.sha256(data).hexdigest(),
                "firstBytesHex": data[:16].hex(),
                "source": "FUNCTION_BOUNDARY_CANDIDATES.json",
                "targetBytesVerified": True,
                "verifiedAgainstSource": False,
                "semanticStatus": "target-byte-slice-only",
            }
        )
    doc = {
        "schema": "reconkit.one-shot-source-function-byte-slices.v1",
        "status": "slices-present" if slices else "absent",
        "source": "FUNCTION_BOUNDARY_CANDIDATES.json",
        "binaryEvidenceSha256": sha256_file(out_dir / "BINARY_EVIDENCE.json") if (out_dir / "BINARY_EVIDENCE.json").exists() else None,
        "functionBoundaryCandidatesSha256": sha256_file(out_dir / "FUNCTION_BOUNDARY_CANDIDATES.json")
        if (out_dir / "FUNCTION_BOUNDARY_CANDIDATES.json").exists()
        else None,
        "sliceCount": len(slices),
        "skippedCount": len(skipped),
        "targetBytesVerified": bool(slices),
        "verifiedAgainstSource": False,
        "slices": slices,
        "skipped": skipped[:500],
        "claimBoundary": (
            "These are target byte slices resolved from binary metadata. They are authoritative for target bytes only; "
            "they do not prove recovered source until matched and verified against source candidates."
        ),
    }
    (out_dir / "FUNCTION_BYTE_SLICES.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc


def safe_source_slug(value: object, fallback: str) -> str:
    text = str(value or fallback)
    text = re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_")
    return text[:80] or fallback


def write_function_slice_sources(out_dir: Path) -> dict[str, Any]:
    slices_doc = json.loads((out_dir / "FUNCTION_BYTE_SLICES.json").read_text()) if (out_dir / "FUNCTION_BYTE_SLICES.json").exists() else {}
    original_path = out_dir / "original.bin"
    original = original_path.read_bytes() if original_path.exists() else b""
    source_dir = out_dir / "function-slice-sources"
    if source_dir.exists():
        shutil.rmtree(source_dir)
    source_dir.mkdir(parents=True, exist_ok=True)
    sources: list[dict[str, Any]] = []
    for index, item in enumerate(slices_doc.get("slices", []) if isinstance(slices_doc.get("slices"), list) else []):
        if not isinstance(item, dict):
            continue
        offset = item.get("fileOffset")
        size = item.get("size")
        if not isinstance(offset, int) or not isinstance(size, int) or offset < 0 or size <= 0 or offset + size > len(original):
            continue
        data = original[offset : offset + size]
        slug = safe_source_slug(item.get("name"), f"slice_{index:04d}")
        rel = f"function-slice-sources/{index:04d}_{slug}.c"
        path = out_dir / rel
        source = "\n".join(
            [
                "/* Generated function byte-slice source. */",
                "/* Emits exact target bytes for one resolved function slice; not semantic decompilation. */",
                "#include <stdint.h>",
                "#include <stdio.h>",
                "",
                f"static const uint8_t reconkit_function_slice[{len(data)}] = {{",
                c_byte_literal(data),
                "};",
                "",
                "int main(void) {",
                "    return fwrite(reconkit_function_slice, 1, sizeof(reconkit_function_slice), stdout) == sizeof(reconkit_function_slice) ? 0 : 1;",
                "}",
                "",
            ]
        )
        path.write_text(source)
        sources.append(
            {
                "name": item.get("name"),
                "path": rel,
                "language": "c",
                "sourceRole": "generated-function-byte-emitter",
                "targetSliceSha256": item.get("sha256"),
                "targetFileOffset": offset,
                "targetSize": size,
                "sourceSha256": sha256_file(path),
                "semanticDecompilation": False,
                "verifiedAgainstSource": False,
                "claimBoundary": "Generated byte-emitter source for a function-sized target slice, not recovered source logic.",
            }
        )
    doc = {
        "schema": "reconkit.one-shot-source-function-slice-sources.v1",
        "status": "sources-present" if sources else "absent",
        "source": "FUNCTION_BYTE_SLICES.json",
        "functionByteSlicesSha256": sha256_file(out_dir / "FUNCTION_BYTE_SLICES.json")
        if (out_dir / "FUNCTION_BYTE_SLICES.json").exists()
        else None,
        "sourceCount": len(sources),
        "semanticDecompilation": False,
        "verifiedAgainstSource": False,
        "sources": sources,
        "claimBoundary": (
            "These generated C sources emit exact target function-slice bytes. They are useful source-side replay "
            "artifacts, but they are not recovered semantic source."
        ),
    }
    (out_dir / "FUNCTION_SLICE_SOURCES.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc


def write_function_reconstruction_tasks(out_dir: Path) -> dict[str, Any]:
    slices_doc = json.loads((out_dir / "FUNCTION_BYTE_SLICES.json").read_text()) if (out_dir / "FUNCTION_BYTE_SLICES.json").exists() else {}
    sources_doc = (
        json.loads((out_dir / "FUNCTION_SLICE_SOURCES.json").read_text())
        if (out_dir / "FUNCTION_SLICE_SOURCES.json").exists()
        else {}
    )
    original_path = out_dir / "original.bin"
    original = original_path.read_bytes() if original_path.exists() else b""
    task_dir = out_dir / "function-reconstruction-tasks"
    if task_dir.exists():
        shutil.rmtree(task_dir)
    task_dir.mkdir(parents=True, exist_ok=True)
    source_by_name = {
        item.get("name"): item
        for item in sources_doc.get("sources", [])
        if isinstance(item, dict) and item.get("name") is not None
    }
    tasks: list[dict[str, Any]] = []
    for index, item in enumerate(slices_doc.get("slices", []) if isinstance(slices_doc.get("slices"), list) else []):
        if not isinstance(item, dict):
            continue
        offset = item.get("fileOffset")
        size = item.get("size")
        if not isinstance(offset, int) or not isinstance(size, int) or offset < 0 or size <= 0 or offset + size > len(original):
            continue
        data = original[offset : offset + size]
        slug = safe_source_slug(item.get("name"), f"slice_{index:04d}")
        rel_dir = f"function-reconstruction-tasks/{index:04d}_{slug}"
        task_path = out_dir / rel_dir
        task_path.mkdir(parents=True, exist_ok=True)
        target_rel = f"{rel_dir}/target.bin"
        task_json_rel = f"{rel_dir}/task.json"
        readme_rel = f"{rel_dir}/README.md"
        verifier_rel = f"{rel_dir}/VERIFY_CANDIDATE.sh"
        prompt_rel = f"{rel_dir}/ONE_SHOT_SOURCE_PROMPT.md"
        (out_dir / target_rel).write_bytes(data)
        source_ref = source_by_name.get(item.get("name"), {})
        verifier = "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "cd \"$(dirname \"$0\")\"",
                "export CCACHE_DISABLE=\"${CCACHE_DISABLE:-1}\"",
                "export CCACHE_DIR=\"${CCACHE_DIR:-$PWD/.ccache}\"",
                "if [[ -f candidate-build.env ]]; then",
                "  # shellcheck disable=SC1091",
                "  source candidate-build.env",
                "fi",
                "TARGET=target.bin",
                "OUTPUT=candidate.bin",
                "rm -f candidate.o candidate.text.bin \"$OUTPUT\"",
                "if [[ -n \"${CANDIDATE_BUILD_COMMAND:-}\" ]]; then",
                "  CANDIDATE_OUTPUT=\"$OUTPUT\" bash -c \"$CANDIDATE_BUILD_COMMAND\"",
                "elif [[ -f candidate.c ]]; then",
                "  ${CC:-gcc} ${CFLAGS:--O2} -c candidate.c -o candidate.o",
                "  ${OBJCOPY:-objcopy} -O binary -j .text candidate.o candidate.text.bin",
                "  cp candidate.text.bin \"$OUTPUT\"",
                "else",
                "  echo \"missing candidate.c or CANDIDATE_BUILD_COMMAND\" >&2",
                "  exit 2",
                "fi",
                "python3 - <<'PY'",
                "import hashlib, pathlib, sys",
                "target = pathlib.Path('target.bin').read_bytes()",
                "candidate = pathlib.Path('candidate.bin').read_bytes()",
                "if hashlib.sha256(target).hexdigest() != hashlib.sha256(candidate).hexdigest() or target != candidate:",
                "    print('FUNCTION_RECONSTRUCTION_CANDIDATE_MISMATCH')",
                "    sys.exit(1)",
                "print('FUNCTION_RECONSTRUCTION_CANDIDATE_OK')",
                "PY",
                "",
            ]
        )
        verifier_path = out_dir / verifier_rel
        verifier_path.write_text(verifier)
        verifier_path.chmod(0o755)
        prompt = "\n".join(
            [
                f"# One-shot semantic source prompt: {item.get('name')}",
                "",
                "Produce a single `candidate.c` file for this function slice.",
                "",
                "Hard requirements:",
                "- Do not emit explanations, markdown fences, build logs, or alternate files.",
                "- The output must be C source only, intended to compile as one function-level translation unit.",
                "- The compiled `.text` bytes must match `target.bin` exactly.",
                "- If exact semantic recovery is impossible from the evidence here, prefer a minimal honest candidate that can be rejected by `VERIFY_CANDIDATE.sh` rather than inventing unsupported semantics.",
                "",
                "Evidence available in this task:",
                f"- Function name hint: `{item.get('name')}`",
                f"- Section: `{item.get('sectionName')}`",
                f"- Address hint: `{item.get('address')}`",
                f"- Target size: `{size}` bytes",
                f"- Target SHA256: `{hashlib.sha256(data).hexdigest()}`",
                f"- Target bytes file: `{target_rel}`",
                f"- Reference byte-emitter source: `{source_ref.get('path')}`",
                "",
                "Acceptance command:",
                "- Save your output as `candidate.c` in this task directory.",
                "- Run `./VERIFY_CANDIDATE.sh`.",
                "- Success requires `FUNCTION_RECONSTRUCTION_CANDIDATE_OK`.",
                "",
                "Claim boundary:",
                "This prompt is a semantic reconstruction request. The package does not claim semantic source recovery unless the candidate passes the acceptance command and the result is recorded as verified evidence.",
                "",
            ]
        )
        prompt_path = out_dir / prompt_rel
        prompt_path.write_text(prompt)
        task_json = {
            "schema": "reconkit.one-shot-source-function-reconstruction-task.v1",
            "name": item.get("name"),
            "status": "ready-for-semantic-source-attempt",
            "semanticDecompilation": False,
            "verifiedAgainstSource": False,
            "target": {
                "path": target_rel,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": size,
                "fileOffset": offset,
                "section": item.get("section"),
                "sectionName": item.get("sectionName"),
                "address": item.get("address"),
            },
            "referenceByteEmitter": {
                "path": source_ref.get("path"),
                "sha256": source_ref.get("sourceSha256"),
                "role": source_ref.get("sourceRole"),
            },
            "acceptance": {
                "required": "candidate semantic source must reproduce target.bin byte-for-byte for this function slice",
                "upgradeGate": "per-function-objdiff-zero-or-equivalent-scoped-byte-identity",
                "currentStatus": "not-claimed",
                "candidateVerifier": verifier_rel,
                "candidateVerifierSha256": sha256_file(verifier_path),
                "oneShotPrompt": prompt_rel,
                "oneShotPromptSha256": sha256_file(prompt_path),
                "defaultCandidateContract": (
                    "Place candidate.c in this task directory; VERIFY_CANDIDATE.sh compiles it to an object, "
                    "extracts .text with objcopy, and compares the bytes to target.bin. Set "
                    "CANDIDATE_BUILD_COMMAND to write candidate.bin for custom compiler/linker flows."
                ),
            },
            "claimBoundary": "This is a one-shot reconstruction task, not recovered semantic source.",
        }
        (out_dir / task_json_rel).write_text(json.dumps(task_json, indent=2, sort_keys=True) + "\n")
        readme = "\n".join(
            [
                f"# Function reconstruction task: {item.get('name')}",
                "",
                "This folder contains exact target bytes for one resolved function-sized slice.",
                "It is prepared for a one-shot semantic source attempt, but no semantic source is claimed here.",
                "",
                f"- Target bytes: `{target_rel}`",
                f"- Target SHA256: `{task_json['target']['sha256']}`",
                f"- Reference byte-emitter: `{source_ref.get('path')}`",
                "- Acceptance: candidate semantic source must reproduce this slice byte-for-byte.",
                "- Candidate replay: put `candidate.c` beside this file and run `./VERIFY_CANDIDATE.sh`, or set `CANDIDATE_BUILD_COMMAND` to write `candidate.bin`.",
                "",
            ]
        )
        (out_dir / readme_rel).write_text(readme)
        tasks.append(
            {
                "name": item.get("name"),
                "path": rel_dir,
                "taskJson": task_json_rel,
                "taskJsonSha256": sha256_file(out_dir / task_json_rel),
                "readme": readme_rel,
                "readmeSha256": sha256_file(out_dir / readme_rel),
                "candidateVerifier": verifier_rel,
                "candidateVerifierSha256": sha256_file(verifier_path),
                "oneShotPrompt": prompt_rel,
                "oneShotPromptSha256": sha256_file(prompt_path),
                "targetBytes": target_rel,
                "targetBytesSha256": task_json["target"]["sha256"],
                "targetSize": size,
                "targetFileOffset": offset,
                "referenceByteEmitter": source_ref.get("path"),
                "semanticDecompilation": False,
                "verifiedAgainstSource": False,
                "acceptanceGate": task_json["acceptance"]["upgradeGate"],
            }
        )
    doc = {
        "schema": "reconkit.one-shot-source-function-reconstruction-tasks.v1",
        "status": "tasks-present" if tasks else "absent",
        "source": "FUNCTION_SLICE_SOURCES.json",
        "functionByteSlicesSha256": sha256_file(out_dir / "FUNCTION_BYTE_SLICES.json")
        if (out_dir / "FUNCTION_BYTE_SLICES.json").exists()
        else None,
        "functionSliceSourcesSha256": sha256_file(out_dir / "FUNCTION_SLICE_SOURCES.json")
        if (out_dir / "FUNCTION_SLICE_SOURCES.json").exists()
        else None,
        "taskCount": len(tasks),
        "semanticDecompilation": False,
        "verifiedAgainstSource": False,
        "tasks": tasks,
        "claimBoundary": (
            "These are one-shot semantic reconstruction tasks grounded in exact function-slice bytes. "
            "They do not claim recovered semantic source until a candidate satisfies the recorded acceptance gate."
        ),
    }
    (out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc


def write_reconstruction_candidate_replay(out_dir: Path) -> None:
    script = r'''#!/usr/bin/env python3
"""Replay function reconstruction candidate gates when candidates are present."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise SystemExit(f"unexpected JSON root: {path}")
    return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json", help="Write replay JSON report.")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    manifest = read_json(ROOT / "FUNCTION_RECONSTRUCTION_TASKS.json")
    rows = []
    matched = 0
    failed = 0
    skipped = 0
    for task in manifest.get("tasks", []):
        if not isinstance(task, dict):
            continue
        task_dir = ROOT / str(task.get("path") or "")
        verifier = ROOT / str(task.get("candidateVerifier") or "")
        candidate = task_dir / "candidate.c"
        candidate_output = task_dir / "candidate.bin"
        candidate_build_env = task_dir / "candidate-build.env"
        if not candidate.exists():
            skipped += 1
            rows.append(
                {
                    "name": task.get("name"),
                    "path": task.get("path"),
                    "status": "skipped",
                    "reason": "no candidate.c present",
                    "semanticDecompilation": False,
                    "verifiedAgainstSource": False,
                }
            )
            continue
        proc = subprocess.run(
            [str(verifier)],
            cwd=task_dir,
            text=True,
            capture_output=True,
            timeout=args.timeout,
            check=False,
        )
        ok = proc.returncode == 0 and "FUNCTION_RECONSTRUCTION_CANDIDATE_OK" in proc.stdout
        candidate_output_sha = sha256_file(candidate_output) if candidate_output.exists() else None
        if ok:
            matched += 1
        else:
            failed += 1
        rows.append(
            {
                "name": task.get("name"),
                "path": task.get("path"),
                "status": "matched" if ok else "failed",
                "returnCode": proc.returncode,
                "stdout": proc.stdout[-2000:],
                "stderr": proc.stderr[-2000:],
                "semanticDecompilation": False,
                "verifiedAgainstSource": ok,
                "targetBytesSha256": task.get("targetBytesSha256"),
                "candidateSourceSha256": sha256_file(candidate),
                "candidateOutputSha256": candidate_output_sha,
                "candidateBuildEnv": str(candidate_build_env.relative_to(ROOT)) if candidate_build_env.exists() else None,
                "candidateBuildEnvSha256": sha256_file(candidate_build_env) if candidate_build_env.exists() else None,
                "byteIdentical": ok,
                "candidate": str(candidate.relative_to(ROOT)),
            }
        )
    if failed:
        status = "failed"
    elif matched and skipped:
        status = "partial"
    elif matched:
        status = "matched"
    else:
        status = "no-candidates"
    report = {
        "schema": "reconkit.one-shot-source-function-reconstruction-candidate-replay.v1",
        "status": status,
        "source": "FUNCTION_RECONSTRUCTION_TASKS.json",
        "functionReconstructionTasksSha256": sha256_file(ROOT / "FUNCTION_RECONSTRUCTION_TASKS.json"),
        "replayScriptSha256": sha256_file(ROOT / "REPLAY_RECONSTRUCTION_CANDIDATES.py"),
        "taskCount": len(manifest.get("tasks", []) if isinstance(manifest.get("tasks"), list) else []),
        "matchedCount": matched,
        "failedCount": failed,
        "skippedCount": skipped,
        "semanticDecompilation": False,
        "claimBoundary": "This replay records candidate byte identity only. Semantic source authority requires matched candidates plus upgraded semantic evidence.",
        "tasks": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    path = out_dir / "REPLAY_RECONSTRUCTION_CANDIDATES.py"
    path.write_text(script)
    path.chmod(0o755)


def write_reconstruction_candidate_importer(out_dir: Path) -> None:
    script = r'''#!/usr/bin/env python3
"""Import one-shot reconstruction candidate.c files into expected task folders."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise SystemExit(f"unexpected JSON root: {path}")
    return data


def safe_relative(path: Path) -> bool:
    return not path.is_absolute() and ".." not in path.parts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True, help="Directory containing expected function-reconstruction-tasks/*/candidate.c files.")
    parser.add_argument("--allow-extra", action="store_true", help="Allow files outside the expected candidate.c set.")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--out", type=Path, default=ROOT / "IMPORT_RECONSTRUCTION_CANDIDATES.json")
    args = parser.parse_args()
    source_dir = args.source_dir.resolve()
    if not source_dir.is_dir():
        raise SystemExit(f"source directory is missing: {source_dir}")
    manifest = read_json(ROOT / "FUNCTION_RECONSTRUCTION_TASKS.json")
    expected = {}
    for task in manifest.get("tasks", []):
        if not isinstance(task, dict):
            continue
        rel = Path(str(task.get("path") or "")) / "candidate.c"
        if not safe_relative(rel):
            raise SystemExit(f"unsafe expected candidate path: {rel}")
        expected[rel.as_posix()] = task
    seen_files = {
        path.relative_to(source_dir).as_posix()
        for path in source_dir.rglob("*")
        if path.is_file()
    }
    allowed_sidecars = {
        "EXPECTED_CANDIDATES.json",
        "ONE_SHOT_RECONSTRUCTION_REQUEST.md",
        "README.md",
    }
    extras = sorted(path for path in seen_files if path not in expected and path not in allowed_sidecars)
    if extras and not args.allow_extra:
        raise SystemExit("unexpected files in response directory: " + ", ".join(extras[:20]))
    imported = []
    skipped = []
    for rel, task in expected.items():
        src = source_dir / rel
        dst = ROOT / rel
        if not src.exists():
            skipped.append({"path": rel, "reason": "not present in source directory"})
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        imported.append({"path": rel, "task": task.get("name"), "size": dst.stat().st_size})
    replay = subprocess.run(
        [sys.executable, str(ROOT / "REPLAY_RECONSTRUCTION_CANDIDATES.py"), "--timeout", str(args.timeout)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=args.timeout * max(1, len(imported) + 1),
    )
    replay_report = read_json(ROOT / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json")
    semantic_evaluation = subprocess.run(
        [sys.executable, str(ROOT / "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=args.timeout,
    )
    semantic_evaluation_report = read_json(ROOT / "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json")
    refresh = subprocess.run(
        [sys.executable, str(ROOT / "REFRESH_RECONSTRUCTION_RECEIPTS.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=args.timeout,
    ) if (ROOT / "REFRESH_RECONSTRUCTION_RECEIPTS.py").exists() else subprocess.CompletedProcess([], 0, "", "")
    refresh_report = read_json(ROOT / "REFRESH_RECONSTRUCTION_RECEIPTS.json") if (ROOT / "REFRESH_RECONSTRUCTION_RECEIPTS.json").exists() else {}
    import_status = (
        "partial"
        if imported and skipped
        else ("imported-with-extra" if imported and extras else ("imported" if imported else "no-candidates"))
    )
    report = {
        "schema": "reconkit.one-shot-source-reconstruction-candidate-import.v1",
        "status": import_status,
        "sourceDir": str(source_dir),
        "importedCount": len(imported),
        "skippedCount": len(skipped),
        "extraCount": len(extras),
        "imported": imported,
        "skipped": skipped,
        "extras": extras,
        "replayReturnCode": replay.returncode,
        "replayStdout": replay.stdout[-2000:],
        "replayStderr": replay.stderr[-2000:],
        "candidateResults": replay_report,
        "semanticAuthorityEvaluationReturnCode": semantic_evaluation.returncode,
        "semanticAuthorityEvaluationStdout": semantic_evaluation.stdout[-2000:],
        "semanticAuthorityEvaluationStderr": semantic_evaluation.stderr[-2000:],
        "semanticAuthorityEvaluation": semantic_evaluation_report,
        "receiptRefreshReturnCode": refresh.returncode,
        "receiptRefreshStdout": refresh.stdout[-2000:],
        "receiptRefreshStderr": refresh.stderr[-2000:],
        "receiptRefresh": refresh_report,
        "semanticDecompilation": False,
        "claimBoundary": "Importing candidates only records replay evidence; it does not by itself upgrade semantic source authority.",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return replay.returncode


if __name__ == "__main__":
    raise SystemExit(main())
'''
    path = out_dir / "IMPORT_RECONSTRUCTION_CANDIDATES.py"
    path.write_text(script)
    path.chmod(0o755)


def write_reconstruction_response_json_importer(out_dir: Path) -> None:
    script = r'''#!/usr/bin/env python3
"""Import a single JSON one-shot reconstruction response."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise SystemExit(f"unexpected JSON root: {path}")
    return data


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_relative(path: Path) -> bool:
    return not path.is_absolute() and ".." not in path.parts


def candidate_map(response: dict[str, Any]) -> dict[str, str]:
    if isinstance(response.get("files"), dict) and isinstance(response.get("candidates"), list):
        raise SystemExit("response JSON must not contain both files and candidates")
    if isinstance(response.get("files"), dict):
        out: dict[str, str] = {}
        for path, content in response["files"].items():
            if not isinstance(path, str) or not isinstance(content, str):
                raise SystemExit("response files object must map paths to string contents")
            out[path] = content
        return out
    if isinstance(response.get("candidates"), list):
        out = {}
        for row in response["candidates"]:
            if not isinstance(row, dict):
                raise SystemExit("response candidates list contains non-object row")
            path = row.get("path")
            content = row.get("content")
            if not isinstance(path, str) or not isinstance(content, str):
                raise SystemExit("response candidate rows require string path and content")
            if path in out:
                raise SystemExit(f"response candidates list contains duplicate path: {path}")
            out[path] = content
        return out
    raise SystemExit("response JSON must contain files object or candidates list")


def candidate_build_map(response: dict[str, Any], allow_build_command: bool) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    candidates = response.get("candidates")
    if not isinstance(candidates, list):
        return out
    allowed = {"cc": "CC", "cflags": "CFLAGS", "objcopy": "OBJCOPY", "command": "CANDIDATE_BUILD_COMMAND"}
    for row in candidates:
        if not isinstance(row, dict):
            continue
        path = row.get("path")
        build = row.get("build")
        if not isinstance(path, str) or build is None:
            continue
        if not isinstance(build, dict):
            raise SystemExit(f"response build override must be an object: {path}")
        mapped: dict[str, str] = {}
        for key, env_key in allowed.items():
            value = build.get(key)
            if value is None:
                continue
            if key == "command" and not allow_build_command:
                raise SystemExit(f"response build command override requires --allow-build-command: {path}")
            if not isinstance(value, str) or "\n" in value or "\r" in value or "\0" in value:
                raise SystemExit(f"response build override has invalid {key}: {path}")
            mapped[env_key] = value
        extra = sorted(set(build) - set(allowed))
        if extra:
            raise SystemExit(f"unsupported response build override keys for {path}: {', '.join(extra)}")
        if mapped:
            out[path] = mapped
    return out


def response_contract_errors(response: dict[str, Any], supplied: dict[str, str]) -> list[str]:
    errors: list[str] = []
    if response.get("schema") not in (None, "reconkit.one-shot-source-reconstruction-response.v1"):
        errors.append("response schema mismatch")
    if response.get("semanticDecompilation") not in (None, False):
        errors.append("response claims semantic decompilation")
    task_count = response.get("taskCount")
    if task_count is not None:
        if not isinstance(task_count, int) or isinstance(task_count, bool):
            errors.append("response taskCount must be an integer")
        elif task_count != len(supplied):
            errors.append("response taskCount does not match supplied candidate count")
    source_sha = response.get("sourceSha256")
    if source_sha is not None:
        if not isinstance(source_sha, str):
            errors.append("response sourceSha256 must be a string")
        elif (ROOT / "ONE_SHOT_RECONSTRUCTION_BUNDLE.json").exists():
            actual = hashlib.sha256((ROOT / "ONE_SHOT_RECONSTRUCTION_BUNDLE.json").read_bytes()).hexdigest()
            if source_sha != actual:
                errors.append("response sourceSha256 does not match ONE_SHOT_RECONSTRUCTION_BUNDLE.json")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--response-json", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true", help="Allow missing candidate.c entries.")
    parser.add_argument("--allow-extra", action="store_true", help="Allow response paths outside the expected candidate.c set.")
    parser.add_argument("--allow-build-command", action="store_true", help="Allow structured candidates[].build.command overrides that are executed by task-local candidate verifiers.")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--out", type=Path, default=ROOT / "IMPORT_RECONSTRUCTION_RESPONSE_JSON.json")
    args = parser.parse_args()
    response_path = args.response_json.resolve()
    response = read_json(response_path)
    template = read_json(ROOT / "RECONSTRUCTION_RESPONSE_TEMPLATE.json")
    expected = {
        str(row.get("path")): row
        for row in template.get("expectedCandidates", [])
        if isinstance(row, dict) and isinstance(row.get("path"), str)
    }
    supplied = candidate_map(response)
    build_overrides = candidate_build_map(response, args.allow_build_command)
    extras = sorted(path for path in supplied if path not in expected)
    missing = sorted(path for path in expected if path not in supplied)
    invalid_paths = sorted(
        path
        for path in supplied
        if Path(path).is_absolute() or ".." in Path(path).parts or not path.endswith("/candidate.c")
    )
    contract_errors = response_contract_errors(response, supplied)
    if contract_errors:
        raise SystemExit("; ".join(contract_errors))
    if invalid_paths:
        raise SystemExit("unsafe or invalid candidate paths: " + ", ".join(invalid_paths[:20]))
    if extras and not args.allow_extra:
        raise SystemExit("unexpected response paths: " + ", ".join(extras[:20]))
    if missing and not args.allow_partial:
        raise SystemExit("missing response paths: " + ", ".join(missing[:20]))
    imported = []
    for rel, content in supplied.items():
        if rel not in expected:
            continue
        rel_path = Path(rel)
        if not safe_relative(rel_path):
            raise SystemExit(f"unsafe candidate path: {rel}")
        dst = ROOT / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content)
        build_env = build_overrides.get(rel)
        build_env_path = dst.parent / "candidate-build.env"
        if build_env:
            build_env_path.write_text(
                "\n".join(f"{key}={shlex.quote(value)}" for key, value in sorted(build_env.items())) + "\n"
            )
        elif build_env_path.exists():
            build_env_path.unlink()
        imported.append(
            {
                "path": rel,
                "task": expected[rel].get("task"),
                "size": dst.stat().st_size,
                "sha256": sha256_text(content),
                "buildOverride": bool(build_env),
                "buildOverrideKeys": sorted(build_env) if build_env else [],
            }
        )
    replay = subprocess.run(
        [sys.executable, str(ROOT / "REPLAY_RECONSTRUCTION_CANDIDATES.py"), "--timeout", str(args.timeout)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=args.timeout * max(1, len(imported) + 1),
    )
    replay_report = read_json(ROOT / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json")
    semantic_evaluation = subprocess.run(
        [sys.executable, str(ROOT / "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=args.timeout,
    )
    semantic_evaluation_report = read_json(ROOT / "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json")
    refresh = subprocess.run(
        [sys.executable, str(ROOT / "REFRESH_RECONSTRUCTION_RECEIPTS.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=args.timeout,
    ) if (ROOT / "REFRESH_RECONSTRUCTION_RECEIPTS.py").exists() else subprocess.CompletedProcess([], 0, "", "")
    refresh_report = read_json(ROOT / "REFRESH_RECONSTRUCTION_RECEIPTS.json") if (ROOT / "REFRESH_RECONSTRUCTION_RECEIPTS.json").exists() else {}
    import_status = (
        "partial"
        if imported and missing
        else ("imported-with-extra" if imported and extras else ("imported" if imported else "no-candidates"))
    )
    report = {
        "schema": "reconkit.one-shot-source-reconstruction-json-import.v1",
        "status": import_status,
        "responseJson": str(response_path),
        "responseSha256": hashlib.sha256(response_path.read_bytes()).hexdigest(),
        "expectedCount": len(expected),
        "importedCount": len(imported),
        "buildOverrideCount": len(build_overrides),
        "buildOverridePaths": sorted(build_overrides),
        "buildOverrideExpectedPaths": sorted(path for path in build_overrides if path in expected),
        "buildOverrideExtraPaths": sorted(path for path in build_overrides if path not in expected),
        "missingCount": len(missing),
        "extraCount": len(extras),
        "imported": imported,
        "missing": missing,
        "extras": extras,
        "replayReturnCode": replay.returncode,
        "replayStdout": replay.stdout[-2000:],
        "replayStderr": replay.stderr[-2000:],
        "candidateResults": replay_report,
        "semanticAuthorityEvaluationReturnCode": semantic_evaluation.returncode,
        "semanticAuthorityEvaluationStdout": semantic_evaluation.stdout[-2000:],
        "semanticAuthorityEvaluationStderr": semantic_evaluation.stderr[-2000:],
        "semanticAuthorityEvaluation": semantic_evaluation_report,
        "receiptRefreshReturnCode": refresh.returncode,
        "receiptRefreshStdout": refresh.stdout[-2000:],
        "receiptRefreshStderr": refresh.stderr[-2000:],
        "receiptRefresh": refresh_report,
        "semanticDecompilation": False,
        "claimBoundary": "A JSON response imports candidate source text only; replay and semantic evaluation remain the authority gates.",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return replay.returncode


if __name__ == "__main__":
    raise SystemExit(main())
'''
    path = out_dir / "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py"
    path.write_text(script)
    path.chmod(0o755)


def write_reconstruction_response_json_validator(out_dir: Path) -> None:
    script = r'''#!/usr/bin/env python3
"""Preflight a single JSON one-shot reconstruction response without importing files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise SystemExit(f"unexpected JSON root: {path}")
    return data


def candidate_map(response: dict[str, Any]) -> dict[str, str]:
    if isinstance(response.get("files"), dict) and isinstance(response.get("candidates"), list):
        raise SystemExit("response JSON must not contain both files and candidates")
    if isinstance(response.get("files"), dict):
        out: dict[str, str] = {}
        for path, content in response["files"].items():
            if not isinstance(path, str) or not isinstance(content, str):
                raise SystemExit("response files object must map paths to string contents")
            out[path] = content
        return out
    if isinstance(response.get("candidates"), list):
        out = {}
        for row in response["candidates"]:
            if not isinstance(row, dict):
                raise SystemExit("response candidates list contains non-object row")
            path = row.get("path")
            content = row.get("content")
            if not isinstance(path, str) or not isinstance(content, str):
                raise SystemExit("response candidate rows require string path and content")
            if path in out:
                raise SystemExit(f"response candidates list contains duplicate path: {path}")
            out[path] = content
        return out
    raise SystemExit("response JSON must contain files object or candidates list")


def candidate_build_map(response: dict[str, Any], allow_build_command: bool) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    candidates = response.get("candidates")
    if not isinstance(candidates, list):
        return out
    allowed = {"cc", "cflags", "objcopy", "command"}
    for row in candidates:
        if not isinstance(row, dict):
            continue
        path = row.get("path")
        build = row.get("build")
        if not isinstance(path, str) or build is None:
            continue
        if not isinstance(build, dict):
            raise SystemExit(f"response build override must be an object: {path}")
        mapped: dict[str, str] = {}
        for key in allowed:
            value = build.get(key)
            if value is None:
                continue
            if key == "command" and not allow_build_command:
                raise SystemExit(f"response build command override requires --allow-build-command: {path}")
            if not isinstance(value, str) or "\n" in value or "\r" in value or "\0" in value:
                raise SystemExit(f"response build override has invalid {key}: {path}")
            mapped[key] = value
        extra = sorted(set(build) - allowed)
        if extra:
            raise SystemExit(f"unsupported response build override keys for {path}: {', '.join(extra)}")
        if mapped:
            out[path] = mapped
    return out


def response_contract_errors(response: dict[str, Any], supplied: dict[str, str]) -> list[str]:
    errors: list[str] = []
    if response.get("schema") not in (None, "reconkit.one-shot-source-reconstruction-response.v1"):
        errors.append("response schema mismatch")
    if response.get("semanticDecompilation") not in (None, False):
        errors.append("response claims semantic decompilation")
    task_count = response.get("taskCount")
    if task_count is not None:
        if not isinstance(task_count, int) or isinstance(task_count, bool):
            errors.append("response taskCount must be an integer")
        elif task_count != len(supplied):
            errors.append("response taskCount does not match supplied candidate count")
    source_sha = response.get("sourceSha256")
    if source_sha is not None:
        if not isinstance(source_sha, str):
            errors.append("response sourceSha256 must be a string")
        elif (ROOT / "ONE_SHOT_RECONSTRUCTION_BUNDLE.json").exists():
            actual = hashlib.sha256((ROOT / "ONE_SHOT_RECONSTRUCTION_BUNDLE.json").read_bytes()).hexdigest()
            if source_sha != actual:
                errors.append("response sourceSha256 does not match ONE_SHOT_RECONSTRUCTION_BUNDLE.json")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--response-json", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true", help="Allow missing candidate.c entries.")
    parser.add_argument("--allow-extra", action="store_true", help="Allow response paths outside the expected candidate.c set.")
    parser.add_argument("--allow-build-command", action="store_true", help="Allow structured candidates[].build.command overrides that are executed by task-local candidate verifiers.")
    parser.add_argument("--out", type=Path, default=ROOT / "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.json")
    args = parser.parse_args()
    response_path = args.response_json.resolve()
    response = read_json(response_path)
    template = read_json(ROOT / "RECONSTRUCTION_RESPONSE_TEMPLATE.json")
    expected = {
        str(row.get("path")): row
        for row in template.get("expectedCandidates", [])
        if isinstance(row, dict) and isinstance(row.get("path"), str)
    }
    supplied = candidate_map(response)
    build_overrides = candidate_build_map(response, args.allow_build_command)
    extras = sorted(path for path in supplied if path not in expected)
    missing = sorted(path for path in expected if path not in supplied)
    invalid_paths = sorted(
        path
        for path in supplied
        if Path(path).is_absolute() or ".." in Path(path).parts or not path.endswith("/candidate.c")
    )
    errors: list[str] = []
    errors.extend(response_contract_errors(response, supplied))
    if extras and not args.allow_extra:
        errors.append("unexpected response paths: " + ", ".join(extras[:20]))
    if missing and not args.allow_partial:
        errors.append("missing response paths: " + ", ".join(missing[:20]))
    if invalid_paths:
        errors.append("unsafe or invalid candidate paths: " + ", ".join(invalid_paths[:20]))
    candidates = [
        {
            "path": path,
            "size": len(content.encode("utf-8")),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "expected": path in expected,
            "buildOverride": path in build_overrides,
            "buildOverrideKeys": sorted(build_overrides.get(path, {})),
        }
        for path, content in sorted(supplied.items())
    ]
    preflight_status = "invalid" if errors else ("partial" if missing else ("valid-with-extra" if extras else "valid"))
    report = {
        "schema": "reconkit.one-shot-source-reconstruction-json-preflight.v1",
        "status": preflight_status,
        "responseJson": str(response_path),
        "responseSha256": hashlib.sha256(response_path.read_bytes()).hexdigest(),
        "expectedCount": len(expected),
        "candidateCount": len(supplied),
        "buildOverrideCount": len(build_overrides),
        "buildOverridePaths": sorted(build_overrides),
        "buildOverrideExpectedPaths": sorted(path for path in build_overrides if path in expected),
        "buildOverrideExtraPaths": sorted(path for path in build_overrides if path not in expected),
        "missingCount": len(missing),
        "extraCount": len(extras),
        "invalidPathCount": len(invalid_paths),
        "missing": missing,
        "extras": extras,
        "invalidPaths": invalid_paths,
        "candidates": candidates,
        "errors": errors,
        "semanticDecompilation": False,
        "claimBoundary": "Preflight validates response shape only. Import, replay, and semantic authority evaluation remain required.",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''
    path = out_dir / "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py"
    path.write_text(script)
    path.chmod(0o755)


def write_reconstruction_receipt_refresher(out_dir: Path) -> None:
    script = r'''#!/usr/bin/env python3
"""Refresh package-local reconstruction receipts after candidate import.

This updates the mutable package directory after candidate replay. It does not
rebuild source archives or portable deliverable bundles; those complete-mode
artifacts are immutable snapshots and must be regenerated separately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise SystemExit(f"unexpected JSON root: {path}")
    return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_file_rows(root: Path, rel_root: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink()):
        rel = path.relative_to(root).as_posix()
        rows.append({"path": f"{rel_root}/{rel}", "size": path.stat().st_size, "sha256": sha256_file(path)})
    return rows


def extend_tree_manifest(rows: list[dict[str, Any]], digest: hashlib._Hash | None, root: Path, rel_root: str) -> None:
    for row in tree_file_rows(root, rel_root):
        rows.append(row)
        if digest is not None:
            digest.update(str(row["path"]).encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(row["size"]).encode("ascii"))
            digest.update(b"\0")
            digest.update(str(row["sha256"]).encode("ascii"))
            digest.update(b"\n")


PACKAGE_NAMES = [
    "AUTHORITATIVE_SOURCE.md",
    "AUTHORITY_SUMMARY.json",
    "AUTHORITY_GATES.json",
    "BINARY_EVIDENCE.json",
    "CANDIDATE_BUILD_RECIPE.json",
    "CLAIMS.json",
    "CONTENT_MANIFEST.json",
    "FUNCTION_BOUNDARY_CANDIDATES.json",
    "FUNCTION_BYTE_SLICES.json",
    "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json",
    "FUNCTION_RECONSTRUCTION_TASKS.json",
    "FUNCTION_SLICE_SOURCES.json",
    "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py",
    "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py",
    "EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py",
    "IMPORT_RECONSTRUCTION_CANDIDATES.py",
    "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py",
    "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py",
    "REFRESH_RECONSTRUCTION_RECEIPTS.py",
    "ONE_SHOT_RECONSTRUCTION_BUNDLE.json",
    "Makefile",
    "ONE_SHOT_RECONSTRUCTION_REQUEST.json",
    "ONE_SHOT_RECONSTRUCTION_REQUEST.md",
    "PACKAGE_PROOF.json",
    "PROOF_COMMANDS.json",
    "PROOF_COMMANDS.sh",
    "PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py",
    "README.md",
    "REPLAY_RECONSTRUCTION_CANDIDATES.py",
    "REPLAY_CANDIDATE.sh",
    "SEMANTIC_READINESS.json",
    "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json",
    "RECONSTRUCTION_RESPONSE_TEMPLATE.json",
    "SHA256SUMS",
    "SOURCE_INDEX.json",
    "SOURCE_ROLES.json",
    "TOOLCHAIN_PROVENANCE.json",
    "VERIFIED_SOURCE_CANDIDATES.json",
    "VERIFY.sh",
    "VERIFY.py",
    "binary-source-roundtrip.json",
    "candidate-source.c",
    "candidate-source-roundtrip.json",
    "c-source-roundtrip.json",
    "full-binary.S",
    "full-binary.c",
    "one-shot-source-receipt.json",
    "original.bin",
    "source-authority-report.json",
]

CONTENT_NAMES = [
    "Makefile",
    "ONE_SHOT_RECONSTRUCTION_REQUEST.json",
    "ONE_SHOT_RECONSTRUCTION_REQUEST.md",
    "BINARY_EVIDENCE.json",
    "FUNCTION_BOUNDARY_CANDIDATES.json",
    "FUNCTION_BYTE_SLICES.json",
    "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json",
    "FUNCTION_RECONSTRUCTION_TASKS.json",
    "FUNCTION_SLICE_SOURCES.json",
    "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py",
    "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py",
    "EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py",
    "IMPORT_RECONSTRUCTION_CANDIDATES.py",
    "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py",
    "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py",
    "REFRESH_RECONSTRUCTION_RECEIPTS.py",
    "ONE_SHOT_RECONSTRUCTION_BUNDLE.json",
    "REPLAY_CANDIDATE.sh",
    "REPLAY_RECONSTRUCTION_CANDIDATES.py",
    "SEMANTIC_READINESS.json",
    "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json",
    "RECONSTRUCTION_RESPONSE_TEMPLATE.json",
    "SHA256SUMS",
    "PROOF_COMMANDS.json",
    "PROOF_COMMANDS.sh",
    "PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py",
    "VERIFY.py",
    "VERIFY.sh",
    "CANDIDATE_BUILD_RECIPE.json",
    "SOURCE_ROLES.json",
    "full-binary.S",
    "full-binary.c",
    "candidate-source.c",
    "original.bin",
]

SHA_NAMES = [
    "Makefile",
    "ONE_SHOT_RECONSTRUCTION_REQUEST.md",
    "REPLAY_CANDIDATE.sh",
    "REPLAY_RECONSTRUCTION_CANDIDATES.py",
    "VERIFY.py",
    "VERIFY.sh",
    "CANDIDATE_BUILD_RECIPE.json",
    "BINARY_EVIDENCE.json",
    "FUNCTION_BOUNDARY_CANDIDATES.json",
    "FUNCTION_BYTE_SLICES.json",
    "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json",
    "FUNCTION_RECONSTRUCTION_TASKS.json",
    "FUNCTION_SLICE_SOURCES.json",
    "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py",
    "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py",
    "EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py",
    "IMPORT_RECONSTRUCTION_CANDIDATES.py",
    "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py",
    "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py",
    "REFRESH_RECONSTRUCTION_RECEIPTS.py",
    "ONE_SHOT_RECONSTRUCTION_BUNDLE.json",
    "ONE_SHOT_RECONSTRUCTION_REQUEST.json",
    "RECONSTRUCTION_RESPONSE_TEMPLATE.json",
    "SEMANTIC_READINESS.json",
    "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json",
    "SOURCE_ROLES.json",
    "full-binary.S",
    "full-binary.c",
    "candidate-source.c",
    "original.bin",
]


def write_sha256sums() -> None:
    lines: list[str] = []
    for name in SHA_NAMES:
        path = ROOT / name
        if path.exists():
            lines.append(f"{sha256_file(path)}  {name}")
    for rel_root in ("candidate-source-tree", "function-slice-sources", "function-reconstruction-tasks"):
        for row in tree_file_rows(ROOT / rel_root, rel_root):
            lines.append(f"{row['sha256']}  {row['path']}")
    (ROOT / "SHA256SUMS").write_text("\n".join(lines) + "\n")


def write_content_manifest() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    for name in CONTENT_NAMES:
        path = ROOT / name
        if not path.exists():
            continue
        row = {"path": name, "size": path.stat().st_size, "sha256": sha256_file(path)}
        rows.append(row)
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(row["size"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(row["sha256"]).encode("ascii"))
        digest.update(b"\n")
    extend_tree_manifest(rows, digest, ROOT / "candidate-source-tree", "candidate-source-tree")
    extend_tree_manifest(rows, digest, ROOT / "function-slice-sources", "function-slice-sources")
    extend_tree_manifest(rows, digest, ROOT / "function-reconstruction-tasks", "function-reconstruction-tasks")
    doc = {
        "schema": "reconkit.one-shot-source-content-manifest.v1",
        "contentIdentity": digest.hexdigest(),
        "identityScope": (
            "Stable package source content only: original bytes, generated assembler/C byte-source, "
            "optional supplied source candidate, Makefile, and standalone verifiers. "
            "Local receipts with output paths and generation times are excluded."
        ),
        "files": rows,
    }
    (ROOT / "CONTENT_MANIFEST.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc


def write_package_manifest() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for name in PACKAGE_NAMES:
        path = ROOT / name
        if path.exists():
            rows.append({"path": name, "size": path.stat().st_size, "sha256": sha256_file(path)})
    for rel_root in ("candidate-source-tree", "function-slice-sources", "function-reconstruction-tasks"):
        rows.extend(tree_file_rows(ROOT / rel_root, rel_root))
    doc = {"schema": "reconkit.one-shot-source-package-manifest.v1", "files": rows}
    (ROOT / "package-manifest.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc


def refresh_claims_and_gates(content: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt = read_json(ROOT / "one-shot-source-receipt.json")
    claims = read_json(ROOT / "CLAIMS.json")
    claims["contentIdentity"] = content.get("contentIdentity")
    claims["contentIdentityScope"] = content.get("identityScope")
    (ROOT / "CLAIMS.json").write_text(json.dumps(claims, indent=2, sort_keys=True) + "\n")

    gates = read_json(ROOT / "AUTHORITY_GATES.json")
    gates["contentIdentity"] = content.get("contentIdentity")
    gates["originalSha256"] = receipt.get("originalSha256")
    (ROOT / "AUTHORITY_GATES.json").write_text(json.dumps(gates, indent=2, sort_keys=True) + "\n")

    receipt["contentIdentity"] = content.get("contentIdentity")
    receipt["contentManifest"] = str(ROOT / "CONTENT_MANIFEST.json")
    (ROOT / "one-shot-source-receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return claims, gates


def proof_object(content: dict[str, Any], claims: dict[str, Any], gates: dict[str, Any], authority_summary: dict[str, Any] | None) -> dict[str, Any]:
    receipt = read_json(ROOT / "one-shot-source-receipt.json")
    reconstruction_tasks = read_json(ROOT / "FUNCTION_RECONSTRUCTION_TASKS.json")
    return {
        **read_json(ROOT / "PACKAGE_PROOF.json"),
        "contentIdentity": content.get("contentIdentity"),
        "authoritySummary": authority_summary,
        "authoritySummarySha256": sha256_file(ROOT / "AUTHORITY_SUMMARY.json") if (ROOT / "AUTHORITY_SUMMARY.json").exists() else None,
        "original": {"path": "original.bin", "sha256": receipt.get("originalSha256"), "size": receipt.get("originalSize")},
        "proven": claims.get("proven"),
        "notProven": claims.get("notProven"),
        "authorityGateStatus": gates.get("status"),
        "authorityGates": gates.get("gates"),
        "functionReconstructionTasks": reconstruction_tasks,
        "functionReconstructionCandidateResults": read_json(ROOT / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json"),
        "semanticAuthorityEvaluation": read_json(ROOT / "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json"),
    }


def write_authority_summary(content: dict[str, Any], claims: dict[str, Any], gates: dict[str, Any], proof: dict[str, Any]) -> dict[str, Any]:
    candidates = read_json(ROOT / "VERIFIED_SOURCE_CANDIDATES.json")
    summary = {
        **read_json(ROOT / "AUTHORITY_SUMMARY.json"),
        "schema": "reconkit.one-shot-source-authority-summary.v1",
        "status": claims.get("status"),
        "authorityClass": claims.get("authorityClass"),
        "accuracyClass": claims.get("accuracyClass"),
        "authorityContractStatus": "passed" if gates.get("status") == "passed" else "failed",
        "authorityGateStatus": gates.get("status"),
        "sourceCandidateStatus": candidates.get("status"),
        "packageProofStatus": proof.get("status"),
        "contentIdentity": content.get("contentIdentity"),
        "semanticDecompilation": False,
        "claimBoundary": proof.get("claimBoundary"),
    }
    (ROOT / "AUTHORITY_SUMMARY.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def refresh_deliverable(summary: dict[str, Any], proof: dict[str, Any]) -> bool:
    path = ROOT / "receipts" / "deliverable.json"
    if not path.exists():
        return False
    deliverable = read_json(path)
    deliverable["authoritySummary"] = summary
    deliverable["authoritySummarySha256"] = sha256_file(ROOT / "AUTHORITY_SUMMARY.json")
    deliverable["contentIdentity"] = proof.get("contentIdentity")
    deliverable["packageProofStatus"] = proof.get("status")
    deliverable["authorityGateStatus"] = proof.get("authorityGateStatus")
    deliverable["functionReconstructionCandidateResults"] = read_json(ROOT / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json")
    deliverable["semanticAuthorityEvaluation"] = read_json(ROOT / "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json")
    deliverable["semanticAuthorityEvaluator"] = proof.get("semanticAuthorityEvaluator")
    deliverable["deliverablePhase"] = "post-candidate-import-package-index"
    deliverable.pop("bundleVerifier", None)
    path.write_text(json.dumps(deliverable, indent=2, sort_keys=True) + "\n")
    return True


def retire_stale_complete_receipts() -> list[str]:
    retired: list[str] = []
    for rel in ("receipts/bundle-verify.json", "receipts/one-shot-source-result.json"):
        path = ROOT / rel
        if path.exists():
            path.unlink()
            retired.append(rel)
    return retired


def refresh() -> dict[str, Any]:
    retired = retire_stale_complete_receipts()
    write_sha256sums()
    content = write_content_manifest()
    claims, gates = refresh_claims_and_gates(content)
    proof = proof_object(content, claims, gates, read_json(ROOT / "AUTHORITY_SUMMARY.json"))
    (ROOT / "PACKAGE_PROOF.json").write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")
    summary = write_authority_summary(content, claims, gates, proof)
    proof = proof_object(content, claims, gates, summary)
    proof["authoritySummarySha256"] = sha256_file(ROOT / "AUTHORITY_SUMMARY.json")
    (ROOT / "PACKAGE_PROOF.json").write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")
    deliverable_refreshed = refresh_deliverable(summary, proof)
    write_sha256sums()
    content = write_content_manifest()
    claims, gates = refresh_claims_and_gates(content)
    proof = proof_object(content, claims, gates, summary)
    (ROOT / "PACKAGE_PROOF.json").write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")
    summary = write_authority_summary(content, claims, gates, proof)
    proof = proof_object(content, claims, gates, summary)
    proof["authoritySummarySha256"] = sha256_file(ROOT / "AUTHORITY_SUMMARY.json")
    (ROOT / "PACKAGE_PROOF.json").write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")
    package_manifest = write_package_manifest()
    return {
        "schema": "reconkit.one-shot-source-reconstruction-receipt-refresh.v1",
        "status": "refreshed",
        "contentIdentity": content.get("contentIdentity"),
        "packageManifestFileCount": len(package_manifest.get("files", [])),
        "deliverableRefreshed": deliverable_refreshed,
        "retiredCompleteReceipts": retired,
        "semanticDecompilation": False,
        "claimBoundary": "Package-directory receipts were refreshed after candidate import. Source archives and portable bundles are immutable snapshots and must be regenerated separately.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "REFRESH_RECONSTRUCTION_RECEIPTS.json")
    args = parser.parse_args()
    report = refresh()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    path = out_dir / "REFRESH_RECONSTRUCTION_RECEIPTS.py"
    path.write_text(script)
    path.chmod(0o755)


def write_reconstruction_candidate_results(out_dir: Path) -> dict[str, Any]:
    tasks_doc = json.loads((out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").read_text()) if (out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").exists() else {}
    rows = []
    for task in tasks_doc.get("tasks", []) if isinstance(tasks_doc.get("tasks"), list) else []:
        if not isinstance(task, dict):
            continue
        rows.append(
            {
                "name": task.get("name"),
                "path": task.get("path"),
                "status": "skipped",
                "reason": "no candidate.c present",
                "semanticDecompilation": False,
                "verifiedAgainstSource": False,
            }
        )
    doc = {
        "schema": "reconkit.one-shot-source-function-reconstruction-candidate-replay.v1",
        "status": "no-candidates",
        "source": "FUNCTION_RECONSTRUCTION_TASKS.json",
        "functionReconstructionTasksSha256": sha256_file(out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json")
        if (out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").exists()
        else None,
        "replayScriptSha256": sha256_file(out_dir / "REPLAY_RECONSTRUCTION_CANDIDATES.py")
        if (out_dir / "REPLAY_RECONSTRUCTION_CANDIDATES.py").exists()
        else None,
        "taskCount": len(tasks_doc.get("tasks", []) if isinstance(tasks_doc.get("tasks"), list) else []),
        "matchedCount": 0,
        "failedCount": 0,
        "skippedCount": len(rows),
        "semanticDecompilation": False,
        "claimBoundary": "This replay records candidate byte identity only. Semantic source authority requires matched candidates plus upgraded semantic evidence.",
        "tasks": rows,
    }
    (out_dir / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc


def write_one_shot_reconstruction_request(out_dir: Path) -> dict[str, Any]:
    tasks_doc = json.loads((out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").read_text()) if (out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").exists() else {}
    tasks = tasks_doc.get("tasks") if isinstance(tasks_doc.get("tasks"), list) else []
    lines = [
        "# One-shot reconstruction request",
        "",
        "Produce candidate semantic C source for every task listed below in one response.",
        "",
        "Output contract:",
        "- Return one C source file per task.",
        "- Use the exact path `function-reconstruction-tasks/<task>/candidate.c` for each file.",
        "- Do not return markdown fences, commentary, logs, or alternate filenames.",
        "- Each `candidate.c` must satisfy that task's `VERIFY_CANDIDATE.sh` acceptance gate.",
        "- If exact recovery is not possible from the evidence, emit the smallest honest C candidate that the verifier can reject; do not invent semantic certainty.",
        "",
        "Package-level replay:",
        "- After writing all `candidate.c` files, run `./REPLAY_RECONSTRUCTION_CANDIDATES.py` from the package root.",
        "- A semantic upgrade is not allowed unless the replay ledger reports matched candidates and separate semantic evidence is recorded.",
        "",
        "Tasks:",
    ]
    for task in tasks:
        if not isinstance(task, dict):
            continue
        lines.extend(
            [
                f"- `{task.get('path')}/candidate.c`",
                f"  - name: `{task.get('name')}`",
                f"  - prompt: `{task.get('oneShotPrompt')}`",
                f"  - verifier: `{task.get('candidateVerifier')}`",
                f"  - target: `{task.get('targetBytes')}`",
                f"  - target SHA256: `{task.get('targetBytesSha256')}`",
                f"  - acceptance: `{task.get('acceptanceGate')}`",
            ]
        )
    lines.extend(
        [
            "",
            "Claim boundary:",
            "This request asks for candidate semantic source. The package remains byte-authoritative only until candidates pass replay and semantic authority evidence is explicitly upgraded.",
            "",
        ]
    )
    path = out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.md"
    path.write_text("\n".join(lines))
    doc = {
        "path": "ONE_SHOT_RECONSTRUCTION_REQUEST.md",
        "sha256": sha256_file(path),
        "taskCount": len(tasks),
        "semanticDecompilation": False,
        "claimBoundary": "Top-level one-shot request for candidate source generation; not proof of semantic recovery.",
    }
    return doc


def write_one_shot_reconstruction_request_json(out_dir: Path) -> dict[str, Any]:
    tasks_doc = (
        json.loads((out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").read_text())
        if (out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").exists()
        else {}
    )
    template_doc = (
        json.loads((out_dir / "RECONSTRUCTION_RESPONSE_TEMPLATE.json").read_text())
        if (out_dir / "RECONSTRUCTION_RESPONSE_TEMPLATE.json").exists()
        else {}
    )
    tasks = tasks_doc.get("tasks") if isinstance(tasks_doc.get("tasks"), list) else []
    request_tasks = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        request_tasks.append(
            {
                "name": task.get("name"),
                "candidatePath": f"{task.get('path')}/candidate.c",
                "prompt": task.get("oneShotPrompt"),
                "promptSha256": task.get("oneShotPromptSha256"),
                "verifier": task.get("candidateVerifier"),
                "verifierSha256": task.get("candidateVerifierSha256"),
                "targetBytes": task.get("targetBytes"),
                "targetBytesSha256": task.get("targetBytesSha256"),
                "acceptanceGate": task.get("acceptanceGate"),
            }
        )
    doc = {
        "schema": "reconkit.one-shot-source-reconstruction-request.v1",
        "status": "candidate-source-request",
        "taskCount": len(request_tasks),
        "tasks": request_tasks,
        "preferredResponse": {
            "schema": "reconkit.one-shot-source-reconstruction-response.v1",
            "format": "json-object",
            "shape": template_doc.get("jsonResponseShape")
            or {
                "schema": "reconkit.one-shot-source-reconstruction-response.v1",
                "files": {
                    "function-reconstruction-tasks/<task>/candidate.c": "C source text"
                },
            },
            "structuredShape": template_doc.get("jsonStructuredResponseShape"),
            "replayReportShapes": template_doc.get("jsonReplayReportShapes"),
        },
        "acceptedResponsePaths": [task["candidatePath"] for task in request_tasks],
        "commands": {
            "validateJson": "./VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json",
            "validateJsonWithBuildCommand": "./VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json --allow-build-command",
            "importJson": "./IMPORT_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json",
            "importJsonWithBuildCommand": "./IMPORT_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json --allow-build-command",
            "importDirectory": "./IMPORT_RECONSTRUCTION_CANDIDATES.py --source-dir response-dir",
            "replayCandidates": "./REPLAY_RECONSTRUCTION_CANDIDATES.py",
            "evaluateSemanticAuthority": "./EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py",
            "refreshReceipts": "./REFRESH_RECONSTRUCTION_RECEIPTS.py",
        },
        "sourceArtifacts": {
            "taskManifest": {
                "path": "FUNCTION_RECONSTRUCTION_TASKS.json",
                "sha256": sha256_file(out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json")
                if (out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").exists()
                else None,
            },
            "responseTemplate": {
                "path": "RECONSTRUCTION_RESPONSE_TEMPLATE.json",
                "sha256": None,
                "hashPinnedBy": "RECONSTRUCTION_RESPONSE_TEMPLATE.json pins ONE_SHOT_RECONSTRUCTION_REQUEST.json to avoid a circular content hash.",
            },
            "markdownRequest": {
                "path": "ONE_SHOT_RECONSTRUCTION_REQUEST.md",
                "sha256": sha256_file(out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.md")
                if (out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.md").exists()
                else None,
            },
        },
        "semanticDecompilation": False,
        "claimBoundary": (
            "This is a deterministic request for candidate source. It is not proof that the response is correct "
            "or that semantic source recovery has been achieved."
        ),
    }
    path = out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.json"
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return {
        "path": "ONE_SHOT_RECONSTRUCTION_REQUEST.json",
        "sha256": sha256_file(path),
        "taskCount": len(request_tasks),
        "semanticDecompilation": False,
        "claimBoundary": "Machine-readable one-shot reconstruction request; not proof of semantic recovery.",
    }


def write_one_shot_reconstruction_bundle(out_dir: Path) -> dict[str, Any]:
    tasks_doc = (
        json.loads((out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").read_text())
        if (out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").exists()
        else {}
    )
    request_doc = (
        json.loads((out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.json").read_text())
        if (out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.json").exists()
        else {}
    )
    template_doc = (
        json.loads((out_dir / "RECONSTRUCTION_RESPONSE_TEMPLATE.json").read_text())
        if (out_dir / "RECONSTRUCTION_RESPONSE_TEMPLATE.json").exists()
        else {}
    )
    bundle_tasks = []
    task_rows = tasks_doc.get("tasks") if isinstance(tasks_doc.get("tasks"), list) else []
    for task in task_rows:
        if not isinstance(task, dict):
            continue
        prompt_rel = str(task.get("oneShotPrompt") or "")
        task_json_rel = str(task.get("taskJson") or "")
        prompt_path = out_dir / prompt_rel
        task_json_path = out_dir / task_json_rel
        task_json = json.loads(task_json_path.read_text()) if task_json_path.exists() else None
        reference_byte_emitter = task_json.get("referenceByteEmitter") if isinstance(task_json, dict) else {}
        reference_rel = str(reference_byte_emitter.get("path") or "") if isinstance(reference_byte_emitter, dict) else ""
        reference_path = out_dir / reference_rel
        target_rel = str(task.get("targetBytes") or "")
        target_path = out_dir / target_rel
        target_bytes = target_path.read_bytes() if target_path.exists() else b""
        bundle_tasks.append(
            {
                "name": task.get("name"),
                "taskPath": task.get("path"),
                "candidatePath": f"{task.get('path')}/candidate.c",
                "taskJson": task_json_rel,
                "taskJsonSha256": task.get("taskJsonSha256"),
                "task": task_json,
                "prompt": prompt_rel,
                "promptSha256": task.get("oneShotPromptSha256"),
                "promptText": prompt_path.read_text() if prompt_path.exists() else None,
                "verifier": task.get("candidateVerifier"),
                "verifierSha256": task.get("candidateVerifierSha256"),
                "targetBytes": {
                    "path": target_rel,
                    "sha256": task.get("targetBytesSha256"),
                    "size": task.get("targetSize"),
                    "hex": target_bytes.hex() if target_bytes else None,
                },
                "referenceByteEmitter": {
                    "path": reference_rel or task.get("referenceByteEmitter"),
                    "sha256": sha256_file(reference_path) if reference_path.exists() else reference_byte_emitter.get("sha256")
                    if isinstance(reference_byte_emitter, dict)
                    else None,
                    "role": reference_byte_emitter.get("role") if isinstance(reference_byte_emitter, dict) else None,
                    "sourceText": reference_path.read_text() if reference_path.exists() else None,
                },
                "acceptanceGate": task.get("acceptanceGate"),
                "semanticDecompilation": False,
                "verifiedAgainstSource": False,
            }
        )
    doc = {
        "schema": "reconkit.one-shot-source-reconstruction-request-bundle.v1",
        "status": "candidate-source-request-bundle",
        "taskCount": len(bundle_tasks),
        "tasks": bundle_tasks,
        "request": {
            "path": "ONE_SHOT_RECONSTRUCTION_REQUEST.json",
            "sha256": sha256_file(out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.json")
            if (out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.json").exists()
            else None,
            "acceptedResponsePaths": request_doc.get("acceptedResponsePaths"),
            "preferredResponse": request_doc.get("preferredResponse"),
            "commands": request_doc.get("commands"),
        },
        "responseTemplate": {
            "path": "RECONSTRUCTION_RESPONSE_TEMPLATE.json",
            "sha256": sha256_file(out_dir / "RECONSTRUCTION_RESPONSE_TEMPLATE.json")
            if (out_dir / "RECONSTRUCTION_RESPONSE_TEMPLATE.json").exists()
            else None,
            "expectedCandidates": template_doc.get("expectedCandidates"),
            "jsonResponseShape": template_doc.get("jsonResponseShape"),
            "jsonStructuredResponseShape": template_doc.get("jsonStructuredResponseShape"),
            "jsonReplayReportShapes": template_doc.get("jsonReplayReportShapes"),
        },
        "sourceArtifacts": {
            "functionReconstructionTasks": {
                "path": "FUNCTION_RECONSTRUCTION_TASKS.json",
                "sha256": sha256_file(out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json")
                if (out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").exists()
                else None,
            },
            "markdownRequest": {
                "path": "ONE_SHOT_RECONSTRUCTION_REQUEST.md",
                "sha256": sha256_file(out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.md")
                if (out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.md").exists()
                else None,
            },
            "candidateImporter": {
                "path": "IMPORT_RECONSTRUCTION_CANDIDATES.py",
                "sha256": sha256_file(out_dir / "IMPORT_RECONSTRUCTION_CANDIDATES.py")
                if (out_dir / "IMPORT_RECONSTRUCTION_CANDIDATES.py").exists()
                else None,
            },
            "byteAccurateResponseExporter": {
                "path": "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py",
                "sha256": sha256_file(out_dir / "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py")
                if (out_dir / "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py").exists()
                else None,
            },
            "jsonImporter": {
                "path": "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py",
                "sha256": sha256_file(out_dir / "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py")
                if (out_dir / "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py").exists()
                else None,
            },
            "jsonValidator": {
                "path": "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py",
                "sha256": sha256_file(out_dir / "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py")
                if (out_dir / "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py").exists()
                else None,
            },
            "receiptRefresher": {
                "path": "REFRESH_RECONSTRUCTION_RECEIPTS.py",
                "sha256": sha256_file(out_dir / "REFRESH_RECONSTRUCTION_RECEIPTS.py")
                if (out_dir / "REFRESH_RECONSTRUCTION_RECEIPTS.py").exists()
                else None,
            },
            "candidateReplay": {
                "path": "REPLAY_RECONSTRUCTION_CANDIDATES.py",
                "sha256": sha256_file(out_dir / "REPLAY_RECONSTRUCTION_CANDIDATES.py")
                if (out_dir / "REPLAY_RECONSTRUCTION_CANDIDATES.py").exists()
                else None,
            },
            "semanticAuthorityEvaluator": {
                "path": "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py",
                "sha256": sha256_file(out_dir / "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py")
                if (out_dir / "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py").exists()
                else None,
            },
        },
        "commands": request_doc.get("commands"),
        "semanticDecompilation": False,
        "claimBoundary": (
            "This bundle is a self-contained one-shot candidate-source request with embedded prompt text. "
            "It contains no recovered candidate source and proves no semantic recovery."
        ),
    }
    path = out_dir / "ONE_SHOT_RECONSTRUCTION_BUNDLE.json"
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return {
        "path": "ONE_SHOT_RECONSTRUCTION_BUNDLE.json",
        "sha256": sha256_file(path),
        "taskCount": len(bundle_tasks),
        "semanticDecompilation": False,
    }


def write_reconstruction_response_template_exporter(out_dir: Path) -> None:
    script = r'''#!/usr/bin/env python3
"""Export an empty one-shot reconstruction response skeleton."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise SystemExit(f"unexpected JSON root: {path}")
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True, help="Directory to create for the one-shot response skeleton.")
    parser.add_argument("--force", action="store_true", help="Replace an existing skeleton directory.")
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        if not args.force:
            raise SystemExit(f"output already exists: {out}")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    tasks_doc = read_json(ROOT / "FUNCTION_RECONSTRUCTION_TASKS.json")
    template_doc = read_json(ROOT / "RECONSTRUCTION_RESPONSE_TEMPLATE.json")
    expected = []
    for row in template_doc.get("expectedCandidates", []):
        if not isinstance(row, dict):
            continue
        rel = Path(str(row.get("path") or ""))
        if rel.is_absolute() or ".." in rel.parts:
            raise SystemExit(f"unsafe expected candidate path: {rel}")
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        expected.append(row)
    shutil.copy2(ROOT / "ONE_SHOT_RECONSTRUCTION_REQUEST.md", out / "ONE_SHOT_RECONSTRUCTION_REQUEST.md")
    manifest = {
        "schema": "reconkit.one-shot-source-response-skeleton.v1",
        "status": "empty",
        "taskCount": tasks_doc.get("taskCount"),
        "expectedCandidates": expected,
        "importCommand": f"{ROOT / 'IMPORT_RECONSTRUCTION_CANDIDATES.py'} --source-dir {out}",
        "semanticDecompilation": False,
        "claimBoundary": "This skeleton contains no candidate source. Fill only the listed candidate.c paths, then import and replay.",
    }
    (out / "EXPECTED_CANDIDATES.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (out / "README.md").write_text(
        "# One-shot reconstruction response skeleton\n\n"
        "Fill each expected `function-reconstruction-tasks/<task>/candidate.c` file, then run the import command in `EXPECTED_CANDIDATES.json`.\n"
        "Do not rename files or add alternate outputs unless the importer is run with `--allow-extra`.\n"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    path = out_dir / "EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py"
    path.write_text(script)
    path.chmod(0o755)


def write_byte_accurate_reconstruction_response_exporter(out_dir: Path) -> None:
    script = r'''#!/usr/bin/env python3
"""Export a byte-accurate one-shot reconstruction JSON response from the request bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise SystemExit(f"unexpected JSON root: {path}")
    return data


def candidate_source(task: dict[str, Any]) -> str:
    target = task.get("targetBytes")
    if not isinstance(target, dict) or not isinstance(target.get("hex"), str):
        raise SystemExit(f"task has no embedded target hex: {task.get('name')}")
    raw = bytes.fromhex(target["hex"])
    if hashlib.sha256(raw).hexdigest() != target.get("sha256"):
        raise SystemExit(f"embedded target hash mismatch: {task.get('name')}")
    if len(raw) != target.get("size"):
        raise SystemExit(f"embedded target size mismatch: {task.get('name')}")
    byte_lines = []
    for offset in range(0, len(raw), 16):
        chunk = raw[offset : offset + 16]
        byte_lines.append('  ".byte ' + ",".join(f"0x{value:02x}" for value in chunk) + '\\n"')
    body = "\n".join(byte_lines) if byte_lines else '  ""'
    return "\n".join(
        [
            "/*",
            " * Byte-accurate one-shot reconstruction candidate.",
            " * This source is authoritative for the task-local .text bytes only.",
            " * It is not a semantic decompilation claim.",
            f" * Task: {task.get('name')}",
            f" * Target SHA256: {target.get('sha256')}",
            " */",
            "__asm__(",
            '  ".section .text\\n"',
            body,
            ");",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True, help="Write one-shot response JSON.")
    args = parser.parse_args()
    bundle = read_json(ROOT / "ONE_SHOT_RECONSTRUCTION_BUNDLE.json")
    files: dict[str, str] = {}
    for task in bundle.get("tasks", []):
        if not isinstance(task, dict):
            continue
        path = task.get("candidatePath")
        if not isinstance(path, str) or not path.endswith("/candidate.c"):
            raise SystemExit(f"unsafe candidate path in bundle: {path}")
        files[path] = candidate_source(task)
    response = {
        "schema": "reconkit.one-shot-source-reconstruction-response.v1",
        "status": "byte-accurate-candidate-source",
        "source": "ONE_SHOT_RECONSTRUCTION_BUNDLE.json",
        "sourceSha256": hashlib.sha256((ROOT / "ONE_SHOT_RECONSTRUCTION_BUNDLE.json").read_bytes()).hexdigest(),
        "taskCount": len(files),
        "files": files,
        "semanticDecompilation": False,
        "claimBoundary": (
            "This response emits byte-accurate task-local .text candidates from embedded target bytes. "
            "It proves no semantic recovery until the normal replay and semantic authority gates say so."
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(response, indent=2, sort_keys=True) + "\n")
    print(json.dumps(response, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    path = out_dir / "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py"
    path.write_text(script)
    path.chmod(0o755)


def write_byte_accurate_reconstruction_response_prover(out_dir: Path) -> None:
    script = r'''#!/usr/bin/env python3
"""Prove the byte-accurate one-shot response path without mutating this package."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise SystemExit(f"unexpected JSON root: {path}")
    return data


def run(cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False, timeout=timeout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, help="Write proof receipt JSON.")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--keep-workdir", action="store_true")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="reconkit-byte-accurate-response-proof-") as tmp:
        work = Path(tmp) / ROOT.name
        shutil.copytree(
            ROOT,
            work,
            ignore=shutil.ignore_patterns(
                ".ccache",
                "verify-*.o",
                "verify-*.bin",
                "candidate.o",
                "candidate.bin",
                "candidate.text.bin",
            ),
        )
        response = work / "ONE_SHOT_BYTE_ACCURATE_RESPONSE.json"
        export_proc = run(
            [sys.executable, "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py", "--out", str(response)],
            work,
            args.timeout,
        )
        preflight_proc = run(
            [sys.executable, "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py", "--response-json", str(response)],
            work,
            args.timeout,
        )
        import_proc = run(
            [sys.executable, "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py", "--response-json", str(response), "--timeout", str(args.timeout)],
            work,
            args.timeout * 4,
        )
        response_doc = read_json(response) if response.exists() else {}
        preflight = read_json(work / "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.json") if (work / "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.json").exists() else {}
        import_report = read_json(work / "IMPORT_RECONSTRUCTION_RESPONSE_JSON.json") if (work / "IMPORT_RECONSTRUCTION_RESPONSE_JSON.json").exists() else {}
        candidate_results = read_json(work / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json")
        semantic = read_json(work / "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json")
        matched = int(candidate_results.get("matchedCount") or 0)
        failed = int(candidate_results.get("failedCount") or 0)
        skipped = int(candidate_results.get("skippedCount") or 0)
        task_count = int(candidate_results.get("taskCount") or 0)
        ok = (
            export_proc.returncode == 0
            and preflight_proc.returncode == 0
            and import_proc.returncode == 0
            and candidate_results.get("status") == "matched"
            and task_count > 0
            and matched == task_count
            and failed == 0
            and skipped == 0
        )
        report = {
            "schema": "reconkit.one-shot-source-byte-accurate-response-proof.v1",
            "status": "matched" if ok else "failed",
            "ok": ok,
            "sourcePackage": str(ROOT),
            "workdir": str(work) if args.keep_workdir else None,
            "response": {
                "path": str(response),
                "status": response_doc.get("status"),
                "taskCount": response_doc.get("taskCount"),
                "semanticDecompilation": response_doc.get("semanticDecompilation"),
            },
            "exportReturnCode": export_proc.returncode,
            "preflightReturnCode": preflight_proc.returncode,
            "preflightStatus": preflight.get("status"),
            "preflightMissingCount": preflight.get("missingCount"),
            "preflightExtraCount": preflight.get("extraCount"),
            "importReturnCode": import_proc.returncode,
            "importStatus": import_report.get("status"),
            "importedCount": import_report.get("importedCount"),
            "candidateReplayStatus": candidate_results.get("status"),
            "taskCount": task_count,
            "matchedCount": matched,
            "failedCount": failed,
            "skippedCount": skipped,
            "semanticAuthorityStatus": semantic.get("status"),
            "semanticAuthorityBlockers": [item.get("id") for item in semantic.get("blockers", []) if isinstance(item, dict)],
            "semanticDecompilation": False,
            "claimBoundary": (
                "This proof shows that a one-shot JSON response can produce byte-identical task-local candidate source. "
                "It does not prove semantic decompilation while semantic authority blockers remain."
            ),
        }
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report, indent=2, sort_keys=True))
        if ok:
            print("BYTE_ACCURATE_RECONSTRUCTION_RESPONSE_PROOF_OK")
        if args.keep_workdir:
            keep = ROOT / "byte-accurate-response-proof-workdir"
            if keep.exists():
                shutil.rmtree(keep)
            shutil.copytree(work, keep)
            report["workdir"] = str(keep)
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''
    path = out_dir / "PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py"
    path.write_text(script)
    path.chmod(0o755)


def write_reconstruction_response_template(out_dir: Path) -> dict[str, Any]:
    tasks_doc = (
        json.loads((out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").read_text())
        if (out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").exists()
        else {}
    )
    tasks = tasks_doc.get("tasks") if isinstance(tasks_doc.get("tasks"), list) else []
    expected = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        expected.append(
            {
                "path": f"{task.get('path')}/candidate.c",
                "task": task.get("name"),
                "oneShotPrompt": task.get("oneShotPrompt"),
                "oneShotPromptSha256": task.get("oneShotPromptSha256"),
                "candidateVerifier": task.get("candidateVerifier"),
                "candidateVerifierSha256": task.get("candidateVerifierSha256"),
                "targetBytesSha256": task.get("targetBytesSha256"),
                "semanticDecompilation": False,
            }
        )
    doc = {
        "schema": "reconkit.one-shot-source-reconstruction-response-template.v1",
        "status": "empty-template",
        "taskCount": len(expected),
        "expectedCandidates": expected,
        "oneShotReconstructionRequestSha256": sha256_file(out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.md")
        if (out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.md").exists()
        else None,
        "oneShotReconstructionRequestJsonSha256": sha256_file(out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.json")
        if (out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.json").exists()
        else None,
        "functionReconstructionTasksSha256": sha256_file(out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json")
        if (out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").exists()
        else None,
        "importerSha256": sha256_file(out_dir / "IMPORT_RECONSTRUCTION_CANDIDATES.py")
        if (out_dir / "IMPORT_RECONSTRUCTION_CANDIDATES.py").exists()
        else None,
        "jsonImporterSha256": sha256_file(out_dir / "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py")
        if (out_dir / "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py").exists()
        else None,
        "jsonValidatorSha256": sha256_file(out_dir / "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py")
        if (out_dir / "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py").exists()
        else None,
        "receiptRefresherSha256": sha256_file(out_dir / "REFRESH_RECONSTRUCTION_RECEIPTS.py")
        if (out_dir / "REFRESH_RECONSTRUCTION_RECEIPTS.py").exists()
        else None,
        "exporterSha256": sha256_file(out_dir / "EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py")
        if (out_dir / "EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py").exists()
        else None,
        "importCommand": "./IMPORT_RECONSTRUCTION_CANDIDATES.py --source-dir <response-dir>",
        "jsonImportCommand": "./IMPORT_RECONSTRUCTION_RESPONSE_JSON.py --response-json <response.json>",
        "jsonImportCommandWithBuildCommand": "./IMPORT_RECONSTRUCTION_RESPONSE_JSON.py --response-json <response.json> --allow-build-command",
        "jsonValidateCommand": "./VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json <response.json>",
        "jsonValidateCommandWithBuildCommand": "./VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json <response.json> --allow-build-command",
        "receiptRefreshCommand": "./REFRESH_RECONSTRUCTION_RECEIPTS.py",
        "jsonResponseShape": {
            "schema": "reconkit.one-shot-source-reconstruction-response.v1",
            "files": {
                "function-reconstruction-tasks/<task>/candidate.c": "C source text"
            },
        },
        "jsonStructuredResponseShape": {
            "schema": "reconkit.one-shot-source-reconstruction-response.v1",
            "candidates": [
                {
                    "path": "function-reconstruction-tasks/<task>/candidate.c",
                    "content": "C source text",
                    "build": {
                        "cc": "optional compiler executable",
                        "cflags": "optional compiler flags",
                        "objcopy": "optional objcopy executable",
                        "command": "optional custom command that writes $CANDIDATE_OUTPUT; requires --allow-build-command",
                    },
                }
            ],
        },
        "jsonReplayReportShapes": {
            "preflight": {
                "schema": "reconkit.one-shot-source-reconstruction-json-preflight.v1",
                "buildOverrideCount": "number of response candidate paths with build overrides",
                "buildOverridePaths": ["all response candidate paths with build overrides"],
                "buildOverrideExpectedPaths": ["expected candidate paths with build overrides"],
                "buildOverrideExtraPaths": ["extra candidate paths with build overrides"],
            },
            "import": {
                "schema": "reconkit.one-shot-source-reconstruction-json-import.v1",
                "buildOverrideCount": "number of response candidate paths with build overrides, including extras",
                "buildOverridePaths": ["all response candidate paths with build overrides"],
                "buildOverrideExpectedPaths": ["importable expected candidate paths with build overrides"],
                "buildOverrideExtraPaths": ["extra candidate paths with build overrides that were not imported"],
            },
        },
        "exportCommand": "./EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py --out <response-dir>",
        "semanticDecompilation": False,
        "claimBoundary": "This template defines the response file contract only; it contains no recovered source and proves no semantic recovery.",
    }
    (out_dir / "RECONSTRUCTION_RESPONSE_TEMPLATE.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc


def package_file_manifest(out_dir: Path) -> dict[str, Any]:
    names = [
        "AUTHORITATIVE_SOURCE.md",
        "AUTHORITY_SUMMARY.json",
        "AUTHORITY_GATES.json",
        "BINARY_EVIDENCE.json",
        "CANDIDATE_BUILD_RECIPE.json",
        "CLAIMS.json",
        "CONTENT_MANIFEST.json",
        "FUNCTION_BOUNDARY_CANDIDATES.json",
        "FUNCTION_BYTE_SLICES.json",
        "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json",
        "FUNCTION_RECONSTRUCTION_TASKS.json",
        "FUNCTION_SLICE_SOURCES.json",
        "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py",
        "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py",
        "EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py",
        "IMPORT_RECONSTRUCTION_CANDIDATES.py",
        "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py",
        "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py",
        "REFRESH_RECONSTRUCTION_RECEIPTS.py",
        "ONE_SHOT_RECONSTRUCTION_BUNDLE.json",
        "Makefile",
        "ONE_SHOT_RECONSTRUCTION_REQUEST.json",
        "ONE_SHOT_RECONSTRUCTION_REQUEST.md",
        "PACKAGE_PROOF.json",
        "PROOF_COMMANDS.json",
        "PROOF_COMMANDS.sh",
        "PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py",
        "README.md",
        "REPLAY_RECONSTRUCTION_CANDIDATES.py",
        "REPLAY_CANDIDATE.sh",
        "SEMANTIC_READINESS.json",
        "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json",
        "RECONSTRUCTION_RESPONSE_TEMPLATE.json",
        "SHA256SUMS",
        "SOURCE_INDEX.json",
        "SOURCE_ROLES.json",
        "TOOLCHAIN_PROVENANCE.json",
        "VERIFIED_SOURCE_CANDIDATES.json",
        "VERIFY.sh",
        "VERIFY.py",
        "binary-source-roundtrip.json",
        "candidate-source.c",
        "candidate-source-roundtrip.json",
        "c-source-roundtrip.json",
        "full-binary.S",
        "full-binary.c",
        "one-shot-source-receipt.json",
        "original.bin",
        "source-authority-report.json",
    ]
    files: list[dict[str, Any]] = []
    for name in names:
        path = out_dir / name
        if not path.exists():
            continue
        files.append(
            {
                "path": name,
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    tree = out_dir / "candidate-source-tree"
    if tree.exists():
        files.extend(tree_file_rows(tree, "candidate-source-tree"))
    function_sources = out_dir / "function-slice-sources"
    if function_sources.exists():
        files.extend(tree_file_rows(function_sources, "function-slice-sources"))
    reconstruction_tasks = out_dir / "function-reconstruction-tasks"
    if reconstruction_tasks.exists():
        files.extend(tree_file_rows(reconstruction_tasks, "function-reconstruction-tasks"))
    manifest = {
        "schema": "reconkit.one-shot-source-package-manifest.v1",
        "files": files,
    }
    (out_dir / "package-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def content_manifest(out_dir: Path) -> dict[str, Any]:
    names = [
        "Makefile",
        "ONE_SHOT_RECONSTRUCTION_REQUEST.json",
        "ONE_SHOT_RECONSTRUCTION_REQUEST.md",
        "BINARY_EVIDENCE.json",
        "FUNCTION_BOUNDARY_CANDIDATES.json",
        "FUNCTION_BYTE_SLICES.json",
        "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json",
        "FUNCTION_RECONSTRUCTION_TASKS.json",
        "FUNCTION_SLICE_SOURCES.json",
        "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py",
        "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py",
        "EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py",
        "IMPORT_RECONSTRUCTION_CANDIDATES.py",
        "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py",
        "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py",
        "REFRESH_RECONSTRUCTION_RECEIPTS.py",
        "ONE_SHOT_RECONSTRUCTION_BUNDLE.json",
        "REPLAY_CANDIDATE.sh",
        "REPLAY_RECONSTRUCTION_CANDIDATES.py",
        "SEMANTIC_READINESS.json",
        "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json",
        "RECONSTRUCTION_RESPONSE_TEMPLATE.json",
        "SHA256SUMS",
        "PROOF_COMMANDS.json",
        "PROOF_COMMANDS.sh",
        "PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py",
        "VERIFY.py",
        "VERIFY.sh",
        "CANDIDATE_BUILD_RECIPE.json",
        "SEMANTIC_READINESS.json",
        "SOURCE_ROLES.json",
        "full-binary.S",
        "full-binary.c",
        "candidate-source.c",
        "original.bin",
    ]
    files: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    for name in names:
        path = out_dir / name
        if not path.exists():
            continue
        file_sha = sha256_file(path)
        size = path.stat().st_size
        files.append({"path": name, "size": size, "sha256": file_sha})
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_sha.encode("ascii"))
        digest.update(b"\n")
    tree = out_dir / "candidate-source-tree"
    if tree.exists():
        for row in tree_file_rows(tree, "candidate-source-tree"):
            files.append(row)
            digest.update(str(row["path"]).encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(row["size"]).encode("ascii"))
            digest.update(b"\0")
            digest.update(str(row["sha256"]).encode("ascii"))
            digest.update(b"\n")
    extend_tree_manifest(files, digest, out_dir / "function-slice-sources", "function-slice-sources")
    extend_tree_manifest(files, digest, out_dir / "function-reconstruction-tasks", "function-reconstruction-tasks")
    manifest = {
        "schema": "reconkit.one-shot-source-content-manifest.v1",
        "contentIdentity": digest.hexdigest(),
        "identityScope": (
            "Stable package source content only: original bytes, generated assembler/C byte-source, "
            "optional supplied source candidate, Makefile, and standalone verifiers. "
            "Local receipts with output paths and generation times are excluded."
        ),
        "files": files,
    }
    (out_dir / "CONTENT_MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def write_sha256sums(out_dir: Path) -> None:
    names = [
        "Makefile",
        "ONE_SHOT_RECONSTRUCTION_REQUEST.md",
        "REPLAY_CANDIDATE.sh",
        "REPLAY_RECONSTRUCTION_CANDIDATES.py",
        "VERIFY.py",
        "VERIFY.sh",
        "CANDIDATE_BUILD_RECIPE.json",
        "BINARY_EVIDENCE.json",
        "FUNCTION_BOUNDARY_CANDIDATES.json",
        "FUNCTION_BYTE_SLICES.json",
        "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json",
        "FUNCTION_RECONSTRUCTION_TASKS.json",
        "FUNCTION_SLICE_SOURCES.json",
        "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py",
        "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py",
        "EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py",
        "IMPORT_RECONSTRUCTION_CANDIDATES.py",
        "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py",
        "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py",
        "REFRESH_RECONSTRUCTION_RECEIPTS.py",
        "ONE_SHOT_RECONSTRUCTION_BUNDLE.json",
        "ONE_SHOT_RECONSTRUCTION_REQUEST.json",
        "RECONSTRUCTION_RESPONSE_TEMPLATE.json",
        "SEMANTIC_READINESS.json",
        "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json",
        "SOURCE_ROLES.json",
        "full-binary.S",
        "full-binary.c",
        "candidate-source.c",
        "original.bin",
    ]
    lines = []
    for name in names:
        path = out_dir / name
        if path.exists():
            lines.append(f"{sha256_file(path)}  {name}")
    tree = out_dir / "candidate-source-tree"
    if tree.exists():
        for row in tree_file_rows(tree, "candidate-source-tree"):
            lines.append(f"{row['sha256']}  {row['path']}")
    function_sources = out_dir / "function-slice-sources"
    if function_sources.exists():
        for row in tree_file_rows(function_sources, "function-slice-sources"):
            lines.append(f"{row['sha256']}  {row['path']}")
    reconstruction_tasks = out_dir / "function-reconstruction-tasks"
    if reconstruction_tasks.exists():
        for row in tree_file_rows(reconstruction_tasks, "function-reconstruction-tasks"):
            lines.append(f"{row['sha256']}  {row['path']}")
    (out_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n")


def write_claims(out_dir: Path, receipt: dict[str, Any], content: dict[str, Any]) -> dict[str, Any]:
    source_accuracy = receipt.get("sourceAccuracy") if isinstance(receipt.get("sourceAccuracy"), dict) else {}
    claims = {
        "schema": "reconkit.one-shot-source-claims.v1",
        "status": receipt.get("status"),
        "authorityClass": receipt.get("authorityClass"),
        "accuracyClass": receipt.get("accuracyClass"),
        "sourceAccuracy": source_accuracy,
        "proven": {
            "selfContainedPackage": receipt.get("packageSelfContained") is True,
            "originalBytesIncluded": bool(receipt.get("blob")),
            "assemblerSourceRebuildsOriginalBytes": receipt.get("byteIdentical") is True,
            "cSourceEmitsOriginalBytes": receipt.get("cSourceByteIdentical") is True,
            "suppliedSourceCandidateRebuildsOriginalBytes": receipt.get("candidateSourceByteIdentical") is True
            if receipt.get("candidateSource") or receipt.get("candidateSourceDir")
            else None,
            "packageLocalVerifierPassedAtGeneration": False,
            "semanticDecompilation": False,
        },
        "notProven": [
            "full semantic decompilation",
            "human-readable recovered source logic",
            "compiler/toolchain equivalence to an original commercial build",
            "symbol, type, or function boundary recovery",
        ],
        "semanticReadiness": "not-ready"
        if int(receipt.get("semanticSourceBundlesVerified") or 0) == 0
        else "ready",
        "contentIdentity": content.get("contentIdentity"),
        "contentIdentityScope": content.get("identityScope"),
        "primaryArtifacts": {
            "assemblerSource": "full-binary.S",
            "cSource": "full-binary.c",
            "originalBytes": "original.bin",
            "makeVerify": "Makefile",
            "standaloneVerifier": "VERIFY.py",
            "shellVerifier": "VERIFY.sh",
        },
        "reports": {
            "receipt": "one-shot-source-receipt.json",
            "authority": "source-authority-report.json",
            "binaryRoundtrip": "binary-source-roundtrip.json",
            "cRoundtrip": "c-source-roundtrip.json",
            "candidateRoundtrip": "candidate-source-roundtrip.json" if receipt.get("candidateSource") else None,
            "packageManifest": "package-manifest.json",
            "contentManifest": "CONTENT_MANIFEST.json",
        },
        "claimBoundary": receipt.get("claimBoundary"),
    }
    (out_dir / "CLAIMS.json").write_text(json.dumps(claims, indent=2, sort_keys=True) + "\n")
    return claims


def write_authority_gates(
    out_dir: Path,
    receipt: dict[str, Any],
    claims: dict[str, Any],
    content: dict[str, Any],
) -> dict[str, Any]:
    proven = claims.get("proven") if isinstance(claims.get("proven"), dict) else {}
    gates = [
        {
            "id": "self-contained-original-bytes",
            "status": "passed" if proven.get("selfContainedPackage") and proven.get("originalBytesIncluded") else "failed",
            "claim": "The package includes the original byte payload used as the authority source.",
            "evidence": ["original.bin", "one-shot-source-receipt.json", "CONTENT_MANIFEST.json"],
        },
        {
            "id": "assembler-rebuild-byte-identical",
            "status": "passed" if proven.get("assemblerSourceRebuildsOriginalBytes") else "failed",
            "claim": "The generated assembler source rebuilds to the exact original bytes.",
            "evidence": ["full-binary.S", "binary-source-roundtrip.json", "VERIFY.py"],
        },
        {
            "id": "c-emitter-byte-identical",
            "status": "passed" if proven.get("cSourceEmitsOriginalBytes") else "failed",
            "claim": "The generated C byte-emitter source emits the exact original bytes.",
            "evidence": ["full-binary.c", "c-source-roundtrip.json", "VERIFY.py"],
        },
        {
            "id": "package-local-verifier",
            "status": "passed" if proven.get("packageLocalVerifierPassedAtGeneration") else "failed",
            "claim": "The package-local verifier passed during generation.",
            "evidence": ["VERIFY.py", "VERIFY.sh", "Makefile", "CLAIMS.json"],
        },
        {
            "id": "stable-content-identity",
            "status": "passed" if bool(content.get("contentIdentity")) else "failed",
            "claim": "Stable source payload identity is recorded for downstream pinning.",
            "evidence": ["CONTENT_MANIFEST.json", "SHA256SUMS"],
        },
        {
            "id": "semantic-decompilation-boundary",
            "status": "passed" if proven.get("semanticDecompilation") is False else "failed",
            "claim": "This package does not claim semantic decompilation or recovered original source logic.",
            "evidence": ["CLAIMS.json", "SOURCE_INDEX.json", "AUTHORITATIVE_SOURCE.md"],
        },
    ]
    ledger = {
        "schema": "reconkit.one-shot-source-authority-gates.v1",
        "status": "passed" if all(gate["status"] == "passed" for gate in gates) else "failed",
        "contentIdentity": content.get("contentIdentity"),
        "originalSha256": receipt.get("originalSha256"),
        "sourceAuthority": receipt.get("sourceAuthority"),
        "authorityClass": receipt.get("authorityClass"),
        "accuracyClass": receipt.get("accuracyClass"),
        "semanticDecompilation": receipt.get("semanticDecompilation"),
        "gates": gates,
    }
    (out_dir / "AUTHORITY_GATES.json").write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
    return ledger


def write_authority_summary(out_dir: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    content = json.loads((out_dir / "CONTENT_MANIFEST.json").read_text()) if (out_dir / "CONTENT_MANIFEST.json").exists() else {}
    gates = json.loads((out_dir / "AUTHORITY_GATES.json").read_text()) if (out_dir / "AUTHORITY_GATES.json").exists() else {}
    candidates = (
        json.loads((out_dir / "VERIFIED_SOURCE_CANDIDATES.json").read_text())
        if (out_dir / "VERIFIED_SOURCE_CANDIDATES.json").exists()
        else {}
    )
    proof = json.loads((out_dir / "PACKAGE_PROOF.json").read_text()) if (out_dir / "PACKAGE_PROOF.json").exists() else {}
    summary = {
        "schema": "reconkit.one-shot-source-authority-summary.v1",
        "status": receipt.get("status"),
        "authorityClass": receipt.get("authorityClass"),
        "accuracyClass": receipt.get("accuracyClass"),
        "authorityContractStatus": "passed" if gates.get("status") == "passed" else "failed",
        "authorityGateStatus": gates.get("status"),
        "sourceCandidateStatus": candidates.get("status"),
        "packageProofStatus": proof.get("status"),
        "contentIdentity": content.get("contentIdentity") or receipt.get("contentIdentity"),
        "semanticDecompilation": receipt.get("semanticDecompilation"),
        "claimBoundary": proof.get("claimBoundary") or receipt.get("claimBoundary"),
    }
    (out_dir / "AUTHORITY_SUMMARY.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def write_source_index(out_dir: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    sources = [
        {
            "path": "full-binary.S",
            "language": "assembler-with-cpp",
            "sha256": receipt.get("sourceSha256"),
            "authority": "byte-source",
            "sourceRole": "generated-assembler-byte-source",
            "sourceAuthority": "original-bytes",
            "semanticDecompilation": False,
            "rebuild": {
                "commands": [
                    "gcc -x assembler-with-cpp -c full-binary.S -o verify-standalone-asm.o",
                    "objcopy -O binary -j .reconkit_image verify-standalone-asm.o verify-standalone-asm.bin",
                ],
                "output": "verify-standalone-asm.bin",
                "expectedSha256": receipt.get("originalSha256"),
            },
        },
        {
            "path": "full-binary.c",
            "language": "c",
            "sha256": receipt.get("cSourceSha256"),
            "authority": "c-byte-emitter",
            "sourceRole": "generated-c-byte-emitter",
            "sourceAuthority": "original-bytes",
            "semanticDecompilation": False,
            "rebuild": {
                "commands": [
                    "gcc -O2 full-binary.c -o verify-standalone-c-emitter",
                    "./verify-standalone-c-emitter > verify-standalone-c.bin",
                ],
                "output": "verify-standalone-c.bin",
                "expectedSha256": receipt.get("originalSha256"),
            },
        },
    ]
    if receipt.get("candidateSource") or receipt.get("candidateSourceDir"):
        sources.append(
            {
                "path": receipt.get("candidateSourcePath") or "candidate-source-tree",
                "language": "c",
                "sha256": receipt.get("candidateSourceSha256"),
                "authority": "supplied-c-source-candidate",
                "sourceRole": "supplied-byte-exact-source-candidate",
                "sourceAuthority": "candidate-source",
                "accuracyClass": "byte-exact",
                "semanticDecompilation": False,
                "rebuild": {
                    "commands": [receipt.get("candidateReplayCommand")],
                    "output": "verify-candidate-source.bin",
                    "expectedSha256": receipt.get("originalSha256"),
                },
            }
        )
    index = {
        "schema": "reconkit.one-shot-source-index.v1",
        "status": receipt.get("status"),
        "authorityClass": receipt.get("authorityClass"),
        "accuracyClass": receipt.get("accuracyClass"),
        "original": {
            "path": "original.bin",
            "sha256": receipt.get("originalSha256"),
            "size": receipt.get("originalSize"),
        },
        "sources": sources,
        "claimBoundary": receipt.get("claimBoundary"),
    }
    (out_dir / "SOURCE_INDEX.json").write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    return index


def write_source_roles(out_dir: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    roles = [
        {
            "path": "full-binary.S",
            "role": "generated-assembler-byte-source",
            "origin": "generated-from-original-bytes",
            "sourceKind": "byte-source",
            "language": "assembler-with-cpp",
            "accuracyClass": "byte-exact",
            "accuracyScope": "rebuilds the original byte payload through assembler object extraction",
            "semanticStatus": "not-semantic-decompilation",
            "semanticDecompilation": False,
            "sha256": receipt.get("sourceSha256"),
            "evidence": ["binary-source-roundtrip.json", "SOURCE_INDEX.json", "VERIFY.py"],
        },
        {
            "path": "full-binary.c",
            "role": "generated-c-byte-emitter",
            "origin": "generated-from-original-bytes",
            "sourceKind": "byte-emitter",
            "language": "c",
            "accuracyClass": "byte-exact",
            "accuracyScope": "emits the original byte payload on stdout",
            "semanticStatus": "not-semantic-decompilation",
            "semanticDecompilation": False,
            "sha256": receipt.get("cSourceSha256"),
            "evidence": ["c-source-roundtrip.json", "SOURCE_INDEX.json", "VERIFY.py"],
        },
    ]
    if receipt.get("candidateSource") or receipt.get("candidateSourceDir"):
        roles.append(
            {
                "path": receipt.get("candidateSourcePath") or "candidate-source-tree",
                "role": "supplied-byte-exact-source-candidate",
                "origin": "supplied-by-caller",
                "sourceKind": "supplied-source",
                "language": "c",
                "accuracyClass": "byte-exact",
                "accuracyScope": "the supplied candidate produces the original byte payload under the recorded replay command",
                "semanticStatus": "unproven-semantic-equivalence",
                "semanticDecompilation": False,
                "sha256": receipt.get("candidateSourceSha256"),
                "sourceTree": receipt.get("candidateSourceTree"),
                "verificationMode": receipt.get("candidateVerificationMode"),
                "evidence": ["candidate-source-roundtrip.json", "CANDIDATE_BUILD_RECIPE.json", "REPLAY_CANDIDATE.sh"],
            }
        )
    doc = {
        "schema": "reconkit.one-shot-source-roles.v1",
        "status": receipt.get("status"),
        "authorityClass": receipt.get("authorityClass"),
        "accuracyClass": receipt.get("accuracyClass"),
        "semanticDecompilation": False,
        "roles": roles,
        "claimBoundary": (
            "Source roles distinguish byte-source and byte-emitter artifacts from supplied source candidates. "
            "Byte-exact reproduction is proven; recovered original semantics are not claimed."
        ),
    }
    (out_dir / "SOURCE_ROLES.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc


def write_semantic_readiness(out_dir: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    roles = json.loads((out_dir / "SOURCE_ROLES.json").read_text()) if (out_dir / "SOURCE_ROLES.json").exists() else {}
    binary_evidence = (
        json.loads((out_dir / "BINARY_EVIDENCE.json").read_text())
        if (out_dir / "BINARY_EVIDENCE.json").exists()
        else {}
    )
    boundary_candidates = (
        json.loads((out_dir / "FUNCTION_BOUNDARY_CANDIDATES.json").read_text())
        if (out_dir / "FUNCTION_BOUNDARY_CANDIDATES.json").exists()
        else {}
    )
    function_byte_slices = (
        json.loads((out_dir / "FUNCTION_BYTE_SLICES.json").read_text())
        if (out_dir / "FUNCTION_BYTE_SLICES.json").exists()
        else {}
    )
    function_slice_sources = (
        json.loads((out_dir / "FUNCTION_SLICE_SOURCES.json").read_text())
        if (out_dir / "FUNCTION_SLICE_SOURCES.json").exists()
        else {}
    )
    reconstruction_tasks = (
        json.loads((out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").read_text())
        if (out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").exists()
        else {}
    )
    reconstruction_candidate_results = (
        json.loads((out_dir / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json").read_text())
        if (out_dir / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json").exists()
        else {}
    )
    one_shot_request = {
        "path": "ONE_SHOT_RECONSTRUCTION_REQUEST.md",
        "sha256": sha256_file(out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.md")
        if (out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.md").exists()
        else None,
        "taskCount": reconstruction_tasks.get("taskCount"),
        "semanticDecompilation": False,
    }
    one_shot_request_json = {
        "path": "ONE_SHOT_RECONSTRUCTION_REQUEST.json",
        "sha256": sha256_file(out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.json")
        if (out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.json").exists()
        else None,
        "taskCount": reconstruction_tasks.get("taskCount"),
        "semanticDecompilation": False,
    }
    one_shot_request_bundle = {
        "path": "ONE_SHOT_RECONSTRUCTION_BUNDLE.json",
        "sha256": sha256_file(out_dir / "ONE_SHOT_RECONSTRUCTION_BUNDLE.json")
        if (out_dir / "ONE_SHOT_RECONSTRUCTION_BUNDLE.json").exists()
        else None,
        "taskCount": reconstruction_tasks.get("taskCount"),
        "semanticDecompilation": False,
    }
    one_shot_importer = {
        "path": "IMPORT_RECONSTRUCTION_CANDIDATES.py",
        "sha256": sha256_file(out_dir / "IMPORT_RECONSTRUCTION_CANDIDATES.py")
        if (out_dir / "IMPORT_RECONSTRUCTION_CANDIDATES.py").exists()
        else None,
        "semanticDecompilation": False,
    }
    verified_semantic_bundles = int(receipt.get("semanticSourceBundlesVerified") or 0)
    candidate_present = bool(receipt.get("candidateSource") or receipt.get("candidateSourceDir"))
    function_hints = binary_evidence.get("functionBoundaryHints") if isinstance(binary_evidence.get("functionBoundaryHints"), dict) else {}
    ready = verified_semantic_bundles > 0
    doc = {
        "schema": "reconkit.one-shot-source-semantic-readiness.v1",
        "status": "ready" if ready else "not-ready",
        "authorityClass": receipt.get("authorityClass"),
        "accuracyClass": receipt.get("accuracyClass"),
        "currentClaim": "byte-exact-reproduction",
        "targetClaim": "semantic-source-recovery",
        "semanticDecompilation": receipt.get("semanticDecompilation") is True,
        "semanticSourceBundlesVerified": verified_semantic_bundles,
        "suppliedCandidatePresent": candidate_present,
        "binaryEvidenceStatus": binary_evidence.get("status"),
        "functionBoundaryHintStatus": function_hints.get("status"),
        "functionBoundaryHintCount": function_hints.get("count"),
        "functionBoundaryCandidateStatus": boundary_candidates.get("status"),
        "functionBoundaryCandidateCount": boundary_candidates.get("candidateCount"),
        "functionByteSliceStatus": function_byte_slices.get("status"),
        "functionByteSliceCount": function_byte_slices.get("sliceCount"),
        "functionSliceSourceStatus": function_slice_sources.get("status"),
        "functionSliceSourceCount": function_slice_sources.get("sourceCount"),
        "functionReconstructionTaskStatus": reconstruction_tasks.get("status"),
        "functionReconstructionTaskCount": reconstruction_tasks.get("taskCount"),
        "sourceRoles": roles.get("roles"),
        "evidenceAvailable": [
            {
                "id": "binary-format-and-symbol-hints",
                "status": "present" if binary_evidence.get("status") == "recorded" else "absent",
                "evidence": ["BINARY_EVIDENCE.json"] if binary_evidence else [],
            },
            {
                "id": "function-boundary-candidates",
                "status": "present" if boundary_candidates.get("status") == "hints-present" else "absent",
                "evidence": ["FUNCTION_BOUNDARY_CANDIDATES.json"] if boundary_candidates else [],
            },
            {
                "id": "function-byte-slice-targets",
                "status": "present" if function_byte_slices.get("status") == "slices-present" else "absent",
                "evidence": ["FUNCTION_BYTE_SLICES.json"] if function_byte_slices else [],
            },
            {
                "id": "function-slice-byte-emitter-sources",
                "status": "present" if function_slice_sources.get("status") == "sources-present" else "absent",
                "evidence": ["FUNCTION_SLICE_SOURCES.json", "function-slice-sources/"] if function_slice_sources else [],
            },
            {
                "id": "function-reconstruction-tasks",
                "status": "present" if reconstruction_tasks.get("status") == "tasks-present" else "absent",
                "evidence": ["FUNCTION_RECONSTRUCTION_TASKS.json", "function-reconstruction-tasks/"] if reconstruction_tasks else [],
            },
            {
                "id": "whole-binary-byte-reproduction",
                "status": "present",
                "evidence": ["binary-source-roundtrip.json", "c-source-roundtrip.json", "VERIFY.py"],
            },
            {
                "id": "source-role-taxonomy",
                "status": "present",
                "evidence": ["SOURCE_ROLES.json", "SOURCE_INDEX.json", "VERIFIED_SOURCE_CANDIDATES.json"],
            },
            {
                "id": "supplied-candidate-byte-reproduction",
                "status": "present" if candidate_present else "absent",
                "evidence": ["candidate-source-roundtrip.json", "CANDIDATE_BUILD_RECIPE.json", "REPLAY_CANDIDATE.sh"]
                if candidate_present
                else [],
            },
        ],
        "missingForSemanticAuthority": [
            {
                "id": "function-boundary-map",
                "required": True,
                "reason": "Binary evidence may contain symbol hints, but no verified map from source functions to target binary ranges is present.",
            },
            {
                "id": "type-and-symbol-recovery",
                "required": True,
                "reason": "No authoritative recovered type, symbol, or calling-convention evidence is present.",
            },
            {
                "id": "per-function-objdiff-zero",
                "required": True,
                "reason": "No per-function semantic source slices are proven with objdiff 0 or equivalent scoped byte identity.",
            },
            {
                "id": "compiler-profile-equivalence",
                "required": True,
                "reason": "No original compiler, flags, ABI, and linker profile equivalence is proven.",
            },
        ],
        "upgradeRequirement": (
            "Upgrade to semantic-source-recovery only when verified semantic source bundles are present and scoped "
            "by function/export ranges, recovered symbol/type evidence, compiler-profile evidence, and objdiff-zero "
            "or equivalent byte-identity proof for each claimed semantic slice."
        ),
        "claimBoundary": (
            "This package is currently authoritative for byte-exact reproduction. It is not authoritative semantic "
            "source recovery until the missing semantic authority evidence is supplied and verified."
        ),
    }
    (out_dir / "SEMANTIC_READINESS.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc


def write_semantic_source_authority_evaluator(out_dir: Path) -> None:
    script = r'''#!/usr/bin/env python3
"""Evaluate whether reconstruction candidates justify semantic source authority."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise SystemExit(f"unexpected JSON root in {path}")
    return data


def evaluate(root: Path) -> dict[str, Any]:
    readiness = read_json(root / "SEMANTIC_READINESS.json")
    tasks = read_json(root / "FUNCTION_RECONSTRUCTION_TASKS.json")
    results = read_json(root / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json")
    task_count = int(tasks.get("taskCount") or 0)
    matched_count = int(results.get("matchedCount") or 0)
    failed_count = int(results.get("failedCount") or 0)
    skipped_count = int(results.get("skippedCount") or 0)
    all_candidates_matched = (
        results.get("status") == "matched"
        and task_count > 0
        and matched_count == task_count
        and failed_count == 0
        and skipped_count == 0
    )
    missing = list(readiness.get("missingForSemanticAuthority") or [])
    blockers: list[dict[str, Any]] = []
    if results.get("status") == "no-candidates" or skipped_count:
        blockers.append(
            {
                "id": "missing-reconstruction-candidates",
                "reason": "One or more task-local candidate.c files have not been supplied and replayed.",
                "evidence": ["FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json"],
            }
        )
    if results.get("status") == "failed" or failed_count:
        blockers.append(
            {
                "id": "candidate-replay-failures",
                "reason": "One or more supplied reconstruction candidates failed the task-local byte-identity gate.",
                "evidence": ["FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json"],
            }
        )
    if not all_candidates_matched:
        blockers.append(
            {
                "id": "all-candidates-not-byte-identical",
                "reason": "Semantic authority cannot be evaluated until every reconstruction task has a matched candidate.",
                "evidence": ["FUNCTION_RECONSTRUCTION_TASKS.json", "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json"],
            }
        )
    if missing:
        blockers.append(
            {
                "id": "semantic-evidence-incomplete",
                "reason": "Semantic-readiness ledger still records missing symbol, type, boundary, or compiler-profile evidence.",
                "evidence": ["SEMANTIC_READINESS.json"],
                "missing": missing,
            }
        )
    ready = all_candidates_matched and readiness.get("status") == "ready" and not missing
    return {
        "schema": "reconkit.one-shot-source-semantic-authority-evaluation.v1",
        "status": "ready" if ready else "not-ready",
        "semanticDecompilation": False,
        "currentClaim": "byte-exact-reproduction",
        "targetClaim": "semantic-source-recovery",
        "candidateReplayStatus": results.get("status"),
        "taskCount": task_count,
        "matchedCount": matched_count,
        "failedCount": failed_count,
        "skippedCount": skipped_count,
        "allCandidatesMatched": all_candidates_matched,
        "semanticReadinessStatus": readiness.get("status"),
        "functionReconstructionTasksSha256": sha256_file(root / "FUNCTION_RECONSTRUCTION_TASKS.json")
        if (root / "FUNCTION_RECONSTRUCTION_TASKS.json").exists()
        else None,
        "functionReconstructionCandidateResultsSha256": sha256_file(root / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json")
        if (root / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json").exists()
        else None,
        "semanticReadinessSha256": sha256_file(root / "SEMANTIC_READINESS.json")
        if (root / "SEMANTIC_READINESS.json").exists()
        else None,
        "evaluatorScriptSha256": sha256_file(root / "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py")
        if (root / "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py").exists()
        else None,
        "blockers": blockers,
        "upgradeRule": (
            "Only promote to semantic-source-recovery after every reconstruction task has a matched candidate "
            "and SEMANTIC_READINESS.json has no missing semantic authority evidence."
        ),
        "claimBoundary": (
            "Matched candidate bytes alone are not treated as recovered original source semantics. This artifact "
            "is the package-local promotion decision and is intentionally conservative."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json")
    args = parser.parse_args()
    report = evaluate(ROOT)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''
    path = out_dir / "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py"
    path.write_text(script)
    path.chmod(0o755)


def write_semantic_source_authority_evaluation(out_dir: Path) -> dict[str, Any]:
    evaluator_path = out_dir / "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py"
    readiness = (
        json.loads((out_dir / "SEMANTIC_READINESS.json").read_text())
        if (out_dir / "SEMANTIC_READINESS.json").exists()
        else {}
    )
    tasks = (
        json.loads((out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").read_text())
        if (out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").exists()
        else {}
    )
    results = (
        json.loads((out_dir / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json").read_text())
        if (out_dir / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json").exists()
        else {}
    )
    task_count = int(tasks.get("taskCount") or 0)
    matched_count = int(results.get("matchedCount") or 0)
    failed_count = int(results.get("failedCount") or 0)
    skipped_count = int(results.get("skippedCount") or 0)
    all_candidates_matched = (
        results.get("status") == "matched"
        and task_count > 0
        and matched_count == task_count
        and failed_count == 0
        and skipped_count == 0
    )
    missing = list(readiness.get("missingForSemanticAuthority") or [])
    blockers: list[dict[str, Any]] = []
    if results.get("status") == "no-candidates" or skipped_count:
        blockers.append(
            {
                "id": "missing-reconstruction-candidates",
                "reason": "One or more task-local candidate.c files have not been supplied and replayed.",
                "evidence": ["FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json"],
            }
        )
    if results.get("status") == "failed" or failed_count:
        blockers.append(
            {
                "id": "candidate-replay-failures",
                "reason": "One or more supplied reconstruction candidates failed the task-local byte-identity gate.",
                "evidence": ["FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json"],
            }
        )
    if not all_candidates_matched:
        blockers.append(
            {
                "id": "all-candidates-not-byte-identical",
                "reason": "Semantic authority cannot be evaluated until every reconstruction task has a matched candidate.",
                "evidence": ["FUNCTION_RECONSTRUCTION_TASKS.json", "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json"],
            }
        )
    if missing:
        blockers.append(
            {
                "id": "semantic-evidence-incomplete",
                "reason": "Semantic-readiness ledger still records missing symbol, type, boundary, or compiler-profile evidence.",
                "evidence": ["SEMANTIC_READINESS.json"],
                "missing": missing,
            }
        )
    ready = all_candidates_matched and readiness.get("status") == "ready" and not missing
    doc = {
        "schema": "reconkit.one-shot-source-semantic-authority-evaluation.v1",
        "status": "ready" if ready else "not-ready",
        "semanticDecompilation": False,
        "currentClaim": "byte-exact-reproduction",
        "targetClaim": "semantic-source-recovery",
        "candidateReplayStatus": results.get("status"),
        "taskCount": task_count,
        "matchedCount": matched_count,
        "failedCount": failed_count,
        "skippedCount": skipped_count,
        "allCandidatesMatched": all_candidates_matched,
        "semanticReadinessStatus": readiness.get("status"),
        "functionReconstructionTasksSha256": sha256_file(out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json")
        if (out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").exists()
        else None,
        "functionReconstructionCandidateResultsSha256": sha256_file(out_dir / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json")
        if (out_dir / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json").exists()
        else None,
        "semanticReadinessSha256": sha256_file(out_dir / "SEMANTIC_READINESS.json")
        if (out_dir / "SEMANTIC_READINESS.json").exists()
        else None,
        "evaluatorScriptSha256": sha256_file(evaluator_path) if evaluator_path.exists() else None,
        "blockers": blockers,
        "upgradeRule": (
            "Only promote to semantic-source-recovery after every reconstruction task has a matched candidate "
            "and SEMANTIC_READINESS.json has no missing semantic authority evidence."
        ),
        "claimBoundary": (
            "Matched candidate bytes alone are not treated as recovered original source semantics. This artifact "
            "is the package-local promotion decision and is intentionally conservative."
        ),
    }
    (out_dir / "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json").write_text(
        json.dumps(doc, indent=2, sort_keys=True) + "\n"
    )
    return doc


def write_verified_source_candidates(out_dir: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        {
            "path": "full-binary.S",
            "language": "assembler-with-cpp",
            "verificationMode": "assembler-section",
            "sourceRole": "generated-assembler-byte-source",
            "accuracyClass": "byte-exact",
            "byteIdentical": receipt.get("byteIdentical") is True,
            "semanticDecompilation": False,
            "sha256": receipt.get("sourceSha256"),
            "evidence": ["binary-source-roundtrip.json", "SOURCE_INDEX.json", "VERIFY.py"],
            "rebuildOutputSha256": receipt.get("rebuiltSha256"),
            "expectedSha256": receipt.get("originalSha256"),
        },
        {
            "path": "full-binary.c",
            "language": "c",
            "verificationMode": "c-stdout-emitter",
            "sourceRole": "generated-c-byte-emitter",
            "accuracyClass": "byte-exact",
            "byteIdentical": receipt.get("cSourceByteIdentical") is True,
            "semanticDecompilation": False,
            "sha256": receipt.get("cSourceSha256"),
            "evidence": ["c-source-roundtrip.json", "SOURCE_INDEX.json", "VERIFY.py"],
            "rebuildOutputSha256": receipt.get("originalSha256"),
            "expectedSha256": receipt.get("originalSha256"),
        },
    ]
    if receipt.get("candidateSource") or receipt.get("candidateSourceDir"):
        candidates.append(
            {
                "path": receipt.get("candidateSourcePath") or "candidate-source-tree",
                "language": "c",
                "origin": "supplied",
                "verificationMode": receipt.get("candidateVerificationMode"),
                "sourceRole": "supplied-byte-exact-source-candidate",
                "accuracyClass": "byte-exact",
                "byteIdentical": receipt.get("candidateSourceByteIdentical") is True,
                "semanticDecompilation": False,
                "sha256": receipt.get("candidateSourceSha256"),
                "sourceTree": receipt.get("candidateSourceTree"),
                "evidence": [
                    "candidate-source-roundtrip.json",
                    "CANDIDATE_BUILD_RECIPE.json",
                    "REPLAY_CANDIDATE.sh",
                    "SOURCE_INDEX.json",
                    "VERIFY.py",
                ],
                "rebuildOutputSha256": receipt.get("candidateOutputSha256"),
                "expectedSha256": receipt.get("originalSha256"),
                "replayCommand": receipt.get("candidateReplayCommand"),
            }
        )
    manifest = {
        "schema": "reconkit.verified-source-candidates.v1",
        "status": "authoritative"
        if all(candidate["byteIdentical"] is True for candidate in candidates)
        else "incomplete",
        "authorityClass": receipt.get("authorityClass"),
        "sourceAuthority": "original-bytes",
        "accuracyClass": "byte-exact",
        "semanticDecompilation": False,
        "original": {
            "path": "original.bin",
            "sha256": receipt.get("originalSha256"),
            "size": receipt.get("originalSize"),
        },
        "candidates": candidates,
        "claimBoundary": (
            "These are verified source candidates for exact byte reproduction. "
            "They are not claimed to recover original human-authored semantics."
        ),
    }
    (out_dir / "VERIFIED_SOURCE_CANDIDATES.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def write_candidate_build_recipe(out_dir: Path, receipt: dict[str, Any]) -> dict[str, Any] | None:
    if not (receipt.get("candidateSource") or receipt.get("candidateSourceDir")):
        return None
    recipe = {
        "schema": "reconkit.candidate-build-recipe.v1",
        "status": "authoritative" if receipt.get("candidateSourceByteIdentical") is True else "incomplete",
        "candidatePath": receipt.get("candidateSourcePath"),
        "verificationMode": receipt.get("candidateVerificationMode"),
        "replayCommand": receipt.get("candidateReplayCommand"),
        "expectedOutput": {
            "path": "verify-candidate-source.bin",
            "sha256": receipt.get("originalSha256"),
            "size": receipt.get("originalSize"),
        },
        "observedGenerationOutput": {
            "sha256": receipt.get("candidateOutputSha256"),
        },
        "sourceSha256": receipt.get("candidateSourceSha256"),
        "sourceTree": receipt.get("candidateSourceTree"),
        "claimBoundary": (
            "This recipe proves that the packaged supplied candidate produces the target bytes under the recorded "
            "replay command. It does not prove original authorship or semantic equivalence beyond the byte output."
        ),
    }
    (out_dir / "CANDIDATE_BUILD_RECIPE.json").write_text(json.dumps(recipe, indent=2, sort_keys=True) + "\n")
    return recipe


def write_candidate_replay_script(out_dir: Path, receipt: dict[str, Any]) -> None:
    replay_command = receipt.get("candidateReplayCommand")
    if not replay_command:
        return
    script = "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            "cd \"$(dirname \"${BASH_SOURCE[0]}\")\"",
            "export CCACHE_DISABLE=1",
            "export CCACHE_DIR=\"$PWD/.ccache\"",
            f"EXPECTED_OUTPUT_SHA={shell_single_quote(receipt.get('originalSha256'))}",
            f"REPLAY_COMMAND={shell_single_quote(replay_command)}",
            "",
            "sha_file() { sha256sum \"$1\" | awk '{print $1}'; }",
            "require_hash() {",
            "  local path=\"$1\"",
            "  local expected=\"$2\"",
            "  local actual",
            "  actual=\"$(sha_file \"$path\")\"",
            "  if [[ \"$actual\" != \"$expected\" ]]; then",
            "    echo \"hash mismatch: $path\" >&2",
            "    echo \"  expected: $expected\" >&2",
            "    echo \"  actual:   $actual\" >&2",
            "    exit 1",
            "  fi",
            "}",
            "",
            "test -f CANDIDATE_BUILD_RECIPE.json",
            "bash -c \"$REPLAY_COMMAND\"",
            "require_hash verify-candidate-source.bin \"$EXPECTED_OUTPUT_SHA\"",
            "echo \"ONE_SHOT_SOURCE_CANDIDATE_OK\"",
            "",
        ]
    )
    path = out_dir / "REPLAY_CANDIDATE.sh"
    path.write_text(script)
    path.chmod(0o755)


def write_proof_commands_script(out_dir: Path, receipt: dict[str, Any]) -> None:
    metadata = {
        "schema": "reconkit.one-shot-source-proof-commands.v1",
        "status": receipt.get("status"),
        "authorityClass": receipt.get("authorityClass"),
        "accuracyClass": receipt.get("accuracyClass"),
        "entrypoints": {
            "packageLocal": ["python3 VERIFY.py", "./VERIFY.sh", "make verify"],
            "strictPackageValidation": [
                "./scripts/decomp-cli.sh one-shot-source-validate --package <package> --require-complete"
            ],
            "sourceArchiveValidation": [
                "./scripts/decomp-cli.sh one-shot-source-validate --archive <package>.tar.gz --require-complete"
            ],
            "portableBundleReplay": [
                "./scripts/decomp-cli.sh one-shot-source-deliverable-verify --bundle <package>.deliverable.tar.gz --markdown"
            ],
            "byteAccurateResponseProof": [
                "python3 PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py"
            ],
            "responseJsonPreflight": [
                "python3 VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json"
            ],
            "responseJsonImport": [
                "python3 IMPORT_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json"
            ],
            "responseJsonPreflightWithBuildCommand": [
                "python3 VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json --allow-build-command"
            ],
            "responseJsonImportWithBuildCommand": [
                "python3 IMPORT_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json --allow-build-command"
            ],
            "helper": ["RECONKIT_WORKSPACE=/path/to/ReconstructKit ./PROOF_COMMANDS.sh"],
        },
        "artifactLayers": ["package-directory", "source-archive", "deliverable-bundle"],
        "prerequisites": {
            "packageLocal": ["python3", "gcc", "objcopy"],
            "workspaceReplay": ["RECONKIT_WORKSPACE", "scripts/decomp-cli.sh"],
            "optionalOverrides": ["RECONKIT_ARCHIVE_PATH", "RECONKIT_BUNDLE_PATH"],
        },
        "expectedSuccess": {
            "packageLocal": "ONE_SHOT_SOURCE_PACKAGE_OK",
            "strictPackageValidation": "Status: `valid`",
            "sourceArchiveValidation": "Status: `valid`",
            "portableBundleReplay": "Status: `matched`",
            "byteAccurateResponseProof": "BYTE_ACCURATE_RECONSTRUCTION_RESPONSE_PROOF_OK",
            "responseJsonPreflight": "status valid, partial, or valid-with-extra depending on supplied flags",
            "responseJsonImport": "candidate replay matched, partial, failed, or no-candidates depending on supplied response",
            "responseJsonPreflightWithBuildCommand": "same as responseJsonPreflight; permits candidates[].build.command",
            "responseJsonImportWithBuildCommand": "same as responseJsonImport; permits candidates[].build.command",
            "bundleDeliverablePhase": "Deliverable phase: `pre-bundle-index`",
        },
        "semanticDecompilation": receipt.get("semanticDecompilation"),
        "claimBoundary": receipt.get("claimBoundary"),
    }
    (out_dir / "PROOF_COMMANDS.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    script = "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            "PACKAGE_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"",
            "ARCHIVE_PATH=\"${RECONKIT_ARCHIVE_PATH:-${PACKAGE_DIR}.tar.gz}\"",
            "BUNDLE_PATH=\"${RECONKIT_BUNDLE_PATH:-${PACKAGE_DIR}.deliverable.tar.gz}\"",
            "",
            "cd \"$PACKAGE_DIR\"",
            "echo \"# Package-local verification\"",
            "python3 VERIFY.py",
            "",
            "if [[ -n \"${RECONKIT_WORKSPACE:-}\" ]]; then",
            "  echo",
            "  echo \"# Strict package-side complete validation\"",
            "  \"$RECONKIT_WORKSPACE/scripts/decomp-cli.sh\" one-shot-source-validate --package \"$PACKAGE_DIR\" --require-complete --markdown",
            "  if [[ -f \"$ARCHIVE_PATH\" ]]; then",
            "    echo",
            "    echo \"# Source archive validation\"",
            "    echo \"Source archives validate the source package; complete-mode receipts are proven by the portable bundle.\"",
            "    \"$RECONKIT_WORKSPACE/scripts/decomp-cli.sh\" one-shot-source-validate --archive \"$ARCHIVE_PATH\" --require-complete --markdown",
            "  fi",
            "  if [[ -f \"$BUNDLE_PATH\" ]]; then",
            "    echo",
            "    echo \"# Portable bundle replay\"",
            "    echo \"The bundled deliverable is expected to report deliverablePhase=pre-bundle-index; package-side strict validation above checks the final-package-index receipt.\"",
            "    \"$RECONKIT_WORKSPACE/scripts/decomp-cli.sh\" one-shot-source-deliverable-verify --bundle \"$BUNDLE_PATH\" --markdown",
            "  fi",
            "  echo",
            "  echo \"# Byte-accurate one-shot response proof\"",
            "  python3 PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py",
            "else",
            "  echo \"Set RECONKIT_WORKSPACE to run strict validation and bundle replay.\"",
            "fi",
            "",
        ]
    )
    path = out_dir / "PROOF_COMMANDS.sh"
    path.write_text(script)
    path.chmod(0o755)


def write_package_proof(out_dir: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    claims = json.loads((out_dir / "CLAIMS.json").read_text()) if (out_dir / "CLAIMS.json").exists() else {}
    content = json.loads((out_dir / "CONTENT_MANIFEST.json").read_text()) if (out_dir / "CONTENT_MANIFEST.json").exists() else {}
    gates = json.loads((out_dir / "AUTHORITY_GATES.json").read_text()) if (out_dir / "AUTHORITY_GATES.json").exists() else {}
    candidates = (
        json.loads((out_dir / "VERIFIED_SOURCE_CANDIDATES.json").read_text())
        if (out_dir / "VERIFIED_SOURCE_CANDIDATES.json").exists()
        else {}
    )
    source_roles = json.loads((out_dir / "SOURCE_ROLES.json").read_text()) if (out_dir / "SOURCE_ROLES.json").exists() else {}
    binary_evidence = json.loads((out_dir / "BINARY_EVIDENCE.json").read_text()) if (out_dir / "BINARY_EVIDENCE.json").exists() else {}
    boundary_candidates = (
        json.loads((out_dir / "FUNCTION_BOUNDARY_CANDIDATES.json").read_text())
        if (out_dir / "FUNCTION_BOUNDARY_CANDIDATES.json").exists()
        else {}
    )
    function_byte_slices = (
        json.loads((out_dir / "FUNCTION_BYTE_SLICES.json").read_text())
        if (out_dir / "FUNCTION_BYTE_SLICES.json").exists()
        else {}
    )
    function_slice_sources = (
        json.loads((out_dir / "FUNCTION_SLICE_SOURCES.json").read_text())
        if (out_dir / "FUNCTION_SLICE_SOURCES.json").exists()
        else {}
    )
    reconstruction_tasks = (
        json.loads((out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").read_text())
        if (out_dir / "FUNCTION_RECONSTRUCTION_TASKS.json").exists()
        else {}
    )
    reconstruction_candidate_results = (
        json.loads((out_dir / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json").read_text())
        if (out_dir / "FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json").exists()
        else {}
    )
    one_shot_request = {
        "path": "ONE_SHOT_RECONSTRUCTION_REQUEST.md",
        "sha256": sha256_file(out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.md")
        if (out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.md").exists()
        else None,
        "taskCount": reconstruction_tasks.get("taskCount"),
        "semanticDecompilation": False,
    }
    one_shot_request_json = {
        "path": "ONE_SHOT_RECONSTRUCTION_REQUEST.json",
        "sha256": sha256_file(out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.json")
        if (out_dir / "ONE_SHOT_RECONSTRUCTION_REQUEST.json").exists()
        else None,
        "taskCount": reconstruction_tasks.get("taskCount"),
        "semanticDecompilation": False,
    }
    one_shot_request_bundle = {
        "path": "ONE_SHOT_RECONSTRUCTION_BUNDLE.json",
        "sha256": sha256_file(out_dir / "ONE_SHOT_RECONSTRUCTION_BUNDLE.json")
        if (out_dir / "ONE_SHOT_RECONSTRUCTION_BUNDLE.json").exists()
        else None,
        "taskCount": reconstruction_tasks.get("taskCount"),
        "semanticDecompilation": False,
    }
    one_shot_importer = {
        "path": "IMPORT_RECONSTRUCTION_CANDIDATES.py",
        "sha256": sha256_file(out_dir / "IMPORT_RECONSTRUCTION_CANDIDATES.py")
        if (out_dir / "IMPORT_RECONSTRUCTION_CANDIDATES.py").exists()
        else None,
        "semanticDecompilation": False,
    }
    one_shot_json_importer = {
        "path": "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py",
        "sha256": sha256_file(out_dir / "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py")
        if (out_dir / "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py").exists()
        else None,
        "semanticDecompilation": False,
    }
    one_shot_json_validator = {
        "path": "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py",
        "sha256": sha256_file(out_dir / "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py")
        if (out_dir / "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py").exists()
        else None,
        "semanticDecompilation": False,
    }
    one_shot_receipt_refresher = {
        "path": "REFRESH_RECONSTRUCTION_RECEIPTS.py",
        "sha256": sha256_file(out_dir / "REFRESH_RECONSTRUCTION_RECEIPTS.py")
        if (out_dir / "REFRESH_RECONSTRUCTION_RECEIPTS.py").exists()
        else None,
        "semanticDecompilation": False,
        "claimBoundary": (
            "Refreshes package-local receipts after candidate import. It does not rebuild source archives "
            "or portable deliverable bundles."
        ),
    }
    response_template = (
        json.loads((out_dir / "RECONSTRUCTION_RESPONSE_TEMPLATE.json").read_text())
        if (out_dir / "RECONSTRUCTION_RESPONSE_TEMPLATE.json").exists()
        else {}
    )
    response_template_exporter = {
        "path": "EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py",
        "sha256": sha256_file(out_dir / "EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py")
        if (out_dir / "EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py").exists()
        else None,
        "semanticDecompilation": False,
    }
    byte_accurate_response_exporter = {
        "path": "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py",
        "sha256": sha256_file(out_dir / "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py")
        if (out_dir / "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py").exists()
        else None,
        "semanticDecompilation": False,
        "claimBoundary": "Exports byte-accurate .text candidate source from embedded target bytes; not semantic decompilation.",
    }
    byte_accurate_response_prover = {
        "path": "PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py",
        "sha256": sha256_file(out_dir / "PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py")
        if (out_dir / "PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py").exists()
        else None,
        "semanticDecompilation": False,
        "claimBoundary": "Proves byte-accurate one-shot response export/import/replay in a temporary package copy.",
    }
    semantic_readiness = (
        json.loads((out_dir / "SEMANTIC_READINESS.json").read_text())
        if (out_dir / "SEMANTIC_READINESS.json").exists()
        else {}
    )
    semantic_authority_evaluation = (
        json.loads((out_dir / "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json").read_text())
        if (out_dir / "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json").exists()
        else {}
    )
    semantic_authority_evaluator = {
        "path": "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py",
        "sha256": sha256_file(out_dir / "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py")
        if (out_dir / "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py").exists()
        else None,
        "semanticDecompilation": False,
    }
    recipe = (
        json.loads((out_dir / "CANDIDATE_BUILD_RECIPE.json").read_text())
        if (out_dir / "CANDIDATE_BUILD_RECIPE.json").exists()
        else None
    )
    toolchain = (
        json.loads((out_dir / "TOOLCHAIN_PROVENANCE.json").read_text())
        if (out_dir / "TOOLCHAIN_PROVENANCE.json").exists()
        else {}
    )
    authority_summary = (
        json.loads((out_dir / "AUTHORITY_SUMMARY.json").read_text())
        if (out_dir / "AUTHORITY_SUMMARY.json").exists()
        else None
    )
    proof = {
        "schema": "reconkit.one-shot-source-package-proof.v1",
        "status": receipt.get("status"),
        "contentIdentity": content.get("contentIdentity") or receipt.get("contentIdentity"),
        "authorityClass": receipt.get("authorityClass"),
        "accuracyClass": receipt.get("accuracyClass"),
        "authoritySummary": authority_summary,
        "authoritySummarySha256": sha256_file(out_dir / "AUTHORITY_SUMMARY.json")
        if (out_dir / "AUTHORITY_SUMMARY.json").exists()
        else None,
        "original": {
            "path": "original.bin",
            "sha256": receipt.get("originalSha256"),
            "size": receipt.get("originalSize"),
        },
        "proven": claims.get("proven"),
        "notProven": claims.get("notProven"),
        "binaryEvidence": binary_evidence,
        "functionBoundaryCandidates": boundary_candidates,
        "functionByteSlices": function_byte_slices,
        "functionSliceSources": function_slice_sources,
        "functionReconstructionTasks": reconstruction_tasks,
        "functionReconstructionCandidateResults": reconstruction_candidate_results,
        "oneShotReconstructionRequest": one_shot_request,
        "oneShotReconstructionRequestJson": one_shot_request_json,
        "oneShotReconstructionBundle": one_shot_request_bundle,
        "oneShotCandidateImporter": one_shot_importer,
        "oneShotResponseJsonImporter": one_shot_json_importer,
        "oneShotResponseJsonValidator": one_shot_json_validator,
        "oneShotReceiptRefresher": one_shot_receipt_refresher,
        "oneShotResponseTemplate": response_template,
        "oneShotResponseTemplateExporter": response_template_exporter,
        "oneShotByteAccurateResponseExporter": byte_accurate_response_exporter,
        "oneShotByteAccurateResponseProver": byte_accurate_response_prover,
        "semanticSourceBundlesVerified": receipt.get("semanticSourceBundlesVerified"),
        "sourceCandidates": candidates.get("candidates"),
        "sourceRoles": source_roles.get("roles"),
        "semanticReadiness": semantic_readiness,
        "semanticAuthorityEvaluation": semantic_authority_evaluation,
        "semanticAuthorityEvaluator": semantic_authority_evaluator,
        "authorityGateStatus": gates.get("status"),
        "authorityGates": gates.get("gates"),
        "toolchainProvenance": {
            "status": toolchain.get("status"),
            "tools": toolchain.get("tools"),
            "replayEnvironment": toolchain.get("replayEnvironment"),
        }
        if toolchain
        else None,
        "candidateBuildRecipe": {
            "status": recipe.get("status"),
            "candidatePath": recipe.get("candidatePath"),
            "verificationMode": recipe.get("verificationMode"),
            "expectedOutput": recipe.get("expectedOutput"),
        }
        if isinstance(recipe, dict)
        else None,
        "replayEntrypoints": {
            "fullPackage": ["make verify", "./VERIFY.sh", "python3 VERIFY.py"],
            "suppliedCandidate": ["make candidate", "./REPLAY_CANDIDATE.sh"]
            if receipt.get("candidateReplayCommand")
            else [],
        },
        "claimBoundary": receipt.get("claimBoundary"),
    }
    (out_dir / "PACKAGE_PROOF.json").write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")
    return proof


def write_toolchain_provenance(out_dir: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    provenance = {
        "schema": "reconkit.toolchain-provenance.v1",
        "status": "recorded",
        "platform": {
            "python": sys.version.split()[0],
            "pythonExecutable": sys.executable,
            "osName": os.name,
            "platform": sys.platform,
        },
        "tools": {
            "gcc": tool_info("gcc"),
            "objcopy": tool_info("objcopy"),
            "sha256sum": tool_info("sha256sum"),
            "make": tool_info("make"),
            "tar": tool_info("tar"),
        },
        "replayEnvironment": {
            "CCACHE_DISABLE": "1",
            "CCACHE_DIR": ".ccache",
            "RECONKIT_VERIFY_TIMEOUT": "optional seconds override for package-local VERIFY.py",
        },
        "proofEntrypoints": {
            "fullPackage": ["make verify", "./VERIFY.sh", "python3 VERIFY.py"],
            "suppliedCandidate": ["make candidate", "./REPLAY_CANDIDATE.sh"]
            if receipt.get("candidateReplayCommand")
            else [],
        },
        "candidateReplayCommand": receipt.get("candidateReplayCommand"),
        "claimBoundary": (
            "This records the observed verifier toolchain and replay environment. It is provenance, "
            "not a claim that another host has the same compiler binaries unless independently pinned."
        ),
    }
    (out_dir / "TOOLCHAIN_PROVENANCE.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    return provenance


def write_package_readme(out_dir: Path, receipt: dict[str, Any], claims: dict[str, Any]) -> None:
    not_proven = claims.get("notProven") if isinstance(claims.get("notProven"), list) else []
    lines = [
        "# One-Shot Source Package",
        "",
        "This package is authoritative for exact byte reproduction of the included `original.bin`.",
        "It is not a semantic decompilation or recovered human-authored source tree.",
        "",
        "## Verify",
        "",
        "```sh",
        "make verify",
        "```",
        "",
        "Equivalent direct entrypoints:",
        "",
        "```sh",
        "./VERIFY.sh",
        "python3 VERIFY.py",
        "```",
        "",
        "Replay only the supplied source candidate when present:",
        "",
        "```sh",
        "make candidate",
        "./REPLAY_CANDIDATE.sh",
        "```",
        "",
        "Remove verifier build byproducts after local rebuilds:",
        "",
        "```sh",
        "make clean",
        "```",
        "",
        "Successful verification prints:",
        "",
        "```text",
        "ONE_SHOT_SOURCE_PACKAGE_OK",
        "```",
        "",
        "## Primary files",
        "",
        "- `full-binary.S`: assembler byte-source rebuilt with `gcc` and `objcopy`.",
        "- `full-binary.c`: C byte-emitter source that writes the original bytes.",
        "- `candidate-source.c`: optional supplied C source candidate, when provided and byte-verified.",
        "- `original.bin`: included original byte payload.",
        "- `AUTHORITY_GATES.json`: machine-readable authority gate ledger.",
        "- `AUTHORITY_SUMMARY.json`: compact package-local authority contract.",
        "- `CANDIDATE_BUILD_RECIPE.json`: optional supplied-candidate rebuild recipe.",
        "- `REPLAY_CANDIDATE.sh`: optional supplied-candidate replay entrypoint.",
        "- `CLAIMS.json`: compact machine-readable proof contract.",
        "- `CONTENT_MANIFEST.json`: stable content identity for source content.",
        "- `PACKAGE_PROOF.json`: aggregate package-local proof summary.",
        "- `PROOF_COMMANDS.sh`: local proof entrypoint; set `RECONKIT_WORKSPACE` to run strict validation and bundle replay.",
        "- `PROOF_COMMANDS.json`: machine-readable proof entrypoint, prerequisite, and expected-success inventory.",
        "- `ONE_SHOT_RECONSTRUCTION_REQUEST.json`: canonical machine-readable one-shot source request.",
        "- `ONE_SHOT_RECONSTRUCTION_BUNDLE.json`: self-contained machine-readable request bundle with embedded prompt text and response contract.",
        "- `RECONSTRUCTION_RESPONSE_TEMPLATE.json`: exact one-shot response file contract.",
        "- `VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py`: preflight a one-shot JSON response without writing candidate files.",
        "- `IMPORT_RECONSTRUCTION_RESPONSE_JSON.py`: import a single JSON response containing `candidate.c` path/content pairs or structured `candidates[]` entries.",
        "- `EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py`: export a JSON response with byte-accurate task-local `.text` candidates from embedded target bytes.",
        "- `PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py`: prove the byte-accurate response export/import/replay path in a temporary package copy.",
        "- `SEMANTIC_READINESS.json`: semantic recovery gap ledger and upgrade requirements.",
        "- `SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json`: package-local semantic promotion decision.",
        "- `EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py`: replay the semantic authority promotion decision after candidates are imported.",
        "- `EXPORT_RECONSTRUCTION_RESPONSE_TEMPLATE.py`: create an empty response skeleton with expected `candidate.c` paths.",
        "- `SOURCE_INDEX.json`: source file index with authority and rebuild metadata.",
        "- `SOURCE_ROLES.json`: source role taxonomy for generated byte-source, byte-emitter, and supplied candidates.",
        "- `TOOLCHAIN_PROVENANCE.json`: observed verifier toolchain and replay environment.",
        "- `VERIFIED_SOURCE_CANDIDATES.json`: source-candidate inventory with byte-exact accuracy class.",
        "- `package-manifest.json`: tamper-evident hash manifest for package files.",
        "",
        "## Proven",
        "",
        f"- Status: `{receipt.get('status')}`",
        f"- Authority class: `{receipt.get('authorityClass')}`",
        f"- Accuracy class: `{receipt.get('accuracyClass')}`",
        f"- Content identity: `{receipt.get('contentIdentity')}`",
        f"- Original SHA256: `{receipt.get('originalSha256')}`",
        f"- Assembler source SHA256: `{receipt.get('sourceSha256')}`",
        f"- C source SHA256: `{receipt.get('cSourceSha256')}`",
        f"- Self-contained package: `{str(receipt.get('packageSelfContained')).lower()}`",
        "",
        "## Complete-mode receipt chain",
        "",
        "When generated with `--complete`, this package may include `receipts/` and a sibling `.deliverable.tar.gz` bundle.",
        "The complete receipt chain is:",
        "",
        "- `AUTHORITY_SUMMARY.json`: compact package-local authority contract.",
        "- `PACKAGE_PROOF.json`: aggregate proof with `authoritySummarySha256`.",
        "- `receipts/deliverable.json`: single index with required `authoritySummary` and `authoritySummarySha256`.",
        "- `receipts/bundle-verify.json`: post-bundle replay receipt when a bundle is produced.",
        "- `BUNDLE_MANIFEST.json`: top-level bundle manifest with member hashes, `contentIdentity`, and `authoritySummarySha256`.",
        "",
        "`VERIFY.py`, static validation, archive replay, deliverable replay, and bundle replay all enforce the same authority summary and byte-exact boundary.",
        "",
        "## One-shot JSON response replay",
        "",
        "Validate a JSON response before importing it:",
        "",
        "```sh",
        "python3 VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json",
        "```",
        "",
        "Import and replay accepted `candidate.c` files:",
        "",
        "```sh",
        "python3 IMPORT_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json",
        "```",
        "",
        "Structured `candidates[].build.command` entries are rejected by default. Use the explicit opt-in only when the response needs a task-local custom compiler/linker command that writes `$CANDIDATE_OUTPUT`:",
        "",
        "```sh",
        "python3 VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json --allow-build-command",
        "python3 IMPORT_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json --allow-build-command",
        "```",
        "",
        "The custom command is recorded in `candidate-build.env` as `CANDIDATE_BUILD_COMMAND` and remains subject to the task-local byte comparison gate.",
        "",
        "From the ReconstructKit workspace, validate the full complete-mode package with:",
        "",
        "```sh",
        "./scripts/decomp-cli.sh one-shot-source-validate --package path/to/one-shot-source --require-complete",
        "```",
        "",
        "Replay the portable bundle with:",
        "",
        "```sh",
        "./scripts/decomp-cli.sh one-shot-source-deliverable-verify --bundle path/to/one-shot-source.deliverable.tar.gz --markdown",
        "```",
        "",
        "Or run the packaged proof helper:",
        "",
        "```sh",
        "RECONKIT_WORKSPACE=/path/to/ReconstructKit ./PROOF_COMMANDS.sh",
        "```",
        "",
        "## Not proven",
        "",
    ]
    lines.extend(f"- {item}" for item in not_proven)
    lines.extend(
        [
            "",
            "For the exact machine-readable contract, read `CLAIMS.json`.",
            "",
        ]
    )
    (out_dir / "README.md").write_text("\n".join(lines))


def shell_single_quote(value: object) -> str:
    text = str(value or "")
    return "'" + text.replace("'", "'\"'\"'") + "'"


def write_standalone_verifier(out_dir: Path, receipt: dict[str, Any]) -> None:
    py_script = r'''#!/usr/bin/env python3
"""Standalone verifier for a ReconstructKit one-shot source package."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
    VERIFY_TIMEOUT = int(os.environ.get("RECONKIT_VERIFY_TIMEOUT", os.environ.get("RECONKIT_VERIFY_TIMEOUT", "60")))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise SystemExit(f"unexpected JSON root: {path}")
    return data


def expected_json_replay_report_shapes() -> dict:
    return {
        "preflight": {
            "schema": "reconkit.one-shot-source-reconstruction-json-preflight.v1",
            "buildOverrideCount": "number of response candidate paths with build overrides",
            "buildOverridePaths": ["all response candidate paths with build overrides"],
            "buildOverrideExpectedPaths": ["expected candidate paths with build overrides"],
            "buildOverrideExtraPaths": ["extra candidate paths with build overrides"],
        },
        "import": {
            "schema": "reconkit.one-shot-source-reconstruction-json-import.v1",
            "buildOverrideCount": "number of response candidate paths with build overrides, including extras",
            "buildOverridePaths": ["all response candidate paths with build overrides"],
            "buildOverrideExpectedPaths": ["importable expected candidate paths with build overrides"],
            "buildOverrideExtraPaths": ["extra candidate paths with build overrides that were not imported"],
        },
    }


def run(args: list[str], stdout_path: Path | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["CCACHE_DISABLE"] = "1"
    env["CCACHE_DIR"] = str(ROOT / ".ccache")
    try:
        if stdout_path is None:
            return subprocess.run(
                args,
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                env=env,
                timeout=VERIFY_TIMEOUT,
            )
        with stdout_path.open("wb") as fh:
            return subprocess.run(
                args,
                cwd=ROOT,
                stdout=fh,
                stderr=subprocess.PIPE,
                check=False,
                env=env,
                timeout=VERIFY_TIMEOUT,
            )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"command timed out after {VERIFY_TIMEOUT}s: {' '.join(args)}") from exc


def require_hash(path: Path, expected: str) -> None:
    actual = sha256_file(path)
    if actual != expected:
        raise SystemExit(f"hash mismatch: {path.name}\n  expected: {expected}\n  actual:   {actual}")


def verify_manifest() -> None:
    manifest = read_json(ROOT / "package-manifest.json")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise SystemExit("package-manifest.json has no files list")
    for item in files:
        if not isinstance(item, dict):
            raise SystemExit("package-manifest.json contains a non-object file row")
        rel = item.get("path")
        expected = item.get("sha256")
        if not isinstance(rel, str) or not isinstance(expected, str):
            raise SystemExit("package-manifest.json contains an incomplete file row")
        path = ROOT / rel
        if not path.exists():
            raise SystemExit(f"manifest file missing: {rel}")
        require_hash(path, expected)


def verify_content_manifest() -> None:
    manifest = read_json(ROOT / "CONTENT_MANIFEST.json")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise SystemExit("CONTENT_MANIFEST.json has no files list")
    digest = hashlib.sha256()
    for item in files:
        if not isinstance(item, dict):
            raise SystemExit("CONTENT_MANIFEST.json contains a non-object file row")
        rel = item.get("path")
        expected = item.get("sha256")
        expected_size = item.get("size")
        if not isinstance(rel, str) or not isinstance(expected, str):
            raise SystemExit("CONTENT_MANIFEST.json contains an incomplete file row")
        path = ROOT / rel
        if not path.exists():
            raise SystemExit(f"content manifest file missing: {rel}")
        actual = sha256_file(path)
        if actual != expected:
            raise SystemExit(f"content hash mismatch: {rel}")
        size = path.stat().st_size
        if isinstance(expected_size, int) and size != expected_size:
            raise SystemExit(f"content size mismatch: {rel}")
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(actual.encode("ascii"))
        digest.update(b"\n")
    if digest.hexdigest() != manifest.get("contentIdentity"):
        raise SystemExit("CONTENT_MANIFEST.json contentIdentity mismatch")


def verify_sha256sums() -> None:
    path = ROOT / "SHA256SUMS"
    if not path.exists():
        raise SystemExit("missing SHA256SUMS")
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            expected, rel = line.split(None, 1)
        except ValueError as exc:
            raise SystemExit(f"invalid SHA256SUMS line: {line}") from exc
        rel = rel.strip()
        rel_path = Path(rel)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            raise SystemExit(f"unsafe SHA256SUMS path: {rel}")
        target = ROOT / rel_path
        if not target.exists():
            raise SystemExit(f"SHA256SUMS file missing: {rel}")
        actual = sha256_file(target)
        if actual != expected:
            raise SystemExit(f"SHA256SUMS mismatch: {rel}")


def verify_claims() -> None:
    claims = read_json(ROOT / "CLAIMS.json")
    if claims.get("status") != "authoritative":
        raise SystemExit("CLAIMS.json status is not authoritative")
    proven = claims.get("proven")
    if not isinstance(proven, dict):
        raise SystemExit("CLAIMS.json has no proven object")
    required_true = [
        "selfContainedPackage",
        "originalBytesIncluded",
        "assemblerSourceRebuildsOriginalBytes",
        "cSourceEmitsOriginalBytes",
    ]
    for key in required_true:
        if proven.get(key) is not True:
            raise SystemExit(f"CLAIMS.json does not prove {key}")
    if proven.get("semanticDecompilation") is not False:
        raise SystemExit("CLAIMS.json must not claim semantic decompilation")
    content = read_json(ROOT / "CONTENT_MANIFEST.json")
    if claims.get("contentIdentity") != content.get("contentIdentity"):
        raise SystemExit("CLAIMS.json contentIdentity does not match CONTENT_MANIFEST.json")
    if claims.get("authorityClass") != "byte-authoritative-source":
        raise SystemExit("CLAIMS.json authorityClass mismatch")
    if claims.get("accuracyClass") != "byte-exact":
        raise SystemExit("CLAIMS.json accuracyClass mismatch")
    source_accuracy = claims.get("sourceAccuracy")
    if not isinstance(source_accuracy, dict):
        raise SystemExit("CLAIMS.json has no sourceAccuracy object")
    for key in ("assembler", "cByteEmitter"):
        item = source_accuracy.get(key)
        if not isinstance(item, dict) or item.get("byteIdentical") is not True:
            raise SystemExit(f"CLAIMS.json sourceAccuracy does not prove {key}")


def verify_authority_gates() -> None:
    gates = read_json(ROOT / "AUTHORITY_GATES.json")
    claims = read_json(ROOT / "CLAIMS.json")
    proven = claims.get("proven") if isinstance(claims.get("proven"), dict) else {}
    verifier_already_recorded = proven.get("packageLocalVerifierPassedAtGeneration") is True
    if gates.get("sourceAuthority") != "original-bytes":
        raise SystemExit("AUTHORITY_GATES.json sourceAuthority mismatch")
    if gates.get("accuracyClass") != "byte-exact":
        raise SystemExit("AUTHORITY_GATES.json accuracyClass mismatch")
    rows = gates.get("gates")
    if not isinstance(rows, list) or not rows:
        raise SystemExit("AUTHORITY_GATES.json has no gates")
    failed_gate_ids: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            raise SystemExit("AUTHORITY_GATES.json contains non-object gate")
        if row.get("status") != "passed":
            failed_gate_ids.append(str(row.get("id")))
    if verifier_already_recorded:
        if gates.get("status") != "passed":
            raise SystemExit("AUTHORITY_GATES.json status is not passed")
        if failed_gate_ids:
            raise SystemExit(f"authority gates failed: {', '.join(failed_gate_ids)}")
    else:
        allowed_pending = {"package-local-verifier"}
        unexpected = [gate_id for gate_id in failed_gate_ids if gate_id not in allowed_pending]
        if unexpected:
            raise SystemExit(f"authority gates failed before verifier bootstrap: {', '.join(unexpected)}")


def verify_verified_source_candidates() -> None:
    manifest = read_json(ROOT / "VERIFIED_SOURCE_CANDIDATES.json")
    if manifest.get("status") != "authoritative":
        raise SystemExit("VERIFIED_SOURCE_CANDIDATES.json status is not authoritative")
    if manifest.get("authorityClass") != "byte-authoritative-source":
        raise SystemExit("VERIFIED_SOURCE_CANDIDATES.json authorityClass mismatch")
    if manifest.get("accuracyClass") != "byte-exact":
        raise SystemExit("VERIFIED_SOURCE_CANDIDATES.json accuracyClass mismatch")
    if manifest.get("semanticDecompilation") is not False:
        raise SystemExit("VERIFIED_SOURCE_CANDIDATES.json must not claim semantic decompilation")
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list) or len(candidates) < 2:
        raise SystemExit("VERIFIED_SOURCE_CANDIDATES.json has no candidate list")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise SystemExit("VERIFIED_SOURCE_CANDIDATES.json contains non-object candidate")
        if candidate.get("byteIdentical") is not True:
            raise SystemExit(f"source candidate is not byte-identical: {candidate.get('path')}")
        if candidate.get("accuracyClass") != "byte-exact":
            raise SystemExit(f"source candidate accuracy mismatch: {candidate.get('path')}")
        if candidate.get("semanticDecompilation") is not False:
            raise SystemExit(f"source candidate overclaims semantic decompilation: {candidate.get('path')}")


def verify_source_roles() -> None:
    roles_doc = read_json(ROOT / "SOURCE_ROLES.json")
    receipt = read_json(ROOT / "one-shot-source-receipt.json")
    if roles_doc.get("schema") != "reconkit.one-shot-source-roles.v1":
        raise SystemExit("SOURCE_ROLES.json schema mismatch")
    if roles_doc.get("status") != receipt.get("status"):
        raise SystemExit("SOURCE_ROLES.json status mismatch")
    if roles_doc.get("authorityClass") != "byte-authoritative-source":
        raise SystemExit("SOURCE_ROLES.json authorityClass mismatch")
    if roles_doc.get("accuracyClass") != "byte-exact":
        raise SystemExit("SOURCE_ROLES.json accuracyClass mismatch")
    if roles_doc.get("semanticDecompilation") is not False:
        raise SystemExit("SOURCE_ROLES.json must not claim semantic decompilation")
    roles = roles_doc.get("roles")
    if not isinstance(roles, list) or len(roles) < 2:
        raise SystemExit("SOURCE_ROLES.json has no roles list")
    by_path = {role.get("path"): role for role in roles if isinstance(role, dict)}
    expected = {
        "full-binary.S": ("generated-assembler-byte-source", receipt.get("sourceSha256")),
        "full-binary.c": ("generated-c-byte-emitter", receipt.get("cSourceSha256")),
    }
    for rel, (expected_role, expected_sha) in expected.items():
        role = by_path.get(rel)
        if not isinstance(role, dict):
            raise SystemExit(f"SOURCE_ROLES.json missing role for {rel}")
        if role.get("role") != expected_role:
            raise SystemExit(f"SOURCE_ROLES.json role mismatch for {rel}")
        if role.get("accuracyClass") != "byte-exact":
            raise SystemExit(f"SOURCE_ROLES.json accuracy mismatch for {rel}")
        if role.get("semanticDecompilation") is not False:
            raise SystemExit(f"SOURCE_ROLES.json overclaims semantic decompilation for {rel}")
        if role.get("sha256") != expected_sha or sha256_file(ROOT / rel) != expected_sha:
            raise SystemExit(f"SOURCE_ROLES.json hash mismatch for {rel}")


def verify_binary_evidence() -> None:
    evidence = read_json(ROOT / "BINARY_EVIDENCE.json")
    receipt = read_json(ROOT / "one-shot-source-receipt.json")
    if evidence.get("schema") != "reconkit.one-shot-source-binary-evidence.v1":
        raise SystemExit("BINARY_EVIDENCE.json schema mismatch")
    if evidence.get("status") != "recorded":
        raise SystemExit("BINARY_EVIDENCE.json status mismatch")
    original = evidence.get("original")
    if not isinstance(original, dict):
        raise SystemExit("BINARY_EVIDENCE.json has no original object")
    if original.get("sha256") != receipt.get("originalSha256"):
        raise SystemExit("BINARY_EVIDENCE.json original hash mismatch")
    if original.get("size") != receipt.get("originalSize"):
        raise SystemExit("BINARY_EVIDENCE.json original size mismatch")
    require_hash(ROOT / "original.bin", str(original.get("sha256")))
    hints = evidence.get("functionBoundaryHints")
    if not isinstance(hints, dict):
        raise SystemExit("BINARY_EVIDENCE.json has no functionBoundaryHints object")
    if hints.get("verifiedAgainstSource") is not False:
        raise SystemExit("BINARY_EVIDENCE.json must not claim verified source boundaries")


def verify_function_boundary_candidates() -> None:
    candidates_doc = read_json(ROOT / "FUNCTION_BOUNDARY_CANDIDATES.json")
    if candidates_doc.get("schema") != "reconkit.one-shot-source-function-boundary-candidates.v1":
        raise SystemExit("FUNCTION_BOUNDARY_CANDIDATES.json schema mismatch")
    if candidates_doc.get("status") not in ("hints-present", "absent"):
        raise SystemExit("FUNCTION_BOUNDARY_CANDIDATES.json status mismatch")
    if candidates_doc.get("verifiedAgainstSource") is not False:
        raise SystemExit("FUNCTION_BOUNDARY_CANDIDATES.json must not claim verified source boundaries")
    if candidates_doc.get("binaryEvidenceSha256") != sha256_file(ROOT / "BINARY_EVIDENCE.json"):
        raise SystemExit("FUNCTION_BOUNDARY_CANDIDATES.json binaryEvidenceSha256 mismatch")
    candidates = candidates_doc.get("candidates")
    if not isinstance(candidates, list):
        raise SystemExit("FUNCTION_BOUNDARY_CANDIDATES.json has no candidates list")
    if candidates_doc.get("candidateCount") != len(candidates):
        raise SystemExit("FUNCTION_BOUNDARY_CANDIDATES.json candidateCount mismatch")
    for item in candidates:
        if not isinstance(item, dict):
            raise SystemExit("FUNCTION_BOUNDARY_CANDIDATES.json contains non-object candidate")
        if item.get("verifiedAgainstSource") is not False:
            raise SystemExit(f"function candidate overclaims source verification: {item.get('name')}")


def verify_function_byte_slices() -> None:
    slices_doc = read_json(ROOT / "FUNCTION_BYTE_SLICES.json")
    if slices_doc.get("schema") != "reconkit.one-shot-source-function-byte-slices.v1":
        raise SystemExit("FUNCTION_BYTE_SLICES.json schema mismatch")
    if slices_doc.get("status") not in ("slices-present", "absent"):
        raise SystemExit("FUNCTION_BYTE_SLICES.json status mismatch")
    if slices_doc.get("binaryEvidenceSha256") != sha256_file(ROOT / "BINARY_EVIDENCE.json"):
        raise SystemExit("FUNCTION_BYTE_SLICES.json binaryEvidenceSha256 mismatch")
    if slices_doc.get("functionBoundaryCandidatesSha256") != sha256_file(ROOT / "FUNCTION_BOUNDARY_CANDIDATES.json"):
        raise SystemExit("FUNCTION_BYTE_SLICES.json functionBoundaryCandidatesSha256 mismatch")
    if slices_doc.get("verifiedAgainstSource") is not False:
        raise SystemExit("FUNCTION_BYTE_SLICES.json must not claim source verification")
    slices = slices_doc.get("slices")
    if not isinstance(slices, list):
        raise SystemExit("FUNCTION_BYTE_SLICES.json has no slices list")
    if slices_doc.get("sliceCount") != len(slices):
        raise SystemExit("FUNCTION_BYTE_SLICES.json sliceCount mismatch")
    original = (ROOT / "original.bin").read_bytes()
    for item in slices:
        if not isinstance(item, dict):
            raise SystemExit("FUNCTION_BYTE_SLICES.json contains non-object slice")
        if item.get("verifiedAgainstSource") is not False:
            raise SystemExit(f"function byte slice overclaims source verification: {item.get('name')}")
        offset = item.get("fileOffset")
        size = item.get("size")
        if not isinstance(offset, int) or not isinstance(size, int) or offset < 0 or size <= 0:
            raise SystemExit(f"function byte slice has invalid offset/size: {item.get('name')}")
        if offset + size > len(original):
            raise SystemExit(f"function byte slice is outside original.bin: {item.get('name')}")
        if hashlib.sha256(original[offset : offset + size]).hexdigest() != item.get("sha256"):
            raise SystemExit(f"function byte slice hash mismatch: {item.get('name')}")


def verify_function_slice_sources() -> None:
    sources_doc = read_json(ROOT / "FUNCTION_SLICE_SOURCES.json")
    if sources_doc.get("schema") != "reconkit.one-shot-source-function-slice-sources.v1":
        raise SystemExit("FUNCTION_SLICE_SOURCES.json schema mismatch")
    if sources_doc.get("status") not in ("sources-present", "absent"):
        raise SystemExit("FUNCTION_SLICE_SOURCES.json status mismatch")
    if sources_doc.get("functionByteSlicesSha256") != sha256_file(ROOT / "FUNCTION_BYTE_SLICES.json"):
        raise SystemExit("FUNCTION_SLICE_SOURCES.json functionByteSlicesSha256 mismatch")
    if sources_doc.get("semanticDecompilation") is not False or sources_doc.get("verifiedAgainstSource") is not False:
        raise SystemExit("FUNCTION_SLICE_SOURCES.json overclaims semantic/source verification")
    sources = sources_doc.get("sources")
    if not isinstance(sources, list):
        raise SystemExit("FUNCTION_SLICE_SOURCES.json has no sources list")
    if sources_doc.get("sourceCount") != len(sources):
        raise SystemExit("FUNCTION_SLICE_SOURCES.json sourceCount mismatch")
    for item in sources:
        if not isinstance(item, dict):
            raise SystemExit("FUNCTION_SLICE_SOURCES.json contains non-object source")
        rel = item.get("path")
        if not isinstance(rel, str) or Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise SystemExit("FUNCTION_SLICE_SOURCES.json has unsafe source path")
        path = ROOT / rel
        if not path.exists():
            raise SystemExit(f"function slice source missing: {rel}")
        if sha256_file(path) != item.get("sourceSha256"):
            raise SystemExit(f"function slice source hash mismatch: {rel}")
        if item.get("semanticDecompilation") is not False or item.get("verifiedAgainstSource") is not False:
            raise SystemExit(f"function slice source overclaims semantic/source verification: {rel}")


def verify_function_reconstruction_tasks() -> None:
    tasks_doc = read_json(ROOT / "FUNCTION_RECONSTRUCTION_TASKS.json")
    if tasks_doc.get("schema") != "reconkit.one-shot-source-function-reconstruction-tasks.v1":
        raise SystemExit("FUNCTION_RECONSTRUCTION_TASKS.json schema mismatch")
    if tasks_doc.get("status") not in ("tasks-present", "absent"):
        raise SystemExit("FUNCTION_RECONSTRUCTION_TASKS.json status mismatch")
    if tasks_doc.get("functionByteSlicesSha256") != sha256_file(ROOT / "FUNCTION_BYTE_SLICES.json"):
        raise SystemExit("FUNCTION_RECONSTRUCTION_TASKS.json functionByteSlicesSha256 mismatch")
    if tasks_doc.get("functionSliceSourcesSha256") != sha256_file(ROOT / "FUNCTION_SLICE_SOURCES.json"):
        raise SystemExit("FUNCTION_RECONSTRUCTION_TASKS.json functionSliceSourcesSha256 mismatch")
    if tasks_doc.get("semanticDecompilation") is not False or tasks_doc.get("verifiedAgainstSource") is not False:
        raise SystemExit("FUNCTION_RECONSTRUCTION_TASKS.json overclaims semantic/source verification")
    tasks = tasks_doc.get("tasks")
    if not isinstance(tasks, list):
        raise SystemExit("FUNCTION_RECONSTRUCTION_TASKS.json has no tasks list")
    if tasks_doc.get("taskCount") != len(tasks):
        raise SystemExit("FUNCTION_RECONSTRUCTION_TASKS.json taskCount mismatch")
    for item in tasks:
        if not isinstance(item, dict):
            raise SystemExit("FUNCTION_RECONSTRUCTION_TASKS.json contains non-object task")
        if item.get("semanticDecompilation") is not False or item.get("verifiedAgainstSource") is not False:
            raise SystemExit(f"function reconstruction task overclaims semantic/source verification: {item.get('name')}")
        for key, hash_key in (
            ("taskJson", "taskJsonSha256"),
            ("readme", "readmeSha256"),
            ("candidateVerifier", "candidateVerifierSha256"),
            ("oneShotPrompt", "oneShotPromptSha256"),
            ("targetBytes", "targetBytesSha256"),
        ):
            rel = item.get(key)
            if not isinstance(rel, str) or Path(rel).is_absolute() or ".." in Path(rel).parts:
                raise SystemExit(f"FUNCTION_RECONSTRUCTION_TASKS.json has unsafe {key} path")
            path = ROOT / rel
            if not path.exists():
                raise SystemExit(f"function reconstruction task file missing: {rel}")
            if sha256_file(path) != item.get(hash_key):
                raise SystemExit(f"function reconstruction task hash mismatch: {rel}")


def verify_one_shot_response_contract() -> None:
    template = read_json(ROOT / "RECONSTRUCTION_RESPONSE_TEMPLATE.json")
    request = read_json(ROOT / "ONE_SHOT_RECONSTRUCTION_REQUEST.json")
    bundle = read_json(ROOT / "ONE_SHOT_RECONSTRUCTION_BUNDLE.json")
    tasks = read_json(ROOT / "FUNCTION_RECONSTRUCTION_TASKS.json")
    task_rows = tasks.get("tasks")
    if template.get("schema") != "reconkit.one-shot-source-reconstruction-response-template.v1":
        raise SystemExit("RECONSTRUCTION_RESPONSE_TEMPLATE.json schema mismatch")
    if template.get("status") != "empty-template":
        raise SystemExit("RECONSTRUCTION_RESPONSE_TEMPLATE.json status mismatch")
    if template.get("semanticDecompilation") is not False:
        raise SystemExit("RECONSTRUCTION_RESPONSE_TEMPLATE.json must not claim semantic decompilation")
    expected_candidates = template.get("expectedCandidates")
    if not isinstance(expected_candidates, list) or not isinstance(task_rows, list):
        raise SystemExit("RECONSTRUCTION_RESPONSE_TEMPLATE.json has no expectedCandidates list")
    expected_paths = [f"{task.get('path')}/candidate.c" for task in task_rows if isinstance(task, dict)]
    template_paths = [row.get("path") for row in expected_candidates if isinstance(row, dict)]
    if template_paths != expected_paths:
        raise SystemExit("RECONSTRUCTION_RESPONSE_TEMPLATE.json expected candidate paths mismatch")
    response_shape = template.get("jsonResponseShape")
    if not isinstance(response_shape, dict) or response_shape.get("schema") != "reconkit.one-shot-source-reconstruction-response.v1":
        raise SystemExit("RECONSTRUCTION_RESPONSE_TEMPLATE.json JSON response shape mismatch")
    if "candidates" in response_shape or not isinstance(response_shape.get("files"), dict):
        raise SystemExit("RECONSTRUCTION_RESPONSE_TEMPLATE.json JSON response shape must use files only")
    structured_shape = template.get("jsonStructuredResponseShape")
    if not isinstance(structured_shape, dict) or structured_shape.get("schema") != "reconkit.one-shot-source-reconstruction-response.v1":
        raise SystemExit("RECONSTRUCTION_RESPONSE_TEMPLATE.json structured JSON response shape mismatch")
    structured_candidates = structured_shape.get("candidates")
    if not isinstance(structured_candidates, list) or not structured_candidates:
        raise SystemExit("RECONSTRUCTION_RESPONSE_TEMPLATE.json structured JSON response shape has no candidates")
    build = structured_candidates[0].get("build") if isinstance(structured_candidates[0], dict) else None
    if not isinstance(build, dict) or build.get("command") != "optional custom command that writes $CANDIDATE_OUTPUT; requires --allow-build-command":
        raise SystemExit("RECONSTRUCTION_RESPONSE_TEMPLATE.json structured JSON response build command mismatch")
    if template.get("jsonReplayReportShapes") != expected_json_replay_report_shapes():
        raise SystemExit("RECONSTRUCTION_RESPONSE_TEMPLATE.json JSON replay report shapes mismatch")
    if request.get("schema") != "reconkit.one-shot-source-reconstruction-request.v1":
        raise SystemExit("ONE_SHOT_RECONSTRUCTION_REQUEST.json schema mismatch")
    preferred = request.get("preferredResponse")
    if not isinstance(preferred, dict) or preferred.get("schema") != "reconkit.one-shot-source-reconstruction-response.v1":
        raise SystemExit("ONE_SHOT_RECONSTRUCTION_REQUEST.json preferred response mismatch")
    if preferred.get("structuredShape") != structured_shape:
        raise SystemExit("ONE_SHOT_RECONSTRUCTION_REQUEST.json structured preferred response mismatch")
    if preferred.get("replayReportShapes") != expected_json_replay_report_shapes():
        raise SystemExit("ONE_SHOT_RECONSTRUCTION_REQUEST.json replay report shapes mismatch")
    bundle_template = bundle.get("responseTemplate")
    if not isinstance(bundle_template, dict):
        raise SystemExit("ONE_SHOT_RECONSTRUCTION_BUNDLE.json has no responseTemplate object")
    if bundle_template.get("sha256") != sha256_file(ROOT / "RECONSTRUCTION_RESPONSE_TEMPLATE.json"):
        raise SystemExit("ONE_SHOT_RECONSTRUCTION_BUNDLE.json response template hash mismatch")
    if bundle_template.get("jsonStructuredResponseShape") != structured_shape:
        raise SystemExit("ONE_SHOT_RECONSTRUCTION_BUNDLE.json structured response template mismatch")
    if bundle_template.get("jsonReplayReportShapes") != expected_json_replay_report_shapes():
        raise SystemExit("ONE_SHOT_RECONSTRUCTION_BUNDLE.json response template replay report shapes mismatch")


def verify_semantic_readiness() -> None:
    readiness = read_json(ROOT / "SEMANTIC_READINESS.json")
    claims = read_json(ROOT / "CLAIMS.json")
    roles = read_json(ROOT / "SOURCE_ROLES.json")
    binary_evidence = read_json(ROOT / "BINARY_EVIDENCE.json")
    boundary_candidates = read_json(ROOT / "FUNCTION_BOUNDARY_CANDIDATES.json")
    function_byte_slices = read_json(ROOT / "FUNCTION_BYTE_SLICES.json")
    function_slice_sources = read_json(ROOT / "FUNCTION_SLICE_SOURCES.json")
    reconstruction_tasks = read_json(ROOT / "FUNCTION_RECONSTRUCTION_TASKS.json")
    receipt = read_json(ROOT / "one-shot-source-receipt.json")
    if readiness.get("schema") != "reconkit.one-shot-source-semantic-readiness.v1":
        raise SystemExit("SEMANTIC_READINESS.json schema mismatch")
    expected_status = "ready" if int(receipt.get("semanticSourceBundlesVerified") or 0) > 0 else "not-ready"
    if readiness.get("status") != expected_status:
        raise SystemExit("SEMANTIC_READINESS.json status mismatch")
    if readiness.get("authorityClass") != claims.get("authorityClass"):
        raise SystemExit("SEMANTIC_READINESS.json authorityClass mismatch")
    if readiness.get("accuracyClass") != claims.get("accuracyClass"):
        raise SystemExit("SEMANTIC_READINESS.json accuracyClass mismatch")
    if readiness.get("currentClaim") != "byte-exact-reproduction":
        raise SystemExit("SEMANTIC_READINESS.json currentClaim mismatch")
    if readiness.get("targetClaim") != "semantic-source-recovery":
        raise SystemExit("SEMANTIC_READINESS.json targetClaim mismatch")
    if readiness.get("semanticDecompilation") is not False:
        raise SystemExit("SEMANTIC_READINESS.json must not claim semantic decompilation")
    if readiness.get("binaryEvidenceStatus") != binary_evidence.get("status"):
        raise SystemExit("SEMANTIC_READINESS.json binaryEvidenceStatus mismatch")
    if readiness.get("functionBoundaryCandidateStatus") != boundary_candidates.get("status"):
        raise SystemExit("SEMANTIC_READINESS.json functionBoundaryCandidateStatus mismatch")
    if readiness.get("functionBoundaryCandidateCount") != boundary_candidates.get("candidateCount"):
        raise SystemExit("SEMANTIC_READINESS.json functionBoundaryCandidateCount mismatch")
    if readiness.get("functionByteSliceStatus") != function_byte_slices.get("status"):
        raise SystemExit("SEMANTIC_READINESS.json functionByteSliceStatus mismatch")
    if readiness.get("functionByteSliceCount") != function_byte_slices.get("sliceCount"):
        raise SystemExit("SEMANTIC_READINESS.json functionByteSliceCount mismatch")
    if readiness.get("functionSliceSourceStatus") != function_slice_sources.get("status"):
        raise SystemExit("SEMANTIC_READINESS.json functionSliceSourceStatus mismatch")
    if readiness.get("functionSliceSourceCount") != function_slice_sources.get("sourceCount"):
        raise SystemExit("SEMANTIC_READINESS.json functionSliceSourceCount mismatch")
    if readiness.get("functionReconstructionTaskStatus") != reconstruction_tasks.get("status"):
        raise SystemExit("SEMANTIC_READINESS.json functionReconstructionTaskStatus mismatch")
    if readiness.get("functionReconstructionTaskCount") != reconstruction_tasks.get("taskCount"):
        raise SystemExit("SEMANTIC_READINESS.json functionReconstructionTaskCount mismatch")
    if readiness.get("sourceRoles") != roles.get("roles"):
        raise SystemExit("SEMANTIC_READINESS.json sourceRoles mismatch")
    missing = readiness.get("missingForSemanticAuthority")
    if expected_status == "not-ready" and (not isinstance(missing, list) or not missing):
        raise SystemExit("SEMANTIC_READINESS.json has no semantic blockers")


def verify_authority_summary() -> None:
    summary = read_json(ROOT / "AUTHORITY_SUMMARY.json")
    claims = read_json(ROOT / "CLAIMS.json")
    content = read_json(ROOT / "CONTENT_MANIFEST.json")
    gates = read_json(ROOT / "AUTHORITY_GATES.json")
    candidates = read_json(ROOT / "VERIFIED_SOURCE_CANDIDATES.json")
    proof = read_json(ROOT / "PACKAGE_PROOF.json")
    expected = {
        "schema": "reconkit.one-shot-source-authority-summary.v1",
        "status": claims.get("status"),
        "authorityClass": claims.get("authorityClass"),
        "accuracyClass": claims.get("accuracyClass"),
        "authorityContractStatus": "passed" if gates.get("status") == "passed" else "failed",
        "authorityGateStatus": gates.get("status"),
        "sourceCandidateStatus": candidates.get("status"),
        "packageProofStatus": proof.get("status"),
        "contentIdentity": content.get("contentIdentity"),
        "semanticDecompilation": False,
        "claimBoundary": proof.get("claimBoundary"),
    }
    for key, value in expected.items():
        if summary.get(key) != value:
            raise SystemExit(f"AUTHORITY_SUMMARY.json mismatch for {key}")


def verify_proof_commands() -> None:
    metadata = read_json(ROOT / "PROOF_COMMANDS.json")
    claims = read_json(ROOT / "CLAIMS.json")
    if metadata.get("schema") != "reconkit.one-shot-source-proof-commands.v1":
        raise SystemExit("PROOF_COMMANDS.json schema mismatch")
    if metadata.get("status") != claims.get("status"):
        raise SystemExit("PROOF_COMMANDS.json status mismatch")
    if metadata.get("authorityClass") != claims.get("authorityClass"):
        raise SystemExit("PROOF_COMMANDS.json authorityClass mismatch")
    if metadata.get("accuracyClass") != claims.get("accuracyClass"):
        raise SystemExit("PROOF_COMMANDS.json accuracyClass mismatch")
    if metadata.get("semanticDecompilation") is not False:
        raise SystemExit("PROOF_COMMANDS.json must not claim semantic decompilation")
    if metadata.get("artifactLayers") != ["package-directory", "source-archive", "deliverable-bundle"]:
        raise SystemExit("PROOF_COMMANDS.json artifactLayers mismatch")
    prerequisites = metadata.get("prerequisites")
    expected_prerequisites = {
        "packageLocal": ["python3", "gcc", "objcopy"],
        "workspaceReplay": ["RECONKIT_WORKSPACE", "scripts/decomp-cli.sh"],
        "optionalOverrides": ["RECONKIT_ARCHIVE_PATH", "RECONKIT_BUNDLE_PATH"],
    }
    if prerequisites != expected_prerequisites:
        raise SystemExit("PROOF_COMMANDS.json prerequisites mismatch")
    entrypoints = metadata.get("entrypoints")
    if not isinstance(entrypoints, dict):
        raise SystemExit("PROOF_COMMANDS.json has no entrypoints object")
    for key in (
        "packageLocal",
        "strictPackageValidation",
        "sourceArchiveValidation",
        "portableBundleReplay",
        "byteAccurateResponseProof",
        "responseJsonPreflight",
        "responseJsonImport",
        "responseJsonPreflightWithBuildCommand",
        "responseJsonImportWithBuildCommand",
        "helper",
    ):
        value = entrypoints.get(key)
        if not isinstance(value, list) or not value:
            raise SystemExit(f"PROOF_COMMANDS.json missing entrypoint group: {key}")
    expected_success = {
        "packageLocal": "ONE_SHOT_SOURCE_PACKAGE_OK",
        "strictPackageValidation": "Status: `valid`",
        "sourceArchiveValidation": "Status: `valid`",
        "portableBundleReplay": "Status: `matched`",
        "byteAccurateResponseProof": "BYTE_ACCURATE_RECONSTRUCTION_RESPONSE_PROOF_OK",
        "responseJsonPreflight": "status valid, partial, or valid-with-extra depending on supplied flags",
        "responseJsonImport": "candidate replay matched, partial, failed, or no-candidates depending on supplied response",
        "responseJsonPreflightWithBuildCommand": "same as responseJsonPreflight; permits candidates[].build.command",
        "responseJsonImportWithBuildCommand": "same as responseJsonImport; permits candidates[].build.command",
        "bundleDeliverablePhase": "Deliverable phase: `pre-bundle-index`",
    }
    if metadata.get("expectedSuccess") != expected_success:
        raise SystemExit("PROOF_COMMANDS.json expectedSuccess mismatch")


def verify_source_index() -> None:
    index = read_json(ROOT / "SOURCE_INDEX.json")
    receipt = read_json(ROOT / "one-shot-source-receipt.json")
    if index.get("status") != "authoritative":
        raise SystemExit("SOURCE_INDEX.json status is not authoritative")
    original = index.get("original")
    if not isinstance(original, dict) or original.get("sha256") != receipt.get("originalSha256"):
        raise SystemExit("SOURCE_INDEX.json original does not match receipt")
    sources = index.get("sources")
    if not isinstance(sources, list) or len(sources) < 2:
        raise SystemExit("SOURCE_INDEX.json has no source list")
    by_path = {item.get("path"): item for item in sources if isinstance(item, dict)}
    expected = {
        "full-binary.S": receipt.get("sourceSha256"),
        "full-binary.c": receipt.get("cSourceSha256"),
    }
    for rel, expected_sha in expected.items():
        item = by_path.get(rel)
        if not isinstance(item, dict):
            raise SystemExit(f"SOURCE_INDEX.json missing {rel}")
        if item.get("sha256") != expected_sha:
            raise SystemExit(f"SOURCE_INDEX.json hash mismatch for {rel}")
        if item.get("semanticDecompilation") is not False:
            raise SystemExit(f"SOURCE_INDEX.json must not claim semantic decompilation for {rel}")
        if sha256_file(ROOT / rel) != expected_sha:
            raise SystemExit(f"SOURCE_INDEX.json file hash mismatch for {rel}")


def main() -> int:
    receipt = read_json(ROOT / "one-shot-source-receipt.json")
    expected_binary = str(receipt.get("originalSha256") or "")
    expected_asm_source = str(receipt.get("sourceSha256") or "")
    expected_c_source = str(receipt.get("cSourceSha256") or "")
    if not expected_binary or not expected_asm_source or not expected_c_source:
        raise SystemExit("receipt is missing expected hashes")

    verify_manifest()
    verify_sha256sums()
    verify_content_manifest()
    verify_claims()
    verify_authority_gates()
    verify_verified_source_candidates()
    verify_source_roles()
    verify_binary_evidence()
    verify_function_boundary_candidates()
    verify_function_byte_slices()
    verify_function_slice_sources()
    verify_function_reconstruction_tasks()
    verify_one_shot_response_contract()
    verify_semantic_readiness()
    verify_authority_summary()
    verify_proof_commands()
    verify_source_index()
    require_hash(ROOT / "original.bin", expected_binary)
    require_hash(ROOT / "full-binary.S", expected_asm_source)
    require_hash(ROOT / "full-binary.c", expected_c_source)

    asm_obj = ROOT / "verify-standalone-asm.o"
    asm_bin = ROOT / "verify-standalone-asm.bin"
    proc = run(["gcc", "-x", "assembler-with-cpp", "-c", "full-binary.S", "-o", asm_obj.name])
    if proc.returncode != 0:
        raise SystemExit(proc.stderr or proc.stdout or "assembler compile failed")
    proc = run(["objcopy", "-O", "binary", "-j", ".reconkit_image", asm_obj.name, asm_bin.name])
    if proc.returncode != 0:
        raise SystemExit(proc.stderr or proc.stdout or "objcopy failed")
    require_hash(asm_bin, expected_binary)

    c_exe = ROOT / "verify-standalone-c-emitter"
    c_bin = ROOT / "verify-standalone-c.bin"
    proc = run(["gcc", "-O2", "full-binary.c", "-o", c_exe.name])
    if proc.returncode != 0:
        raise SystemExit(proc.stderr or proc.stdout or "C emitter compile failed")
    proc = run([str(c_exe)], stdout_path=c_bin)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace") if isinstance(proc.stderr, bytes) else str(proc.stderr)
        raise SystemExit(stderr or "C emitter run failed")
    require_hash(c_bin, expected_binary)

    candidates_manifest = read_json(ROOT / "VERIFIED_SOURCE_CANDIDATES.json")
    candidates = candidates_manifest.get("candidates")
    if not isinstance(candidates, list):
        raise SystemExit("VERIFIED_SOURCE_CANDIDATES.json has no candidates list")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise SystemExit("VERIFIED_SOURCE_CANDIDATES.json contains a non-object candidate")
        if candidate.get("path") not in ("candidate-source.c", "candidate-source-tree"):
            continue
        candidate_sha = candidate.get("sha256")
        source_tree = candidate.get("sourceTree")
        if isinstance(source_tree, list):
            for row in source_tree:
                if not isinstance(row, dict):
                    raise SystemExit("candidate source tree contains non-object row")
                rel = row.get("path")
                expected = row.get("sha256")
                if not isinstance(rel, str) or not isinstance(expected, str):
                    raise SystemExit("candidate source tree row is incomplete")
                require_hash(ROOT / rel, expected)
        elif not isinstance(candidate_sha, str):
            raise SystemExit("candidate-source.c has no expected source hash")
        else:
            require_hash(ROOT / "candidate-source.c", candidate_sha)
        candidate_bin = ROOT / "verify-candidate-source.bin"
        if candidate.get("verificationMode") == "command-output-file":
            replay_command = candidate.get("replayCommand")
            if not isinstance(replay_command, str) or not replay_command.strip():
                raise SystemExit("candidate-source.c has no replay command")
            proc = subprocess.run(
                replay_command,
                cwd=ROOT,
                shell=True,
                text=True,
                capture_output=True,
                check=False,
                timeout=VERIFY_TIMEOUT,
            )
            if proc.returncode != 0:
                raise SystemExit(proc.stderr or proc.stdout or "candidate replay command failed")
        elif candidate.get("verificationMode") == "c-stdout-emitter":
            candidate_exe = ROOT / "verify-candidate-source-emitter"
            proc = run(["gcc", "-O2", "candidate-source.c", "-o", candidate_exe.name])
            if proc.returncode != 0:
                raise SystemExit(proc.stderr or proc.stdout or "candidate source compile failed")
            proc = run([str(candidate_exe)], stdout_path=candidate_bin)
            if proc.returncode != 0:
                stderr = proc.stderr.decode("utf-8", errors="replace") if isinstance(proc.stderr, bytes) else str(proc.stderr)
                raise SystemExit(stderr or "candidate source run failed")
        else:
            raise SystemExit("candidate-source.c has unsupported verification mode")
        require_hash(candidate_bin, expected_binary)

    print("ONE_SHOT_SOURCE_PACKAGE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    py_verifier = out_dir / "VERIFY.py"
    py_verifier.write_text(py_script)
    py_verifier.chmod(0o755)

    script = "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            "cd \"$(dirname \"${BASH_SOURCE[0]}\")\"",
            "export CCACHE_DISABLE=1",
            "export CCACHE_DIR=\"$PWD/.ccache\"",
            "if command -v python3 >/dev/null 2>&1; then",
            "  exec python3 VERIFY.py",
            "fi",
            "",
            f"EXPECTED_BINARY_SHA={shell_single_quote(receipt.get('originalSha256'))}",
            f"EXPECTED_ASM_SOURCE_SHA={shell_single_quote(receipt.get('sourceSha256'))}",
            f"EXPECTED_C_SOURCE_SHA={shell_single_quote(receipt.get('cSourceSha256'))}",
            f"EXPECTED_CANDIDATE_SOURCE_SHA={shell_single_quote(receipt.get('candidateSourceSha256') or '')}",
            f"EXPECTED_CANDIDATE_REPLAY_COMMAND={shell_single_quote(receipt.get('candidateReplayCommand') or '')}",
            "",
            "sha_file() { sha256sum \"$1\" | awk '{print $1}'; }",
            "require_hash() {",
            "  local path=\"$1\"",
            "  local expected=\"$2\"",
            "  local actual",
            "  actual=\"$(sha_file \"$path\")\"",
            "  if [[ \"$actual\" != \"$expected\" ]]; then",
            "    echo \"hash mismatch: $path\" >&2",
            "    echo \"  expected: $expected\" >&2",
            "    echo \"  actual:   $actual\" >&2",
            "    exit 1",
            "  fi",
            "}",
            "",
            "require_hash full-binary.S \"$EXPECTED_ASM_SOURCE_SHA\"",
            "require_hash full-binary.c \"$EXPECTED_C_SOURCE_SHA\"",
            "require_hash original.bin \"$EXPECTED_BINARY_SHA\"",
            "",
            "gcc -x assembler-with-cpp -c full-binary.S -o verify-standalone-asm.o",
            "objcopy -O binary -j .reconkit_image verify-standalone-asm.o verify-standalone-asm.bin",
            "require_hash verify-standalone-asm.bin \"$EXPECTED_BINARY_SHA\"",
            "",
            "gcc -O2 full-binary.c -o verify-standalone-c-emitter",
            "./verify-standalone-c-emitter > verify-standalone-c.bin",
            "require_hash verify-standalone-c.bin \"$EXPECTED_BINARY_SHA\"",
            "",
            "if [[ -n \"$EXPECTED_CANDIDATE_SOURCE_SHA\" && -f candidate-source.c ]]; then",
            "  require_hash candidate-source.c \"$EXPECTED_CANDIDATE_SOURCE_SHA\"",
            "  if [[ -n \"$EXPECTED_CANDIDATE_REPLAY_COMMAND\" ]]; then",
            "    bash -c \"$EXPECTED_CANDIDATE_REPLAY_COMMAND\"",
            "  else",
            "    gcc -O2 candidate-source.c -o verify-candidate-source-emitter",
            "    ./verify-candidate-source-emitter > verify-candidate-source.bin",
            "  fi",
            "  require_hash verify-candidate-source.bin \"$EXPECTED_BINARY_SHA\"",
            "fi",
            "",
            "echo \"ONE_SHOT_SOURCE_PACKAGE_OK\"",
            "",
        ]
    )
    verifier = out_dir / "VERIFY.sh"
    verifier.write_text(script)
    verifier.chmod(0o755)


def write_makefile(out_dir: Path, receipt: dict[str, Any]) -> None:
    lines = [
            ".PHONY: verify asm c candidate clean",
            "",
            "verify:",
            "\t./VERIFY.sh",
            "",
            "asm: verify-standalone-asm.bin",
            "",
            "verify-standalone-asm.bin: full-binary.S",
            "\tgcc -x assembler-with-cpp -c full-binary.S -o verify-standalone-asm.o",
            "\tobjcopy -O binary -j .reconkit_image verify-standalone-asm.o verify-standalone-asm.bin",
            "",
            "c: verify-standalone-c.bin",
            "",
            "verify-standalone-c.bin: full-binary.c",
            "\tgcc -O2 full-binary.c -o verify-standalone-c-emitter",
            "\t./verify-standalone-c-emitter > verify-standalone-c.bin",
            "",
    ]
    if receipt.get("candidateReplayCommand"):
        lines.extend(
            [
                "candidate:",
                "\t./REPLAY_CANDIDATE.sh",
                "",
            ]
        )
    lines.extend(
        [
            "clean:",
            "\trm -f verify-standalone-asm.o verify-standalone-asm.bin verify-standalone-c-emitter verify-standalone-c.bin",
            "\trm -f candidate-source-emitter candidate-source-output.bin verify-candidate-source-emitter verify-candidate-source.bin",
            "\trm -f verify-candidate-source.bin",
            "\trm -rf .ccache",
            "",
        ]
    )
    makefile = "\n".join(lines)
    (out_dir / "Makefile").write_text(makefile)


def run_standalone_verifier(out_dir: Path, timeout: int) -> dict[str, Any]:
    env = os.environ.copy()
    env["CCACHE_DISABLE"] = "1"
    env["CCACHE_DIR"] = str(out_dir / ".ccache")
    try:
        proc = subprocess.run(
            [sys.executable, "VERIFY.py"],
            cwd=out_dir,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "failed",
            "ok": False,
            "command": "VERIFY.py",
            "error": f"standalone verifier timed out after {timeout}s",
        }
    ok = proc.returncode == 0 and "ONE_SHOT_SOURCE_PACKAGE_OK" in proc.stdout
    return {
        "status": "matched" if ok else "failed",
        "ok": ok,
        "command": "VERIFY.py",
        "returnCode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def clean_standalone_artifacts(out_dir: Path) -> None:
    for name in (
        "verify-standalone-asm.o",
        "verify-standalone-asm.bin",
        "verify-standalone-c-emitter",
        "verify-standalone-c.bin",
        "candidate-source-emitter",
        "candidate-source-output.bin",
        "verify-candidate-source-emitter",
        "verify-candidate-source.bin",
    ):
        try:
            (out_dir / name).unlink()
        except FileNotFoundError:
            pass
    shutil.rmtree(out_dir / ".ccache", ignore_errors=True)


def clean_new_top_level_entries(out_dir: Path, before_names: set[str]) -> None:
    for path in out_dir.iterdir():
        if path.name in before_names:
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()


def create_archive(out_dir: Path) -> dict[str, Any]:
    archive_path = out_dir.with_suffix(".tar.gz")
    root_name = out_dir.name
    def add_path(archive: tarfile.TarFile, path: Path, arcname: str) -> None:
        info = archive.gettarinfo(str(path), arcname=arcname)
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mtime = 0
        if path.is_file():
            with path.open("rb") as fh:
                archive.addfile(info, fh)
        else:
            archive.addfile(info)

    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as archive:
                for path in sorted(out_dir.iterdir(), key=lambda item: item.name):
                    if path.name.startswith("verify-standalone") or path.name == ".ccache":
                        continue
                    add_path(archive, path, f"{root_name}/{path.name}")
                    if path.is_dir():
                        for child in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
                            if child.is_symlink():
                                continue
                            add_path(archive, child, f"{root_name}/{path.name}/{child.relative_to(path).as_posix()}")
    return {
        "path": str(archive_path),
        "sha256": sha256_file(archive_path),
        "size": archive_path.stat().st_size,
        "determinismScope": (
            "tar and gzip metadata are normalized; package file contents still include generation-time "
            "and output-path provenance, so archives are not expected to match across different output directories."
        ),
    }


def create_deliverable_bundle(out_dir: Path, archive_path: Path, receipt_dir: Path, bundle_path: Path) -> dict[str, Any]:
    root_name = out_dir.name
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = bundle_path.with_name(f"{bundle_path.name}.manifest.tmp")
    deliverable_path = receipt_dir / "deliverable.json"
    deliverable = json.loads(deliverable_path.read_text()) if deliverable_path.exists() else {}
    archive_member_path = f"{root_name}/{archive_path.name}"
    members: list[dict[str, Any]] = [
        {
            "path": archive_member_path,
            "size": archive_path.stat().st_size,
            "sha256": sha256_file(archive_path),
        }
    ]
    for child in sorted(receipt_dir.rglob("*"), key=lambda item: item.relative_to(receipt_dir).as_posix()):
        if child.is_file() and not child.is_symlink() and child.name != "BUNDLE_MANIFEST.json":
            members.append(
                {
                    "path": f"{root_name}/receipts/{child.relative_to(receipt_dir).as_posix()}",
                    "size": child.stat().st_size,
                    "sha256": sha256_file(child),
                }
            )
    bundle_manifest = {
        "schema": "reconkit.one-shot-source-deliverable-bundle-manifest.v1",
        "layout": {
            "archive": archive_member_path,
            "receipts": f"{root_name}/receipts/",
            "deliverable": f"{root_name}/receipts/deliverable.json",
        },
        "authoritySummarySha256": deliverable.get("authoritySummarySha256"),
        "contentIdentity": deliverable.get("contentIdentity"),
        "members": members,
    }
    manifest_path.write_text(json.dumps(bundle_manifest, indent=2, sort_keys=True) + "\n")

    def add_path(archive: tarfile.TarFile, path: Path, arcname: str) -> None:
        info = archive.gettarinfo(str(path), arcname=arcname)
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mtime = 0
        if path.is_file():
            with path.open("rb") as fh:
                archive.addfile(info, fh)
        else:
            archive.addfile(info)

    try:
        with bundle_path.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
                with tarfile.open(fileobj=gz, mode="w") as bundle:
                    add_path(bundle, archive_path, archive_member_path)
                    add_path(bundle, manifest_path, "BUNDLE_MANIFEST.json")
                    add_path(bundle, receipt_dir, f"{root_name}/receipts")
                    for child in sorted(receipt_dir.rglob("*"), key=lambda item: item.relative_to(receipt_dir).as_posix()):
                        if child.is_symlink() or child.name == "BUNDLE_MANIFEST.json":
                            continue
                        add_path(bundle, child, f"{root_name}/receipts/{child.relative_to(receipt_dir).as_posix()}")
    finally:
        manifest_path.unlink(missing_ok=True)
    return {
        "path": str(bundle_path),
        "sha256": sha256_file(bundle_path),
        "size": bundle_path.stat().st_size,
        "layout": {
            "archive": archive_member_path,
            "receipts": f"{root_name}/receipts/",
            "deliverable": f"{root_name}/receipts/deliverable.json",
            "manifest": "BUNDLE_MANIFEST.json",
        },
    }


def verify_archive(archive_path: Path, package_name: str, timeout: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="reconkit-one-shot-archive-") as tmp:
        tmp_dir = Path(tmp)
        try:
            with tarfile.open(archive_path, "r:gz") as archive:
                members = archive.getmembers()
                expected_prefix = f"{package_name}/"
                for member in members:
                    member_path = Path(member.name)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        raise ValueError(f"unsafe archive member path: {member.name}")
                    if member.name != package_name and not member.name.startswith(expected_prefix):
                        raise ValueError(f"archive member outside package root: {member.name}")
                    if member.issym() or member.islnk():
                        raise ValueError(f"archive links are not allowed: {member.name}")
                archive.extractall(tmp_dir, members)
        except Exception as exc:
            return {
                "status": "failed",
                "ok": False,
                "archive": str(archive_path),
                "error": f"archive extraction failed: {exc}",
            }
        package_dir = tmp_dir / package_name
        if not package_dir.exists():
            return {
                "status": "failed",
                "ok": False,
                "archive": str(archive_path),
                "error": f"archive did not contain expected package root: {package_name}",
            }
        result = run_standalone_verifier(package_dir, timeout)
        return {
            "status": result["status"],
            "ok": result["ok"],
            "archive": str(archive_path),
            "command": result.get("command"),
            "returnCode": result.get("returnCode"),
            "stdout": result.get("stdout"),
            "stderr": result.get("stderr"),
        }


def c_byte_literal(data: bytes) -> str:
    rows = []
    for offset in range(0, len(data), 12):
        chunk = data[offset : offset + 12]
        rows.append("    " + ", ".join(f"0x{value:02x}" for value in chunk) + ",")
    return "\n".join(rows)


def write_c_source_roundtrip(out_dir: Path, binary: Path, timeout: int) -> dict[str, Any]:
    source_path = out_dir / "full-binary.c"
    emitter_path = out_dir / "full-binary-c-emitter"
    emitted_path = out_dir / "full-binary-c-output.bin"
    data = binary.read_bytes()
    source = "\n".join(
        [
            "/* Generated by one-shot-source.py. */",
            "/* C byte-source emitter: reproduces exact original bytes, not semantic decompilation. */",
            "#include <stdio.h>",
            "#include <stdint.h>",
            "",
            f"static const uint8_t reconkit_image[{len(data)}] = {{",
            c_byte_literal(data),
            "};",
            "",
            "int main(void) {",
            "    return fwrite(reconkit_image, 1, sizeof(reconkit_image), stdout) == sizeof(reconkit_image) ? 0 : 1;",
            "}",
            "",
        ]
    )
    source_path.write_text(source)
    try:
        compile_proc = subprocess.run(
            ["gcc", "-O2", str(source_path), "-o", str(emitter_path)],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        report = {
            "schema": "reconkit.c-source-roundtrip.v1",
            "source": str(source_path),
            "status": "failed",
            "byteIdentical": False,
            "error": f"C emitter compile timed out after {timeout}s",
        }
        (out_dir / "c-source-roundtrip.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        return report

    run_proc = subprocess.CompletedProcess([str(emitter_path)], 1, "", "compile failed")
    if compile_proc.returncode == 0:
        try:
            with emitted_path.open("wb") as fh:
                run_proc = subprocess.run(
                    [str(emitter_path)],
                    stdout=fh,
                    text=False,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=timeout,
                )
        except subprocess.TimeoutExpired:
            run_proc = subprocess.CompletedProcess([str(emitter_path)], 124, b"", b"C emitter run timed out")
    original_sha = sha256_file(binary)
    emitted_sha = sha256_file(emitted_path) if emitted_path.exists() else ""
    byte_identical = compile_proc.returncode == 0 and run_proc.returncode == 0 and emitted_sha == original_sha
    report = {
        "schema": "reconkit.c-source-roundtrip.v1",
        "source": str(source_path),
        "sourceSha256": sha256_file(source_path),
        "emitter": str(emitter_path),
        "emittedBinary": str(emitted_path),
        "status": "matched" if byte_identical else "failed",
        "byteIdentical": byte_identical,
        "originalSha256": original_sha,
        "emittedSha256": emitted_sha,
        "originalSize": len(data),
        "emittedSize": emitted_path.stat().st_size if emitted_path.exists() else 0,
        "compileReturnCode": compile_proc.returncode,
        "compileStdout": compile_proc.stdout[-4000:],
        "compileStderr": compile_proc.stderr[-4000:],
        "runReturnCode": run_proc.returncode,
        "runStderr": (run_proc.stderr or b"")[-4000:].decode("utf-8", errors="replace")
        if isinstance(run_proc.stderr, bytes)
        else str(run_proc.stderr or "")[-4000:],
        "sourceType": "c-byte-emitter",
        "sourceAuthority": "original-bytes",
        "semanticDecompilation": False,
        "scopeNote": "C source emits the exact original bytes; it is authoritative for byte reproduction, not semantic decompilation.",
    }
    (out_dir / "c-source-roundtrip.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def write_supplied_candidate_roundtrip(
    out_dir: Path,
    binary: Path,
    candidate_source: Path | None,
    candidate_source_dir: Path | None,
    candidate_build_command: str | None,
    timeout: int,
) -> dict[str, Any] | None:
    if candidate_source is None and candidate_source_dir is None:
        return None
    if candidate_source is not None and not candidate_source.exists():
        raise SystemExit(f"candidate source not found: {candidate_source}")
    if candidate_source_dir is not None and not candidate_source_dir.is_dir():
        raise SystemExit(f"candidate source directory not found: {candidate_source_dir}")
    packaged_source = out_dir / "candidate-source.c"
    packaged_source_dir = out_dir / "candidate-source-tree"
    preexisting_package_entries = {path.name for path in out_dir.iterdir()} | {"candidate-source.c", "candidate-source-tree"}
    if candidate_source is not None:
        shutil.copyfile(candidate_source, packaged_source)
    if candidate_source_dir is not None:
        shutil.copytree(candidate_source_dir, packaged_source_dir, symlinks=False)
    emitter_path = out_dir / "candidate-source-emitter"
    emitted_path = out_dir / "candidate-source-output.bin"
    verification_mode = "command-output-file" if candidate_build_command else "c-stdout-emitter"
    if candidate_build_command:
        source_value = str(packaged_source) if candidate_source is not None else ""
        replay_source_value = "candidate-source.c" if candidate_source is not None else ""
        build_command = candidate_build_command.format(
            source=source_value,
            source_dir=str(packaged_source_dir),
            output=str(emitted_path),
            package=str(out_dir),
        )
        replay_command = candidate_build_command.format(
            source=replay_source_value,
            source_dir="candidate-source-tree",
            output="verify-candidate-source.bin",
            package=".",
        )
        try:
            compile_proc = subprocess.run(
                build_command,
                cwd=out_dir,
                shell=True,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            report = {
                "schema": "reconkit.supplied-source-candidate-roundtrip.v1",
                "source": str(packaged_source) if candidate_source is not None else None,
                "sourceDir": str(packaged_source_dir) if candidate_source_dir is not None else None,
                "status": "failed",
                "byteIdentical": False,
                "error": f"candidate build command timed out after {timeout}s",
                "verificationMode": verification_mode,
                "buildCommand": build_command,
                "replayCommand": replay_command,
            }
            (out_dir / "candidate-source-roundtrip.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            return report
        run_proc = subprocess.CompletedProcess([build_command], 0 if compile_proc.returncode == 0 else 1, "", "")
    else:
        if candidate_source is None:
            raise SystemExit("--candidate-source-dir requires --candidate-build-command")
        build_command = f"gcc -O2 {packaged_source} -o {emitter_path}; {emitter_path} > {emitted_path}"
        replay_command = "gcc -O2 candidate-source.c -o verify-candidate-source-emitter && ./verify-candidate-source-emitter > verify-candidate-source.bin"
        try:
            compile_proc = subprocess.run(
                ["gcc", "-O2", str(packaged_source), "-o", str(emitter_path)],
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            report = {
                "schema": "reconkit.supplied-source-candidate-roundtrip.v1",
                "source": str(packaged_source),
                "sourceDir": None,
                "status": "failed",
                "byteIdentical": False,
                "error": f"candidate source compile timed out after {timeout}s",
                "verificationMode": verification_mode,
                "buildCommand": build_command,
                "replayCommand": replay_command,
            }
            (out_dir / "candidate-source-roundtrip.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            return report

        run_proc = subprocess.CompletedProcess([str(emitter_path)], 1, "", "compile failed")
        if compile_proc.returncode == 0:
            try:
                with emitted_path.open("wb") as fh:
                    run_proc = subprocess.run(
                        [str(emitter_path)],
                        stdout=fh,
                        text=False,
                        stderr=subprocess.PIPE,
                        check=False,
                        timeout=timeout,
                    )
            except subprocess.TimeoutExpired:
                run_proc = subprocess.CompletedProcess([str(emitter_path)], 124, b"", b"candidate source run timed out")
    original_sha = sha256_file(binary)
    emitted_sha = sha256_file(emitted_path) if emitted_path.exists() else ""
    byte_identical = compile_proc.returncode == 0 and run_proc.returncode == 0 and emitted_sha == original_sha
    report = {
        "schema": "reconkit.supplied-source-candidate-roundtrip.v1",
        "source": str(packaged_source) if candidate_source is not None else None,
        "sourceSha256": sha256_file(packaged_source) if candidate_source is not None else None,
        "sourceDir": str(packaged_source_dir) if candidate_source_dir is not None else None,
        "sourceTree": tree_file_rows(packaged_source_dir, "candidate-source-tree")
        if candidate_source_dir is not None
        else None,
        "emitter": str(emitter_path),
        "emittedBinary": str(emitted_path),
        "status": "matched" if byte_identical else "failed",
        "byteIdentical": byte_identical,
        "verificationMode": verification_mode,
        "buildCommand": build_command,
        "replayCommand": replay_command,
        "originalSha256": original_sha,
        "emittedSha256": emitted_sha,
        "originalSize": binary.stat().st_size,
        "emittedSize": emitted_path.stat().st_size if emitted_path.exists() else 0,
        "compileReturnCode": compile_proc.returncode,
        "compileStdout": compile_proc.stdout[-4000:],
        "compileStderr": compile_proc.stderr[-4000:],
        "runReturnCode": run_proc.returncode,
        "runStderr": (run_proc.stderr or b"")[-4000:].decode("utf-8", errors="replace")
        if isinstance(run_proc.stderr, bytes)
        else str(run_proc.stderr or "")[-4000:],
        "sourceType": "supplied-c-source-candidate",
        "sourceAuthority": "candidate-source",
        "semanticDecompilation": False,
        "scopeNote": (
            "Supplied source candidate produced exact original bytes under the recorded verification command. "
            "This proves byte-exact behavior for that command, not original authorship."
        ),
    }
    (out_dir / "candidate-source-roundtrip.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    allowed_new_entries = {
        "candidate-source-roundtrip.json",
        "candidate-source-emitter",
        "candidate-source-output.bin",
    }
    for path in out_dir.iterdir():
        if path.name in preexisting_package_entries or path.name in allowed_new_entries:
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    return report


def write_receipt(
    out_dir: Path,
    binary: Path,
    roundtrip: dict[str, Any],
    authority_report: dict[str, Any],
    c_roundtrip: dict[str, Any],
    candidate_roundtrip: dict[str, Any] | None,
) -> dict[str, Any]:
    byte_authoritative = (
        authority_report.get("status") == "authoritative"
        and int(authority_report.get("byteSourceArtifactsVerified") or 0) > 0
        and not authority_report.get("ambiguousOrUnverifiedRows")
        and roundtrip.get("byteIdentical") is True
        and c_roundtrip.get("byteIdentical") is True
    )
    semantic_source = int(authority_report.get("semanticSourceBundlesVerified") or 0)
    source = Path(str(roundtrip.get("source") or ""))
    source_sha256 = sha256_file(source) if source.exists() else ""
    source_accuracy = {
        "assembler": {
            "path": roundtrip.get("source"),
            "byteIdentical": roundtrip.get("byteIdentical") is True,
            "sha256": source_sha256,
            "rebuildOutputSha256": roundtrip.get("rebuiltSha256"),
        },
        "cByteEmitter": {
            "path": c_roundtrip.get("source"),
            "byteIdentical": c_roundtrip.get("byteIdentical") is True,
            "sha256": c_roundtrip.get("sourceSha256"),
            "rebuildOutputSha256": c_roundtrip.get("emittedSha256"),
        },
        "suppliedCandidate": {
            "path": "candidate-source.c"
            if candidate_roundtrip and candidate_roundtrip.get("source")
            else ("candidate-source-tree" if candidate_roundtrip and candidate_roundtrip.get("sourceDir") else None),
            "byteIdentical": candidate_roundtrip.get("byteIdentical") is True if candidate_roundtrip else None,
            "sha256": candidate_roundtrip.get("sourceSha256") if candidate_roundtrip else None,
            "rebuildOutputSha256": candidate_roundtrip.get("emittedSha256") if candidate_roundtrip else None,
        },
    }
    receipt = {
        "schema": "reconkit.one-shot-source-receipt.v1",
        "generatedAt": _datetime.datetime.now(_datetime.UTC).isoformat(),
        "binary": str(binary),
        "status": "authoritative" if byte_authoritative else "incomplete",
        "authorityClass": "byte-authoritative-source" if byte_authoritative else "unproven-source",
        "accuracyClass": "byte-exact" if byte_authoritative else "unproven",
        "sourceAccuracy": source_accuracy,
        "source": roundtrip.get("source"),
        "sourceSha256": source_sha256,
        "cSource": c_roundtrip.get("source"),
        "cSourceSha256": c_roundtrip.get("sourceSha256"),
        "cSourceByteIdentical": c_roundtrip.get("byteIdentical") is True,
        "cSourceRoundtripReport": str(out_dir / "c-source-roundtrip.json"),
        "candidateSource": str(out_dir / "candidate-source.c") if candidate_roundtrip and candidate_roundtrip.get("source") else None,
        "candidateSourceDir": str(out_dir / "candidate-source-tree")
        if candidate_roundtrip and candidate_roundtrip.get("sourceDir")
        else None,
        "candidateSourcePath": "candidate-source.c"
        if candidate_roundtrip and candidate_roundtrip.get("source")
        else ("candidate-source-tree" if candidate_roundtrip and candidate_roundtrip.get("sourceDir") else None),
        "candidateSourceSha256": candidate_roundtrip.get("sourceSha256") if candidate_roundtrip else None,
        "candidateSourceTree": candidate_roundtrip.get("sourceTree") if candidate_roundtrip else None,
        "candidateSourceByteIdentical": candidate_roundtrip.get("byteIdentical") is True if candidate_roundtrip else None,
        "candidateSourceRoundtripReport": str(out_dir / "candidate-source-roundtrip.json")
        if candidate_roundtrip
        else None,
        "candidateOutputSha256": candidate_roundtrip.get("emittedSha256") if candidate_roundtrip else None,
        "candidateVerificationMode": candidate_roundtrip.get("verificationMode") if candidate_roundtrip else None,
        "candidateReplayCommand": candidate_roundtrip.get("replayCommand") if candidate_roundtrip else None,
        "artifactMode": roundtrip.get("artifactMode"),
        "packageSelfContained": bool(roundtrip.get("blob")),
        "blob": roundtrip.get("blob"),
        "roundtripReport": str(out_dir / "binary-source-roundtrip.json"),
        "authorityReport": str(out_dir / "source-authority-report.json"),
        "byteIdentical": roundtrip.get("byteIdentical") is True,
        "originalSha256": roundtrip.get("originalSha256"),
        "rebuiltSha256": roundtrip.get("rebuiltSha256"),
        "originalSize": roundtrip.get("originalSize"),
        "rebuiltSize": roundtrip.get("rebuiltSize"),
        "sourceType": roundtrip.get("sourceType"),
        "sourceAuthority": roundtrip.get("sourceAuthority"),
        "semanticDecompilation": roundtrip.get("semanticDecompilation"),
        "semanticSourceBundlesVerified": semantic_source,
        "toolchain": {
            "gcc": tool_info("gcc"),
            "objcopy": tool_info("objcopy"),
        },
        "claimBoundary": (
            "This one-shot command generated source that is authoritative for exact byte reproduction. "
            "It is not a claim of full semantic decompilation unless semanticSourceBundlesVerified is non-zero "
            "and separately scoped by the authority report."
        ),
    }
    (out_dir / "one-shot-source-receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def write_markdown_summary(out_dir: Path, receipt: dict[str, Any], authority_report: dict[str, Any]) -> None:
    rows = authority_report.get("byteSourceRows")
    row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
    lines = [
        "# Authoritative Source Package",
        "",
        f"Status: `{receipt['status']}`",
        f"Input binary: `{receipt['binary']}`",
        f"Generated source: `{receipt['source']}`",
        f"Generated source SHA256: `{receipt['sourceSha256']}`",
        f"C source: `{receipt['cSource']}`",
        f"C source SHA256: `{receipt['cSourceSha256']}`",
        f"C source byte-identical: `{str(receipt['cSourceByteIdentical']).lower()}`",
        f"Artifact mode: `{receipt['artifactMode']}`",
        f"Package self-contained: `{str(receipt['packageSelfContained']).lower()}`",
        f"Blob: `{receipt['blob']}`",
        f"Roundtrip report: `{receipt['roundtripReport']}`",
        f"Authority report: `{receipt['authorityReport']}`",
        "",
        "## Byte Identity",
        "",
        f"- Byte identical: `{str(receipt['byteIdentical']).lower()}`",
        f"- Original SHA256: `{receipt['originalSha256']}`",
        f"- Rebuilt SHA256: `{receipt['rebuiltSha256']}`",
        f"- Original size: `{receipt['originalSize']}`",
        f"- Rebuilt size: `{receipt['rebuiltSize']}`",
        "",
        "## Source Authority",
        "",
        f"- Source type: `{receipt['sourceType']}`",
        f"- Source authority: `{receipt['sourceAuthority']}`",
        f"- Semantic decompilation: `{str(receipt['semanticDecompilation']).lower()}`",
        f"- Semantic source bundles verified: `{receipt['semanticSourceBundlesVerified']}`",
        f"- Strategy: `{row.get('strategy')}`",
        "",
        "## Toolchain",
        "",
        f"- gcc: `{receipt['toolchain']['gcc']['path']}` `{receipt['toolchain']['gcc']['version']}`",
        f"- objcopy: `{receipt['toolchain']['objcopy']['path']}` `{receipt['toolchain']['objcopy']['version']}`",
        "",
        "## Claim Boundary",
        "",
        receipt["claimBoundary"],
        "",
        "This package is authoritative for exact byte reproduction when `status` is `authoritative` and `byteIdentical` is `true`.",
        "It is not a full semantic source recovery claim unless the authority report lists verified semantic source bundles.",
        "",
    ]
    (out_dir / "AUTHORITATIVE_SOURCE.md").write_text("\n".join(lines))


def relpath_or_none(path: Path | None, base: Path) -> str | None:
    if path is None:
        return None
    try:
        return os.path.relpath(path, start=base)
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--complete", action="store_true", help="Produce package, archive, and standard receipts in one invocation.")
    parser.add_argument("--archive", action="store_true", help="Also write a portable .tar.gz source package archive.")
    parser.add_argument("--result-out", type=Path, help="Write the top-level generation JSON result to this path.")
    parser.add_argument("--archive-verify-out", type=Path, help="Write the archive verification JSON result to this path.")
    parser.add_argument("--proof-out", type=Path, help="Write aggregate proof JSON after generation succeeds.")
    parser.add_argument("--proof-markdown-out", type=Path, help="Write aggregate proof Markdown after generation succeeds.")
    parser.add_argument("--response-proof-out", type=Path, help="Write byte-accurate one-shot response proof JSON after generation succeeds.")
    parser.add_argument("--deliverable-out", type=Path, help="Write complete deliverable index JSON after generation succeeds.")
    parser.add_argument("--bundle-out", type=Path, help="Write a portable bundle containing the source archive and receipts.")
    parser.add_argument("--bundle-verify-out", type=Path, help="Write post-bundle verification JSON after bundle creation succeeds.")
    parser.add_argument("--receipt-dir", type=Path, help="Write the standard generation/proof receipts under this directory.")
    parser.add_argument(
        "--candidate-source",
        type=Path,
        help="Optional supplied C source candidate. It must compile with gcc -O2 and emit the exact binary bytes to stdout.",
    )
    parser.add_argument(
        "--candidate-source-dir",
        type=Path,
        help="Optional supplied source tree. Requires --candidate-build-command and is packaged as candidate-source-tree/.",
    )
    parser.add_argument(
        "--candidate-build-command",
        help=(
            "Optional command for --candidate-source. Use {source}, {output}, and {package}; "
            "the command must write the exact target bytes to {output}."
        ),
    )
    parser.add_argument(
        "--artifact-mode",
        choices=["full", "lean"],
        default="full",
        help="full retains copied blob/object/rebuilt files; lean verifies then retains compact source/report artifacts.",
    )
    args = parser.parse_args()
    if args.candidate_build_command and not args.candidate_source:
        if not args.candidate_source_dir:
            raise SystemExit("--candidate-build-command requires --candidate-source or --candidate-source-dir")
    if args.candidate_source_dir and not args.candidate_build_command:
        raise SystemExit("--candidate-source-dir requires --candidate-build-command")
    if args.complete:
        args.archive = True
        args.receipt_dir = args.receipt_dir or args.out / "receipts"
        args.bundle_out = args.bundle_out or args.out.with_suffix(".deliverable.tar.gz")
    if args.receipt_dir:
        args.receipt_dir.mkdir(parents=True, exist_ok=True)
        args.standard_result_out = args.receipt_dir / "one-shot-source-result.json"
        args.result_out = args.result_out or args.standard_result_out
        args.archive_verify_out = args.archive_verify_out or args.receipt_dir / "archive-verify.json"
        args.proof_out = args.proof_out or args.receipt_dir / "proof.json"
        args.proof_markdown_out = args.proof_markdown_out or args.receipt_dir / "proof.md"
        args.response_proof_out = args.response_proof_out or args.receipt_dir / "byte-accurate-response-proof.json"
        args.deliverable_out = args.deliverable_out or args.receipt_dir / "deliverable.json"
        if args.bundle_out:
            args.bundle_verify_out = args.bundle_verify_out or args.receipt_dir / "bundle-verify.json"

    args.out.mkdir(parents=True, exist_ok=True)
    roundtrip = binary_source.verify_roundtrip(args.binary, args.out, args.timeout, args.artifact_mode)
    roundtrip_path = args.out / "binary-source-roundtrip.json"
    authority_report = authority.build_report(roundtrip_path)
    (args.out / "source-authority-report.json").write_text(json.dumps(authority_report, indent=2, sort_keys=True) + "\n")
    c_roundtrip = write_c_source_roundtrip(args.out, args.binary, args.timeout)
    candidate_roundtrip = write_supplied_candidate_roundtrip(
        args.out,
        args.binary,
        args.candidate_source,
        args.candidate_source_dir,
        args.candidate_build_command,
        args.timeout,
    )
    receipt = write_receipt(args.out, args.binary, roundtrip, authority_report, c_roundtrip, candidate_roundtrip)
    write_markdown_summary(args.out, receipt, authority_report)
    write_standalone_verifier(args.out, receipt)
    write_makefile(args.out, receipt)
    write_candidate_build_recipe(args.out, receipt)
    write_candidate_replay_script(args.out, receipt)
    write_proof_commands_script(args.out, receipt)
    write_binary_evidence(args.out, args.binary, args.timeout)
    write_function_boundary_candidates(args.out)
    write_function_byte_slices(args.out)
    write_function_slice_sources(args.out)
    write_function_reconstruction_tasks(args.out)
    write_reconstruction_candidate_replay(args.out)
    write_reconstruction_candidate_importer(args.out)
    write_reconstruction_response_json_importer(args.out)
    write_reconstruction_response_json_validator(args.out)
    write_reconstruction_receipt_refresher(args.out)
    write_reconstruction_candidate_results(args.out)
    write_one_shot_reconstruction_request(args.out)
    write_reconstruction_response_template_exporter(args.out)
    write_byte_accurate_reconstruction_response_exporter(args.out)
    write_byte_accurate_reconstruction_response_prover(args.out)
    write_reconstruction_response_template(args.out)
    write_one_shot_reconstruction_request_json(args.out)
    write_reconstruction_response_template(args.out)
    write_one_shot_reconstruction_bundle(args.out)
    write_source_roles(args.out, receipt)
    write_semantic_readiness(args.out, receipt)
    write_semantic_source_authority_evaluator(args.out)
    write_semantic_source_authority_evaluation(args.out)
    write_sha256sums(args.out)
    content = content_manifest(args.out)
    claims = write_claims(args.out, receipt, content)
    write_authority_gates(args.out, receipt, claims, content)
    write_source_index(args.out, receipt)
    write_verified_source_candidates(args.out, receipt)
    write_toolchain_provenance(args.out, receipt)
    write_package_proof(args.out, receipt)
    write_authority_summary(args.out, receipt)
    write_package_proof(args.out, receipt)
    write_package_readme(args.out, receipt, claims)
    package_manifest = package_file_manifest(args.out)
    receipt["packageManifest"] = str(args.out / "package-manifest.json")
    receipt["packageManifestFileCount"] = len(package_manifest["files"])
    receipt["contentManifest"] = str(args.out / "CONTENT_MANIFEST.json")
    receipt["contentIdentity"] = content["contentIdentity"]
    (args.out / "one-shot-source-receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    write_markdown_summary(args.out, receipt, authority_report)
    write_standalone_verifier(args.out, receipt)
    write_makefile(args.out, receipt)
    write_candidate_build_recipe(args.out, receipt)
    write_candidate_replay_script(args.out, receipt)
    write_proof_commands_script(args.out, receipt)
    write_binary_evidence(args.out, args.binary, args.timeout)
    write_function_boundary_candidates(args.out)
    write_function_byte_slices(args.out)
    write_function_slice_sources(args.out)
    write_function_reconstruction_tasks(args.out)
    write_reconstruction_response_json_importer(args.out)
    write_reconstruction_response_json_validator(args.out)
    write_reconstruction_receipt_refresher(args.out)
    write_reconstruction_response_template_exporter(args.out)
    write_byte_accurate_reconstruction_response_exporter(args.out)
    write_byte_accurate_reconstruction_response_prover(args.out)
    write_reconstruction_response_template(args.out)
    write_one_shot_reconstruction_request_json(args.out)
    write_reconstruction_response_template(args.out)
    write_one_shot_reconstruction_bundle(args.out)
    write_source_roles(args.out, receipt)
    write_semantic_readiness(args.out, receipt)
    write_semantic_source_authority_evaluator(args.out)
    write_semantic_source_authority_evaluation(args.out)
    write_sha256sums(args.out)
    content = content_manifest(args.out)
    claims = write_claims(args.out, receipt, content)
    write_authority_gates(args.out, receipt, claims, content)
    write_source_index(args.out, receipt)
    write_verified_source_candidates(args.out, receipt)
    write_toolchain_provenance(args.out, receipt)
    write_package_proof(args.out, receipt)
    write_authority_summary(args.out, receipt)
    write_package_proof(args.out, receipt)
    write_package_readme(args.out, receipt, claims)
    package_file_manifest(args.out)
    pre_verifier_entries = {path.name for path in args.out.iterdir()}
    standalone = run_standalone_verifier(args.out, args.timeout)
    clean_new_top_level_entries(args.out, pre_verifier_entries)
    if standalone["ok"]:
        claims["proven"]["packageLocalVerifierPassedAtGeneration"] = True
        (args.out / "CLAIMS.json").write_text(json.dumps(claims, indent=2, sort_keys=True) + "\n")
        write_authority_gates(args.out, receipt, claims, content)
        write_toolchain_provenance(args.out, receipt)
        write_package_proof(args.out, receipt)
        write_authority_summary(args.out, receipt)
        write_package_proof(args.out, receipt)
        write_package_readme(args.out, receipt, claims)
        write_proof_commands_script(args.out, receipt)
        write_binary_evidence(args.out, args.binary, args.timeout)
        write_function_boundary_candidates(args.out)
        write_function_byte_slices(args.out)
        write_function_slice_sources(args.out)
        write_function_reconstruction_tasks(args.out)
        write_reconstruction_response_json_importer(args.out)
        write_reconstruction_response_json_validator(args.out)
        write_reconstruction_receipt_refresher(args.out)
        write_reconstruction_response_template_exporter(args.out)
        write_byte_accurate_reconstruction_response_exporter(args.out)
        write_byte_accurate_reconstruction_response_prover(args.out)
        write_reconstruction_response_template(args.out)
        write_one_shot_reconstruction_request_json(args.out)
        write_reconstruction_response_template(args.out)
        write_one_shot_reconstruction_bundle(args.out)
        write_source_roles(args.out, receipt)
        write_semantic_readiness(args.out, receipt)
        write_semantic_source_authority_evaluator(args.out)
        write_semantic_source_authority_evaluation(args.out)
        content = content_manifest(args.out)
        claims = write_claims(args.out, receipt, content)
        claims["proven"]["packageLocalVerifierPassedAtGeneration"] = True
        (args.out / "CLAIMS.json").write_text(json.dumps(claims, indent=2, sort_keys=True) + "\n")
        write_authority_gates(args.out, receipt, claims, content)
        write_package_proof(args.out, receipt)
        write_authority_summary(args.out, receipt)
        write_package_proof(args.out, receipt)
        receipt["contentManifest"] = str(args.out / "CONTENT_MANIFEST.json")
        receipt["contentIdentity"] = content["contentIdentity"]
        (args.out / "one-shot-source-receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        write_package_readme(args.out, receipt, claims)
        package_file_manifest(args.out)
        clean_standalone_artifacts(args.out)
    archive = create_archive(args.out) if standalone["ok"] and args.archive else None
    archive_verifier = (
        archive_verify_mod.verify_archive(
            Path(str(archive["path"])),
            args.timeout,
            expect_archive_sha256=str(archive.get("sha256")),
            expect_content_identity=receipt.get("contentIdentity"),
        )
        if isinstance(archive, dict)
        else None
    )
    if args.archive_verify_out and archive_verifier is not None:
        args.archive_verify_out.parent.mkdir(parents=True, exist_ok=True)
        args.archive_verify_out.write_text(json.dumps(archive_verifier, indent=2, sort_keys=True) + "\n")
    output = {**receipt, "standaloneVerifier": standalone, "archive": archive, "archiveVerifier": archive_verifier}
    if args.result_out:
        args.result_out.parent.mkdir(parents=True, exist_ok=True)
        args.result_out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    standard_result_out = getattr(args, "standard_result_out", None)
    if standard_result_out and standard_result_out != args.result_out:
        standard_result_out.parent.mkdir(parents=True, exist_ok=True)
        standard_result_out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps(output, indent=2, sort_keys=True))
    archive_ok = True if archive_verifier is None else archive_verifier["ok"]
    proof = None
    if receipt["status"] == "authoritative" and standalone["ok"] and archive_ok and (args.proof_out or args.proof_markdown_out):
        proof = (
            proof_mod.prove_archive(Path(str(archive["path"])), args.timeout, None, receipt.get("contentIdentity"))
            if isinstance(archive, dict)
            else proof_mod.prove_package(args.out, args.timeout, receipt.get("contentIdentity"))
        )
        if args.proof_out:
            args.proof_out.parent.mkdir(parents=True, exist_ok=True)
            args.proof_out.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")
        if args.proof_markdown_out:
            args.proof_markdown_out.parent.mkdir(parents=True, exist_ok=True)
            args.proof_markdown_out.write_text(proof_mod.markdown_report(proof))
    response_proof = None
    if receipt["status"] == "authoritative" and standalone["ok"] and archive_ok and args.response_proof_out:
        response_proof_proc = subprocess.run(
            [
                sys.executable,
                str(args.out / "PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py"),
                "--out",
                str(args.response_proof_out),
                "--timeout",
                str(args.timeout),
            ],
            cwd=args.out,
            text=True,
            capture_output=True,
            check=False,
            timeout=args.timeout * 4,
        )
        response_proof = (
            json.loads(args.response_proof_out.read_text())
            if args.response_proof_out.exists()
            else {
                "schema": "reconkit.one-shot-source-byte-accurate-response-proof.v1",
                "status": "failed",
                "ok": False,
            }
        )
        if isinstance(response_proof, dict):
            response_proof.setdefault("returnCode", response_proof_proc.returncode)
            if response_proof_proc.returncode != 0:
                response_proof.setdefault("stdout", response_proof_proc.stdout[-2000:])
                response_proof.setdefault("stderr", response_proof_proc.stderr[-2000:])
            args.response_proof_out.parent.mkdir(parents=True, exist_ok=True)
            args.response_proof_out.write_text(json.dumps(response_proof, indent=2, sort_keys=True) + "\n")
    bundle_verifier = None
    deliverable = None
    if args.deliverable_out:
        package_proof = json.loads((args.out / "PACKAGE_PROOF.json").read_text()) if (args.out / "PACKAGE_PROOF.json").exists() else {}
        deliverable_base = args.deliverable_out.parent
        archive_path = Path(str(archive["path"])) if isinstance(archive, dict) else None
        archive_with_paths = dict(archive) if isinstance(archive, dict) else None
        if archive_with_paths is not None and archive_path is not None:
            archive_with_paths["relativePath"] = relpath_or_none(archive_path, deliverable_base)
        deliverable = {
            "schema": "reconkit.one-shot-source-deliverable.v1",
            "status": "authoritative" if receipt["status"] == "authoritative" and standalone["ok"] and archive_ok else "failed",
            "deliverablePhase": "pre-bundle-index",
            "authoritySummary": {
                "schema": "reconkit.one-shot-source-authority-summary.v1",
                "status": receipt.get("status"),
                "authorityClass": receipt.get("authorityClass"),
                "accuracyClass": receipt.get("accuracyClass"),
                "authorityContractStatus": archive_verifier.get("authorityContractStatus")
                if isinstance(archive_verifier, dict)
                else None,
                "authorityGateStatus": package_proof.get("authorityGateStatus"),
                "sourceCandidateStatus": archive_verifier.get("sourceCandidateStatus")
                if isinstance(archive_verifier, dict)
                else None,
                "packageProofStatus": package_proof.get("status"),
                "contentIdentity": receipt.get("contentIdentity"),
                "semanticDecompilation": receipt.get("semanticDecompilation"),
                "claimBoundary": package_proof.get("claimBoundary") or receipt.get("claimBoundary"),
            },
            "authoritySummarySha256": sha256_file(args.out / "AUTHORITY_SUMMARY.json")
            if (args.out / "AUTHORITY_SUMMARY.json").exists()
            else None,
            "package": {
                "path": str(args.out),
                "relativePath": relpath_or_none(args.out, deliverable_base),
            },
            "archive": archive_with_paths,
            "contentIdentity": receipt.get("contentIdentity"),
            "packageProofStatus": package_proof.get("status"),
            "standaloneVerifier": standalone,
            "archiveVerifier": archive_verifier,
            "aggregateProof": {
                "status": proof.get("status") if isinstance(proof, dict) else None,
                "ok": proof.get("ok") if isinstance(proof, dict) else None,
            },
            "receipts": {
                "generation": {
                    "path": str(args.result_out),
                    "relativePath": relpath_or_none(args.result_out, deliverable_base),
                }
                if args.result_out
                else None,
                "archiveVerify": {
                    "path": str(args.archive_verify_out),
                    "relativePath": relpath_or_none(args.archive_verify_out, deliverable_base),
                }
                if args.archive_verify_out
                else None,
                "proof": {
                    "path": str(args.proof_out),
                    "relativePath": relpath_or_none(args.proof_out, deliverable_base),
                }
                if args.proof_out
                else None,
                "proofMarkdown": {
                    "path": str(args.proof_markdown_out),
                    "relativePath": relpath_or_none(args.proof_markdown_out, deliverable_base),
                }
                if args.proof_markdown_out
                else None,
                "byteAccurateResponseProof": {
                    "path": str(args.response_proof_out),
                    "relativePath": relpath_or_none(args.response_proof_out, deliverable_base),
                }
                if args.response_proof_out
                else None,
                "bundleVerify": {
                    "path": str(args.bundle_verify_out),
                    "relativePath": relpath_or_none(args.bundle_verify_out, deliverable_base),
                }
                if args.bundle_verify_out
                else None,
                "deliverable": {
                    "path": str(args.deliverable_out),
                    "relativePath": relpath_or_none(args.deliverable_out, deliverable_base),
                },
            },
            "sourceCandidates": package_proof.get("sourceCandidates"),
            "binaryEvidence": package_proof.get("binaryEvidence"),
            "functionBoundaryCandidates": package_proof.get("functionBoundaryCandidates"),
            "functionByteSlices": package_proof.get("functionByteSlices"),
            "functionSliceSources": package_proof.get("functionSliceSources"),
            "functionReconstructionTasks": package_proof.get("functionReconstructionTasks"),
            "functionReconstructionCandidateResults": package_proof.get("functionReconstructionCandidateResults"),
            "oneShotReconstructionRequest": package_proof.get("oneShotReconstructionRequest"),
            "oneShotReconstructionRequestJson": package_proof.get("oneShotReconstructionRequestJson"),
            "oneShotReconstructionBundle": package_proof.get("oneShotReconstructionBundle"),
            "oneShotCandidateImporter": package_proof.get("oneShotCandidateImporter"),
            "oneShotResponseJsonImporter": package_proof.get("oneShotResponseJsonImporter"),
            "oneShotResponseJsonValidator": package_proof.get("oneShotResponseJsonValidator"),
            "oneShotReceiptRefresher": package_proof.get("oneShotReceiptRefresher"),
            "oneShotResponseTemplate": package_proof.get("oneShotResponseTemplate"),
            "oneShotResponseTemplateExporter": package_proof.get("oneShotResponseTemplateExporter"),
            "oneShotByteAccurateResponseExporter": package_proof.get("oneShotByteAccurateResponseExporter"),
            "oneShotByteAccurateResponseProver": package_proof.get("oneShotByteAccurateResponseProver"),
            "oneShotByteAccurateResponseProof": response_proof,
            "sourceRoles": package_proof.get("sourceRoles"),
            "semanticReadiness": package_proof.get("semanticReadiness"),
            "semanticAuthorityEvaluation": package_proof.get("semanticAuthorityEvaluation"),
            "semanticAuthorityEvaluator": package_proof.get("semanticAuthorityEvaluator"),
            "candidateBuildRecipe": package_proof.get("candidateBuildRecipe"),
            "authorityGateStatus": package_proof.get("authorityGateStatus"),
            "toolchainProvenance": package_proof.get("toolchainProvenance"),
            "replayEntrypoints": package_proof.get("replayEntrypoints"),
            "claimBoundary": package_proof.get("claimBoundary") or receipt.get("claimBoundary"),
        }
        args.deliverable_out.parent.mkdir(parents=True, exist_ok=True)
        args.deliverable_out.write_text(json.dumps(deliverable, indent=2, sort_keys=True) + "\n")
        if args.bundle_out and archive_path is not None:
            bundle = create_deliverable_bundle(args.out, archive_path, deliverable_base, args.bundle_out)
            deliverable["bundle"] = {
                **bundle,
                "relativePath": relpath_or_none(Path(str(bundle["path"])), deliverable_base),
            }
            bundle_verifier = deliverable_verify_mod.verify_bundle(Path(str(bundle["path"])), args.timeout)
            deliverable["bundleVerifier"] = {
                "status": bundle_verifier.get("status"),
                "ok": bundle_verifier.get("ok"),
                "bundleManifestStatus": bundle_verifier.get("bundleManifestStatus"),
                "bundleManifestSha256": bundle_verifier.get("bundleManifestSha256"),
                "bundleManifestAuthoritySummarySha256": bundle_verifier.get("bundleManifestAuthoritySummarySha256"),
                "bundleManifestContentIdentity": bundle_verifier.get("bundleManifestContentIdentity"),
            }
            deliverable["deliverablePhase"] = "final-package-index"
            if args.bundle_verify_out:
                args.bundle_verify_out.parent.mkdir(parents=True, exist_ok=True)
                args.bundle_verify_out.write_text(json.dumps(bundle_verifier, indent=2, sort_keys=True) + "\n")
            args.deliverable_out.write_text(json.dumps(deliverable, indent=2, sort_keys=True) + "\n")
    bundle_ok = True if bundle_verifier is None else bundle_verifier.get("ok") is True
    if args.result_out:
        output["proof"] = proof
        output["byteAccurateResponseProof"] = response_proof
        output["deliverable"] = {
            "path": str(args.deliverable_out),
            "status": deliverable.get("status") if isinstance(deliverable, dict) else None,
            "authoritySummarySha256": deliverable.get("authoritySummarySha256") if isinstance(deliverable, dict) else None,
            "bundle": deliverable.get("bundle") if isinstance(deliverable, dict) else None,
            "bundleVerifier": deliverable.get("bundleVerifier") if isinstance(deliverable, dict) else None,
        } if args.deliverable_out else None
        output["bundleVerifier"] = bundle_verifier
        output["completeStatus"] = {
            "ok": receipt["status"] == "authoritative"
            and standalone["ok"]
            and archive_ok
            and bundle_ok
            and (not args.response_proof_out or (isinstance(response_proof, dict) and response_proof.get("ok") is True)),
            "package": receipt["status"],
            "standaloneVerifier": standalone.get("status"),
            "archiveVerifier": archive_verifier.get("status") if isinstance(archive_verifier, dict) else None,
            "proof": proof.get("status") if isinstance(proof, dict) else None,
            "byteAccurateResponseProof": response_proof.get("status") if isinstance(response_proof, dict) else None,
            "deliverable": deliverable.get("status") if isinstance(deliverable, dict) else None,
            "bundleVerifier": bundle_verifier.get("status") if isinstance(bundle_verifier, dict) else None,
        }
        args.result_out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
        standard_result_out = getattr(args, "standard_result_out", None)
        if standard_result_out and standard_result_out != args.result_out:
            standard_result_out.parent.mkdir(parents=True, exist_ok=True)
            standard_result_out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    return 0 if receipt["status"] == "authoritative" and standalone["ok"] and archive_ok and bundle_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
