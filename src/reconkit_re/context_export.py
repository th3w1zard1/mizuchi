"""Export arbitrary app/install trees into LLM-readable context files."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .state import atomic_write_json, now


TEXT_SUFFIXES = {
    ".bat",
    ".cfg",
    ".conf",
    ".css",
    ".csv",
    ".h",
    ".hpp",
    ".htm",
    ".html",
    ".ini",
    ".inf",
    ".js",
    ".json",
    ".log",
    ".lua",
    ".manifest",
    ".md",
    ".mf",
    ".nfo",
    ".plist",
    ".properties",
    ".ps1",
    ".py",
    ".rc",
    ".reg",
    ".rtf",
    ".sh",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
ARCHIVE_SUFFIXES = {
    ".7z",
    ".cab",
    ".dmg",
    ".exe",
    ".gz",
    ".iso",
    ".msi",
    ".mst",
    ".pkg",
    ".rar",
    ".tar",
    ".tgz",
    ".whl",
    ".xar",
    ".zip",
}
BINARY_ANALYSIS_SUFFIXES = {".dll", ".dylib", ".exe", ".so", ".xbe"}
EMBEDDED_ARCHIVE_TYPES = {"zip", "7z", "cab", "gz", "tar", "rar", "xz", "bz2", "msi"}
EMBEDDED_ARCHIVE_SIGNATURES = (
    ("zip", b"PK\x03\x04"),
    ("7z", b"7z\xbc\xaf\x27\x1c"),
    ("cab", b"MSCF"),
    ("rar", b"Rar!\x1a\x07\x00"),
    ("rar", b"Rar!\x1a\x07\x01\x00"),
    ("xz", b"\xfd7zXZ\x00"),
    ("bz2", b"BZh"),
    ("msi", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
)


@dataclass(frozen=True)
class ExportConfig:
    input_path: Path
    out_dir: Path
    output_format: str = "json"
    binary_analysis: str = "standard"
    extract_containers: bool = True
    max_files: int = 1000
    max_depth: int = 4
    max_hash_bytes: int = 512_000_000
    max_text_bytes: int = 2_000_000
    max_binary_analysis_bytes: int = 256_000_000
    max_container_members: int = 300
    strings_limit: int = 500
    max_index_text_chars: int = 2_000
    include_low_signal_members: bool = False


class ContextExporter:
    def __init__(self, config: ExportConfig) -> None:
        self.config = config
        self.root = config.input_path.expanduser().resolve()
        self.out_dir = config.out_dir.expanduser().resolve()
        self.files_dir = self.out_dir / "files"
        self.extracted_dir = self.out_dir / "extracted"
        self.rows: list[dict[str, Any]] = []
        self.visited_sha256: set[str] = set()
        self.seen_files = 0

    def run(self) -> dict[str, Any]:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.extracted_dir.mkdir(parents=True, exist_ok=True)
        self._walk(self.root, logical_prefix="", depth=0)
        tree = build_context_tree(self.rows)
        llm_index = build_llm_context_index(self.root, self.rows, self.config)
        atomic_write_json(self.out_dir / "tree.json", tree)
        atomic_write_json(self.out_dir / "LLM_CONTEXT.json", llm_index)
        (self.out_dir / "TREE.md").write_text(render_context_tree_markdown(self.root, tree), encoding="utf-8")
        (self.out_dir / "LLM_CONTEXT.md").write_text(render_llm_context_markdown(llm_index), encoding="utf-8")
        manifest = {
            "schema": "reconkit.context-export.v1",
            "createdAt": now(),
            "inputPath": str(self.root),
            "outputDirectory": str(self.out_dir),
            "outputFormat": self.config.output_format,
            "binaryAnalysis": self.config.binary_analysis,
            "extractContainers": self.config.extract_containers,
            "limits": {
                "maxFiles": self.config.max_files,
                "maxDepth": self.config.max_depth,
                "maxHashBytes": self.config.max_hash_bytes,
                "maxTextBytes": self.config.max_text_bytes,
                "maxBinaryAnalysisBytes": self.config.max_binary_analysis_bytes,
                "maxContainerMembers": self.config.max_container_members,
                "stringsLimit": self.config.strings_limit,
                "maxIndexTextChars": self.config.max_index_text_chars,
            },
            "includeLowSignalMembers": self.config.include_low_signal_members,
            "filesVisited": self.seen_files,
            "filesExported": len(self.rows),
            "truncated": self.seen_files >= self.config.max_files,
            "tree": {
                "json": "tree.json",
                "markdown": "TREE.md",
            },
            "llmContext": {
                "json": "LLM_CONTEXT.json",
                "markdown": "LLM_CONTEXT.md",
            },
            "entries": self.rows,
        }
        atomic_write_json(self.out_dir / "manifest.json", manifest)
        return manifest

    def _walk(self, path: Path, *, logical_prefix: str, depth: int) -> None:
        if self.seen_files >= self.config.max_files:
            return
        if path.is_file():
            self._export_file(path, logical_prefix=logical_prefix, depth=depth)
            return
        if not path.is_dir():
            return
        for child in sorted(path.iterdir(), key=child_priority):
            if self.seen_files >= self.config.max_files:
                return
            child_prefix = f"{logical_prefix}/{child.name}" if logical_prefix else child.name
            if child.is_dir():
                self._walk(child, logical_prefix=child_prefix, depth=depth)
            else:
                self._export_file(child, logical_prefix=child_prefix, depth=depth)

    def _export_file(self, path: Path, *, logical_prefix: str, depth: int) -> None:
        self.seen_files += 1
        stat = path.stat()
        digest_info = digest_file(path, stat.st_size, self.config.max_hash_bytes)
        digest = str(digest_info["sha256"])
        rel = logical_prefix or path.name
        kind = classify_file(path)
        export_path, payload = self._write_surrogate(path, rel, kind, digest, stat.st_size, digest_info)
        row: dict[str, Any] = {
            "path": rel,
            "sourcePath": str(path),
            "size": stat.st_size,
            "sha256": digest,
            "hashScope": digest_info["scope"],
            "bytesHashed": digest_info["bytesHashed"],
            "kind": kind,
            "mime": mimetypes.guess_type(path.name)[0],
            "export": str(export_path.relative_to(self.out_dir)),
            "llmSummary": summarize_file_payload(payload, self.config.max_index_text_chars),
        }
        self.rows.append(row)
        if (
            self.config.extract_containers
            and depth < self.config.max_depth
            and digest not in self.visited_sha256
            and should_try_extract(path, kind)
        ):
            self.visited_sha256.add(digest)
            extraction = self._extract_container(path, rel, digest, depth)
            if extraction:
                row["extraction"] = extraction

    def _write_surrogate(self, path: Path, rel: str, kind: str, digest: str, size: int, digest_info: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
        payload = build_file_payload(path, rel, kind, digest, size, self.config)
        payload["hashScope"] = digest_info["scope"]
        payload["bytesHashed"] = digest_info["bytesHashed"]
        out_path = self.files_dir / safe_export_name(rel, self.config.output_format)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if self.config.output_format == "md":
            out_path.write_text(render_markdown(payload), encoding="utf-8")
        else:
            out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return out_path, payload

    def _extract_container(self, path: Path, rel: str, digest: str, depth: int) -> dict[str, Any] | None:
        if shutil.which("7z") is None:
            return {"status": "skipped", "reason": "7z not found"}
        listing = run_command(["7z", "l", "-slt", str(path)], timeout=60, max_chars=120_000)
        extract_root = self.extracted_dir / safe_component(f"{Path(rel).name}-{digest[:12]}")
        extract_root.mkdir(parents=True, exist_ok=True)
        result = run_command(["7z", "x", "-y", f"-o{extract_root}", str(path)], timeout=180)
        fallback: dict[str, Any] | None = None
        if result["returnCode"] != 0:
            fallback = try_extract_embedded_archive(path, extract_root, listing)
            if fallback and fallback.get("returnCode") == 0:
                result = {
                    "command": result["command"],
                    "returnCode": 0,
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                    "truncated": result.get("truncated", False),
                }
        status = "complete" if result["returnCode"] == 0 else "partial" if has_extracted_files(extract_root) else "failed"
        extraction = {
            "status": status,
            "tool": "7z",
            "directory": str(extract_root.relative_to(self.out_dir)),
            "returnCode": result["returnCode"],
            "stdoutTail": result["stdout"][-2000:],
            "stderrTail": result["stderr"][-2000:],
        }
        if listing["returnCode"] != 0:
            extraction["listingWarning"] = {
                "returnCode": listing["returnCode"],
                "stdoutTail": listing["stdout"][-2000:],
                "stderrTail": listing["stderr"][-2000:],
            }
        if fallback:
            extraction["embeddedArchiveFallback"] = fallback
        specialized = try_specialized_extractors(path, extract_root, listing)
        if specialized:
            extraction["specializedExtractors"] = specialized
            if status == "failed" and any(int(item.get("extractedFiles") or 0) > 0 for item in specialized):
                status = "partial"
                extraction["status"] = status
        if status in {"complete", "partial"}:
            before = len(self.rows)
            selection = self._walk_extracted_members(
                extract_root,
                logical_prefix=f"{rel}::extracted",
                depth=depth + 1,
                limit=self.config.max_container_members,
            )
            extraction["exportedMembers"] = len(self.rows) - before
            extraction["totalMembers"] = selection["totalMembers"]
            extraction["lowSignalMembers"] = selection["lowSignalMembers"]
            extraction["skippedMembers"] = max(0, selection["totalMembers"] - selection["selectedCount"])
            extraction["selectedMembersPreview"] = selection["selectedPreview"]
        return extraction

    def _walk_extracted_members(self, path: Path, *, logical_prefix: str, depth: int, limit: int) -> dict[str, Any]:
        files = [child for child in path.rglob("*") if child.is_file()]
        selection = select_extracted_members(
            files,
            base=path,
            limit=limit,
            include_low_signal=self.config.include_low_signal_members,
        )
        for child in selection["selected"]:
            if self.seen_files >= self.config.max_files:
                break
            rel = child.relative_to(path)
            child_prefix = f"{logical_prefix}/{rel.as_posix()}"
            self._export_file(child, logical_prefix=child_prefix, depth=depth)
        return {
            "totalMembers": len(files),
            "lowSignalMembers": selection["lowSignalCount"],
            "selectedCount": len(selection["selected"]),
            "selectedPreview": [child.relative_to(path).as_posix() for child in selection["selected"][:40]],
        }


def export_context(config: ExportConfig) -> dict[str, Any]:
    return ContextExporter(config).run()


def classify_file(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        with path.open("rb") as fh:
            head = fh.read(8)
    except OSError:
        return "unreadable"
    if head.startswith(b"MZ"):
        return "pe"
    if head.startswith(b"\x7fELF"):
        return "elf"
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\xca\xfe\xba\xbe"):
        return "jvm-class"
    if suffix in TEXT_SUFFIXES:
        return "text"
    if suffix in ARCHIVE_SUFFIXES:
        return "container"
    if is_probably_text(path):
        return "text"
    return "binary"


def should_try_extract(path: Path, kind: str) -> bool:
    return kind in {"container", "pe", "zip", "pdf"} or path.suffix.lower() in ARCHIVE_SUFFIXES


def try_extract_embedded_archive(path: Path, extract_root: Path, listing: dict[str, Any]) -> dict[str, Any] | None:
    if shutil.which("7z") is None:
        return None
    candidates = embedded_archive_candidates(listing, source=path)
    if not candidates:
        return {"status": "skipped", "reason": "no embedded archive candidates", "returnCode": -1}
    attempts: list[dict[str, Any]] = []
    successes: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="reconkit-embedded-carve-") as tmp:
        temp_root = Path(tmp)
        for index, candidate in enumerate(candidates, start=1):
            carved_name = f"embedded-{index:02d}.{candidate['type']}"
            carved_path = temp_root / carved_name
            offset = int(candidate["offset"])
            if offset < 0 or offset >= path.stat().st_size:
                attempts.append({"type": candidate["type"], "offset": offset, "status": "skipped", "reason": "offset outside file"})
                continue
            if not archive_signature_matches(path, offset, str(candidate["type"])):
                attempts.append(
                    {
                        "type": candidate["type"],
                        "offset": offset,
                        "source": candidate.get("source"),
                        "status": "skipped",
                        "reason": "signature mismatch",
                    }
                )
                continue
            carve_archive(path, carved_path, offset)
            target_dir = extract_root / safe_component(f"embedded-{index:02d}-{candidate['type']}")
            target_dir.mkdir(parents=True, exist_ok=True)
            result = run_command(["7z", "x", "-y", f"-o{target_dir}", str(carved_path)], timeout=180)
            extracted_files = count_files(target_dir)
            attempts.append(
                {
                    "type": candidate["type"],
                    "offset": offset,
                    "source": candidate.get("source"),
                    "returnCode": result["returnCode"],
                    "extractedFiles": extracted_files,
                    "stdoutTail": result["stdout"][-1000:],
                    "stderrTail": result["stderr"][-1000:],
                }
            )
            if result["returnCode"] == 0 or extracted_files > 0:
                successes.append(
                    {
                        "type": candidate["type"],
                        "offset": offset,
                        "source": candidate.get("source"),
                        "directory": str(target_dir.name),
                        "returnCode": result["returnCode"],
                        "extractedFiles": extracted_files,
                        "stdoutTail": result["stdout"][-2000:],
                        "stderrTail": result["stderr"][-2000:],
                    }
                )
    if successes:
        status = "complete" if all(item["returnCode"] == 0 for item in successes) else "partial"
        return {
            "status": status,
            "returnCode": 0 if status == "complete" else 1,
            "candidates": len(candidates),
            "successfulCandidates": len(successes),
            "successes": successes,
            "attempts": attempts,
        }
    return {
        "status": "failed",
        "reason": "all embedded archive candidates failed",
        "returnCode": 1,
        "candidates": len(candidates),
        "attempts": attempts[:20],
    }


def try_specialized_extractors(path: Path, extract_root: Path, listing: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    suffix = path.suffix.lower()
    listing_text = f"{listing.get('stdout') or ''}\n{listing.get('stderr') or ''}"
    if suffix == ".exe" and shutil.which("innoextract") and looks_like_inno_setup(path, listing_text):
        target_dir = extract_root / "specialized-innoextract"
        target_dir.mkdir(parents=True, exist_ok=True)
        result = run_command(["innoextract", "-d", str(target_dir), str(path)], timeout=300, max_chars=120_000)
        results.append(
            {
                "tool": "innoextract",
                "status": "complete" if result["returnCode"] == 0 else "failed",
                "directory": str(target_dir.name),
                "returnCode": result["returnCode"],
                "extractedFiles": count_files(target_dir),
                "stdoutTail": result["stdout"][-4000:],
                "stderrTail": result["stderr"][-4000:],
            }
        )
    if suffix in {".msi", ".mst"} and shutil.which("msiextract"):
        target_dir = extract_root / "specialized-msiextract"
        target_dir.mkdir(parents=True, exist_ok=True)
        result = run_command(["msiextract", "-C", str(target_dir), str(path)], timeout=300, max_chars=120_000)
        results.append(
            {
                "tool": "msiextract",
                "status": "complete" if result["returnCode"] == 0 else "failed",
                "directory": str(target_dir.name),
                "returnCode": result["returnCode"],
                "extractedFiles": count_files(target_dir),
                "stdoutTail": result["stdout"][-4000:],
                "stderrTail": result["stderr"][-4000:],
            }
        )
    if suffix in {".pkg", ".dmg"} and shutil.which("unar"):
        target_dir = extract_root / "specialized-unar"
        target_dir.mkdir(parents=True, exist_ok=True)
        result = run_command(["unar", "-force-overwrite", "-output-directory", str(target_dir), str(path)], timeout=300, max_chars=120_000)
        results.append(
            {
                "tool": "unar",
                "status": "complete" if result["returnCode"] == 0 else "failed",
                "directory": str(target_dir.name),
                "returnCode": result["returnCode"],
                "extractedFiles": count_files(target_dir),
                "stdoutTail": result["stdout"][-4000:],
                "stderrTail": result["stderr"][-4000:],
            }
        )
    return results


def looks_like_inno_setup(path: Path, listing_text: str) -> bool:
    if "inno setup" in listing_text.lower():
        return True
    result = run_command(["strings", "-a", "-n", "8", str(path)], timeout=30, max_chars=200_000) if shutil.which("strings") else {"stdout": ""}
    return "inno setup" in str(result.get("stdout") or "").lower()


def embedded_archive_candidates(listing: dict[str, Any], *, source: Path | None = None) -> list[dict[str, Any]]:
    stdout = str(listing.get("stdout") or "")
    candidates: list[dict[str, Any]] = []
    stream_base_by_path: dict[str, int] = {}
    for block in parse_7z_slt_blocks(stdout):
        path_name = str(block.get("Path") or "")
        archive_type = str(block.get("Type") or "").lower()
        offset = parse_int(block.get("Offset"))
        if path_name.startswith("[") and path_name.endswith("]") and offset is not None and archive_type not in EMBEDDED_ARCHIVE_TYPES:
            stream_base_by_path[path_name] = offset
        if archive_type not in EMBEDDED_ARCHIVE_TYPES or offset is None:
            continue
        candidates.append({"type": archive_type, "offset": offset, "source": "7z-listing-relative"})
        base = stream_base_by_path.get(path_name)
        if base is not None:
            candidates.append({"type": archive_type, "offset": base + offset, "source": "7z-listing-layered"})
    if source is not None:
        candidates.extend(scan_embedded_archive_signatures(source))
    return dedupe_archive_candidates(candidates)


def parse_7z_slt_blocks(stdout: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in stdout.splitlines():
        if not line.strip():
            if current:
                blocks.append(current)
                current = {}
            continue
        if " = " not in line:
            continue
        key, value = line.split(" = ", 1)
        if key.strip() == "Path" and "Path" in current:
            blocks.append(current)
            current = {}
        current[key.strip()] = value.strip()
    if current:
        blocks.append(current)
    return blocks


def parse_int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def scan_embedded_archive_signatures(path: Path, *, max_hits: int = 100) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    overlap = max(len(signature) for _archive_type, signature in EMBEDDED_ARCHIVE_SIGNATURES) - 1
    tail = b""
    base_offset = 0
    with path.open("rb") as fh:
        while len(hits) < max_hits:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            window = tail + chunk
            window_base = base_offset - len(tail)
            for archive_type, signature in EMBEDDED_ARCHIVE_SIGNATURES:
                start = 0
                while len(hits) < max_hits:
                    index = window.find(signature, start)
                    if index < 0:
                        break
                    absolute = window_base + index
                    key = (archive_type, absolute)
                    if absolute > 0 and key not in seen:
                        seen.add(key)
                        hits.append({"type": archive_type, "offset": absolute, "source": "signature-scan"})
                    start = index + 1
            tail = window[-overlap:] if overlap else b""
            base_offset += len(chunk)
    return sorted(hits, key=lambda candidate: int(candidate["offset"]))


def dedupe_archive_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for candidate in candidates:
        archive_type = str(candidate.get("type") or "").lower()
        offset = parse_int(candidate.get("offset"))
        if archive_type not in EMBEDDED_ARCHIVE_TYPES or offset is None:
            continue
        key = (archive_type, offset)
        if key in seen:
            continue
        seen.add(key)
        deduped.append({"type": archive_type, "offset": offset, "source": candidate.get("source")})
    return deduped


def archive_signature_matches(source: Path, offset: int, archive_type: str) -> bool:
    signatures = [signature for candidate_type, signature in EMBEDDED_ARCHIVE_SIGNATURES if candidate_type == archive_type]
    if not signatures:
        return True
    with source.open("rb") as fh:
        fh.seek(offset)
        head = fh.read(max(len(signature) for signature in signatures))
    return any(head.startswith(signature) for signature in signatures)


def carve_archive(source: Path, target: Path, offset: int) -> None:
    with source.open("rb") as src, target.open("wb") as dst:
        src.seek(offset)
        shutil.copyfileobj(src, dst, length=1024 * 1024)


def has_extracted_files(path: Path) -> bool:
    return any(child.is_file() for child in path.rglob("*"))


def count_files(path: Path) -> int:
    return sum(1 for child in path.rglob("*") if child.is_file())


def child_priority(path: Path) -> tuple[int, str]:
    name = path.name.lower()
    suffix = path.suffix.lower()
    path_lower = str(path).lower().replace("\\", "/")
    if path.is_dir():
        if name in {".rsrc", "resources", "resource", "scripts", "docs", "documentation"}:
            return (0, name)
        if name in {"cursor", "icon", "group_icon", "group_cursor"}:
            return (8, name)
        return (3, name)
    if is_low_signal_member_path(path_lower):
        return (9, path_lower)
    if name in {"version.txt", "packageinfo", "distribution", "setup.ini", "manifest", "installscript"}:
        return (0, name)
    if name in {".text", ".textv", ".bind", ".rdata", ".data", ".idata", ".pdata", ".xdata", ".reloc"}:
        return (1, name)
    if suffix in {".txt", ".xml", ".json", ".ini", ".manifest", ".plist", ".yaml", ".yml", ".md"}:
        return (1, name)
    if suffix in {".msi", ".pkg", ".zip", ".cab", ".7z", ".exe"}:
        return (2, name)
    if name in {"certificate"}:
        return (4, name)
    if suffix in {".ico", ".icns", ".png", ".jpg", ".jpeg", ".bmp"} or "cursor" in path_lower:
        return (9, name)
    return (5, name)


def select_extracted_members(
    files: list[Path],
    *,
    base: Path,
    limit: int,
    include_low_signal: bool,
) -> dict[str, Any]:
    prioritized = sorted(files, key=lambda child: extracted_member_priority(base, child))
    selected: list[Path] = []
    low_signal_count = 0
    for child in prioritized:
        rel = child.relative_to(base).as_posix().lower()
        low_signal = is_low_signal_member_path(rel)
        if low_signal:
            low_signal_count += 1
            if not include_low_signal:
                continue
        if len(selected) >= limit:
            break
        selected.append(child)
    return {"selected": selected, "lowSignalCount": low_signal_count}


def extracted_member_priority(base: Path, child: Path) -> tuple[int, str]:
    rel = child.relative_to(base).as_posix().lower()
    suffix = child.suffix.lower()
    name = child.name.lower()
    if is_low_signal_member_path(rel):
        return (9, rel)
    if rel.endswith("version.txt") or rel.endswith("setup.ini") or rel.endswith("manifest"):
        return (0, rel)
    if name in {".text", ".textv", ".bind", ".rdata", ".data", ".idata", ".pdata", ".xdata", ".reloc"}:
        return (1, rel)
    if rel.startswith(".rsrc/version") or rel.endswith("/version.txt"):
        return (1, rel)
    if suffix in {".txt", ".xml", ".json", ".ini", ".manifest", ".plist", ".yaml", ".yml", ".md", ".cfg", ".log"}:
        return (2, rel)
    if suffix in {".exe", ".dll", ".so", ".dylib", ".pkg", ".msi", ".cab", ".zip", ".7z"}:
        return (3, rel)
    return (5, rel)


def is_low_signal_member_path(value: str) -> bool:
    lowered = value.replace("\\", "/").lower()
    if re.search(r"(?:^|/)\.rsrc/(?:[^/]+/)?(?:cursor|icon|group_cursor|group_icon|bitmap)(?:/|$)", lowered):
        return True
    low_signal_fragments = (
        ".rsrc/cursor/",
        ".rsrc/icon/",
        ".rsrc/group_cursor/",
        ".rsrc/group_icon/",
        ".rsrc/bitmap/",
        "/.rsrc/cursor/",
        "/.rsrc/icon/",
        "/.rsrc/group_cursor/",
        "/.rsrc/group_icon/",
        "/.rsrc/bitmap/",
        "__macosx/",
        "/thumbs.db",
    )
    return any(fragment in lowered for fragment in low_signal_fragments)


def build_context_tree(rows: list[dict[str, Any]]) -> dict[str, Any]:
    root: dict[str, Any] = {"schema": "reconkit.context-tree.v1", "name": ".", "kind": "directory", "children": {}}
    for row in rows:
        parts = [part for part in str(row.get("path") or "").split("/") if part]
        if not parts:
            parts = [Path(str(row.get("sourcePath") or "root")).name or "root"]
        cursor = root
        for part in parts[:-1]:
            children = cursor.setdefault("children", {})
            cursor = children.setdefault(part, {"name": part, "kind": "directory", "children": {}})
        leaf_name = parts[-1]
        cursor.setdefault("children", {})[leaf_name] = {
            "name": leaf_name,
            "kind": row.get("kind"),
            "path": row.get("path"),
            "sourcePath": row.get("sourcePath"),
            "size": row.get("size"),
            "sha256": row.get("sha256"),
            "export": row.get("export"),
            "extraction": summarize_extraction(row.get("extraction")),
        }
    return freeze_tree(root)


def summarize_file_payload(payload: dict[str, Any], max_text_chars: int) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "kind": payload.get("kind"),
        "exportedPath": payload.get("path"),
    }
    text = str(payload.get("text") or "")
    if text:
        summary["textPreview"] = bounded_text(text, max_text_chars)
        summary["textTruncated"] = len(text) > max_text_chars
    strings = payload.get("strings")
    if isinstance(strings, list):
        summary["stringsPreview"] = [str(item) for item in strings[: min(40, len(strings))]]
        summary["stringsCountInSurrogate"] = len(strings)
    analysis = payload.get("analysis")
    if isinstance(analysis, dict):
        summary["analysis"] = summarize_analysis(analysis)
    container = payload.get("container")
    if isinstance(container, dict):
        summary["container"] = summarize_container(container)
    return {key: value for key, value in summary.items() if value not in (None, "", [], {})}


def bounded_text(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n[truncated]"


def summarize_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    file_info = analysis.get("file")
    if isinstance(file_info, dict):
        result["file"] = first_nonempty_line(str(file_info.get("stdout") or ""))
    strings = analysis.get("strings")
    if isinstance(strings, list):
        result["stringsPreview"] = [str(item) for item in strings[:40]]
        result["stringsCountInSurrogate"] = len(strings)
    head_strings = analysis.get("headStrings")
    if isinstance(head_strings, list):
        result["headStrings"] = [str(item) for item in head_strings[:40]]
        result["headStringsCountInSurrogate"] = len(head_strings)
    if analysis.get("headBytesScanned") is not None:
        result["headBytesScanned"] = analysis.get("headBytesScanned")
    for key in ("sevenZipMetadata", "objdumpHeaders", "rabinInfo", "rabinImports", "rabinSections", "exiftool"):
        value = analysis.get(key)
        if isinstance(value, dict):
            result[key] = {
                "returnCode": value.get("returnCode"),
                "stdoutPreview": bounded_text(str(value.get("stdout") or ""), 1200),
                "stderrPreview": bounded_text(str(value.get("stderr") or ""), 600),
                "truncated": value.get("truncated"),
            }
    if analysis.get("status"):
        result["status"] = analysis.get("status")
        result["reason"] = analysis.get("reason")
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def summarize_container(container: dict[str, Any]) -> dict[str, Any]:
    listing = container.get("listing")
    result: dict[str, Any] = {"status": container.get("status")}
    if isinstance(listing, dict):
        result["returnCode"] = listing.get("returnCode")
        result["listingPreview"] = bounded_text(str(listing.get("stdout") or ""), 1600)
        result["stderrPreview"] = bounded_text(str(listing.get("stderr") or ""), 600)
    if container.get("reason"):
        result["reason"] = container.get("reason")
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def first_nonempty_line(value: str) -> str:
    for line in value.splitlines():
        if line.strip():
            return line.strip()
    return ""


def build_llm_context_index(input_path: Path, rows: list[dict[str, Any]], config: ExportConfig) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for row in rows:
        item = {
            "path": row.get("path"),
            "kind": row.get("kind"),
            "size": row.get("size"),
            "sha256": row.get("sha256"),
            "hashScope": row.get("hashScope"),
            "bytesHashed": row.get("bytesHashed"),
            "mime": row.get("mime"),
            "surrogate": row.get("export"),
            "summary": row.get("llmSummary"),
            "extraction": summarize_extraction(row.get("extraction")),
        }
        entries.append({key: value for key, value in item.items() if value not in (None, "", [], {})})
    return {
        "schema": "reconkit.llm-context-index.v1",
        "createdAt": now(),
        "inputPath": str(input_path),
        "outputFormat": config.output_format,
        "entryCount": len(entries),
        "entries": entries,
        "claimBoundary": "This is an LLM-readable surrogate index over files, resources, strings, metadata, and extracted containers; it is not semantic source parity proof.",
    }


def summarize_extraction(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "status": value.get("status"),
        "tool": value.get("tool"),
        "directory": value.get("directory"),
        "exportedMembers": value.get("exportedMembers"),
        "reason": value.get("reason"),
        "specializedExtractors": [
            {
                "tool": item.get("tool"),
                "status": item.get("status"),
                "extractedFiles": item.get("extractedFiles"),
            }
            for item in value.get("specializedExtractors", [])
            if isinstance(item, dict)
        ],
    }


def freeze_tree(node: dict[str, Any]) -> dict[str, Any]:
    children = node.get("children")
    if isinstance(children, dict):
        frozen_children = [freeze_tree(child) for _name, child in sorted(children.items(), key=lambda item: item[0].lower())]
        node = {**node, "children": frozen_children, "childCount": len(frozen_children)}
    return node


def render_context_tree_markdown(input_path: Path, tree: dict[str, Any]) -> str:
    lines = [
        "# ReconstructKit Context Tree",
        "",
        f"Input: `{input_path}`",
        "",
        "This is a plaintext hierarchy of exported files and extracted container members. Each file points at its JSON/Markdown surrogate.",
        "",
        "## Tree",
        "",
    ]
    for child in tree.get("children", []):
        render_tree_node(child, lines, depth=0)
    lines.append("")
    return "\n".join(lines)


def render_tree_node(node: dict[str, Any], lines: list[str], *, depth: int) -> None:
    indent = "  " * depth
    children = node.get("children") if isinstance(node.get("children"), list) else []
    if children:
        lines.append(f"{indent}- `{node.get('name')}/`")
        for child in children:
            render_tree_node(child, lines, depth=depth + 1)
        return
    details = [str(node.get("kind") or "file")]
    if node.get("size") is not None:
        details.append(f"{node['size']} bytes")
    if node.get("export"):
        details.append(f"export: `{node['export']}`")
    extraction = node.get("extraction")
    if isinstance(extraction, dict) and extraction.get("status"):
        details.append(f"extraction: `{extraction['status']}`")
    lines.append(f"{indent}- `{node.get('name')}` ({'; '.join(details)})")


def render_llm_context_markdown(index: dict[str, Any]) -> str:
    lines = [
        "# ReconstructKit LLM Context",
        "",
        f"Input: `{index.get('inputPath')}`",
        f"Entries: `{index.get('entryCount')}`",
        "",
        f"Claim boundary: {index.get('claimBoundary')}",
        "",
        "## Entries",
        "",
    ]
    for entry in index.get("entries", []):
        path = entry.get("path")
        lines.append(f"### `{path}`")
        lines.append("")
        lines.append(f"- Kind: `{entry.get('kind')}`")
        lines.append(f"- Size: `{entry.get('size')}`")
        if entry.get("mime"):
            lines.append(f"- MIME: `{entry.get('mime')}`")
        if entry.get("sha256"):
            lines.append(f"- SHA256: `{entry.get('sha256')}`")
        if entry.get("hashScope") != "full":
            lines.append(f"- Hash scope: `{entry.get('hashScope')}` over `{entry.get('bytesHashed')}` bytes")
        if entry.get("surrogate"):
            lines.append(f"- Surrogate: `{entry.get('surrogate')}`")
        extraction = entry.get("extraction")
        if isinstance(extraction, dict) and extraction.get("status"):
            lines.append(f"- Extraction: `{extraction.get('status')}`")
        lines.append("")
        render_summary_markdown(entry.get("summary"), lines)
    return "\n".join(lines)


def render_summary_markdown(summary: Any, lines: list[str]) -> None:
    if not isinstance(summary, dict):
        return
    text = summary.get("textPreview")
    if text:
        lines.extend(["#### Text Preview", "", "```text", str(text), "```", ""])
    strings = summary.get("stringsPreview")
    if isinstance(strings, list) and strings:
        lines.extend(["#### Strings", ""])
        lines.extend(f"- `{line}`" for line in strings[:40])
        lines.append("")
    analysis = summary.get("analysis")
    if isinstance(analysis, dict) and analysis:
        lines.extend(["#### Analysis Summary", "", "```json", json.dumps(analysis, indent=2, sort_keys=True), "```", ""])
    container = summary.get("container")
    if isinstance(container, dict) and container:
        lines.extend(["#### Container Summary", "", "```json", json.dumps(container, indent=2, sort_keys=True), "```", ""])


def build_file_payload(path: Path, rel: str, kind: str, digest: str, size: int, config: ExportConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "reconkit.context-file.v1",
        "path": rel,
        "sourcePath": str(path),
        "kind": kind,
        "size": size,
        "sha256": digest,
    }
    if kind == "text":
        payload["text"] = read_text_preview(path, config.max_text_bytes)
    elif kind == "pdf":
        payload["text"] = pdf_text(path, config.max_text_bytes)
    elif kind in {"pe", "elf"}:
        if size > config.max_binary_analysis_bytes:
            payload["analysis"] = limited_binary_analysis(path, config, size)
        else:
            payload["analysis"] = binary_analysis(path, config)
    elif kind in {"zip", "container"}:
        payload["container"] = container_listing(path)
    elif kind == "jvm-class":
        class_dump = jvm_class_text(path, config.max_text_bytes)
        if class_dump:
            payload["text"] = class_dump["text"]
            payload["analysis"] = class_dump["analysis"]
        else:
            payload["strings"] = extract_strings(path, config.strings_limit)
    else:
        if size > config.max_binary_analysis_bytes:
            payload["analysis"] = limited_binary_analysis(path, config, size)
        else:
            payload["strings"] = extract_strings(path, config.strings_limit)
    return payload


def binary_analysis(path: Path, config: ExportConfig) -> dict[str, Any]:
    analysis = {
        "file": run_command(["file", "-b", str(path)], timeout=15),
        "strings": extract_strings(path, config.strings_limit),
    }
    if shutil.which("exiftool") is not None:
        analysis["exiftool"] = run_command(["exiftool", "-j", "-charset", "filename=UTF8", str(path)], timeout=30, max_chars=80_000)
    mode = config.binary_analysis
    if shutil.which("7z") is not None and (path.suffix.lower() in ARCHIVE_SUFFIXES.union(BINARY_ANALYSIS_SUFFIXES) or classify_file(path) in {"pe", "elf", "container", "zip"}):
        analysis["sevenZipMetadata"] = run_command(["7z", "l", "-slt", str(path)], timeout=30, max_chars=40_000)
    if mode in {"standard", "deep"} and shutil.which("objdump") is not None and (path.suffix.lower() in BINARY_ANALYSIS_SUFFIXES or classify_file(path) == "pe"):
        analysis["objdumpHeaders"] = run_command(["objdump", "-x", str(path)], timeout=20, max_chars=60_000)
    if mode == "deep" and shutil.which("rabin2") is not None and (path.suffix.lower() in BINARY_ANALYSIS_SUFFIXES or classify_file(path) == "pe"):
        analysis["rabinInfo"] = run_command(["rabin2", "-I", str(path)], timeout=20, max_chars=40_000)
        analysis["rabinImports"] = run_command(["rabin2", "-i", str(path)], timeout=20, max_chars=40_000)
        analysis["rabinSections"] = run_command(["rabin2", "-S", str(path)], timeout=20, max_chars=40_000)
    return analysis


def limited_binary_analysis(path: Path, config: ExportConfig, size: int) -> dict[str, Any]:
    analysis = {
        "status": "bounded",
        "reason": "file exceeds maxBinaryAnalysisBytes; skipped expensive whole-file binary tools",
        "size": size,
        "maxBinaryAnalysisBytes": config.max_binary_analysis_bytes,
        "file": run_command(["file", "-b", str(path)], timeout=15),
    }
    if config.strings_limit > 0:
        preview_bytes = min(size, max(0, config.max_binary_analysis_bytes), 256 * 1024)
        with path.open("rb") as fh:
            analysis["headStrings"] = extract_strings_from_bytes(fh.read(preview_bytes), config.strings_limit)
        analysis["headBytesScanned"] = preview_bytes
    return analysis


def container_listing(path: Path) -> dict[str, Any]:
    if shutil.which("7z") is None:
        return {"status": "skipped", "reason": "7z not found"}
    result = run_command(["7z", "l", str(path)], timeout=60, max_chars=80_000)
    return {"status": "complete" if result["returnCode"] == 0 else "failed", "listing": result}


def pdf_text(path: Path, max_bytes: int) -> str:
    if shutil.which("pdftotext") is None:
        return ""
    with tempfile.TemporaryDirectory(prefix="reconkit-pdf-") as tmp:
        out = Path(tmp) / "out.txt"
        result = run_command(["pdftotext", "-layout", str(path), str(out)], timeout=60)
        if result["returnCode"] != 0 or not out.exists():
            return ""
        return out.read_text(encoding="utf-8", errors="replace")[:max_bytes]


def jvm_class_text(path: Path, max_bytes: int) -> dict[str, Any] | None:
    if shutil.which("javap") is None:
        return None
    result = run_command(["javap", "-verbose", "-p", "-c", str(path)], timeout=30, max_chars=max_bytes)
    if result["returnCode"] != 0:
        return {
            "text": "",
            "analysis": {
                "tool": "javap",
                "status": "failed",
                "returnCode": result["returnCode"],
                "stderrTail": result["stderr"][-2000:],
            },
        }
    return {
        "text": result["stdout"],
        "analysis": {
            "tool": "javap",
            "status": "complete",
            "returnCode": result["returnCode"],
            "truncated": result.get("truncated", False),
        },
    }


def extract_strings(path: Path, limit: int) -> list[str]:
    if shutil.which("strings") is None:
        return []
    result = run_command(["strings", "-a", "-n", "5", str(path)], timeout=30, max_chars=200_000)
    if result["returnCode"] != 0:
        return []
    lines = []
    seen = set()
    for line in result["stdout"].splitlines():
        text = line.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        lines.append(text)
        if len(lines) >= limit:
            break
    return lines


def extract_strings_from_bytes(data: bytes, limit: int, *, min_len: int = 5) -> list[str]:
    pattern = re.compile(rb"[\x20-\x7e]{" + str(min_len).encode("ascii") + rb",}")
    lines: list[str] = []
    seen: set[str] = set()
    for match in pattern.finditer(data):
        text = match.group(0).decode("ascii", errors="replace").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        lines.append(text)
        if len(lines) >= limit:
            break
    return lines


def read_text_preview(path: Path, max_bytes: int) -> str:
    data = path.read_bytes()[:max_bytes]
    return decode_text_bytes(data)


def is_probably_text(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            data = fh.read(8192)
    except OSError:
        return False
    if b"\0" in data:
        even_nuls = sum(1 for index in range(0, len(data), 2) if data[index] == 0)
        odd_nuls = sum(1 for index in range(1, len(data), 2) if data[index] == 0)
        half = max(1, len(data) // 2)
        if even_nuls / half < 0.35 and odd_nuls / half < 0.35:
            return False
    if not data:
        return True
    printable = sum(1 for b in data if b in b"\n\r\t" or 32 <= b < 127)
    return printable / len(data) > 0.85


def decode_text_bytes(data: bytes) -> str:
    if data.startswith(b"\xff\xfe") or likely_utf16le(data):
        return data.decode("utf-16le", errors="replace").lstrip("\ufeff")
    if data.startswith(b"\xfe\xff") or likely_utf16be(data):
        return data.decode("utf-16be", errors="replace").lstrip("\ufeff")
    return data.decode("utf-8", errors="replace")


def likely_utf16le(data: bytes) -> bool:
    if len(data) < 8:
        return False
    odd = data[1::2]
    even = data[0::2]
    return odd.count(0) / max(1, len(odd)) > 0.35 and even.count(0) / max(1, len(even)) < 0.15


def likely_utf16be(data: bytes) -> bool:
    if len(data) < 8:
        return False
    odd = data[1::2]
    even = data[0::2]
    return even.count(0) / max(1, len(even)) > 0.35 and odd.count(0) / max(1, len(odd)) < 0.15


def digest_file(path: Path, size: int, max_hash_bytes: int) -> dict[str, Any]:
    digest = hashlib.sha256()
    bytes_hashed = 0
    limit = max_hash_bytes if max_hash_bytes > 0 else size
    with path.open("rb") as fh:
        while bytes_hashed < limit:
            chunk = fh.read(min(1024 * 1024, limit - bytes_hashed))
            if not chunk:
                break
            digest.update(chunk)
            bytes_hashed += len(chunk)
    scope = "full" if bytes_hashed >= size else "prefix"
    return {"sha256": digest.hexdigest(), "scope": scope, "bytesHashed": bytes_hashed}


def sha256_file(path: Path) -> str:
    return str(digest_file(path, path.stat().st_size, path.stat().st_size)["sha256"])


def run_command(command: list[str], *, timeout: int, max_chars: int = 20_000) -> dict[str, Any]:
    try:
        proc = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
        return {
            "command": command,
            "returnCode": proc.returncode,
            "stdout": proc.stdout[:max_chars],
            "stderr": proc.stderr[:max_chars],
            "truncated": len(proc.stdout) > max_chars or len(proc.stderr) > max_chars,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "returnCode": -1,
            "stdout": (exc.stdout or "")[:max_chars],
            "stderr": (exc.stderr or "")[:max_chars],
            "truncated": True,
            "timedOut": True,
        }
    except Exception as exc:
        return {"command": command, "returnCode": -1, "stdout": "", "stderr": str(exc), "truncated": False}


def safe_component(value: str) -> str:
    value = value.replace(os.sep, "_")
    return re.sub(r"[^A-Za-z0-9._+=@-]+", "_", value).strip("._") or "file"


def safe_export_name(rel: str, output_format: str) -> Path:
    suffix = ".md" if output_format == "md" else ".json"
    parts = [safe_component(part) for part in Path(rel).parts if part not in {"", "."}]
    if not parts:
        parts = ["root"]
    return Path(*parts).with_suffix(Path(parts[-1]).suffix + suffix)


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# {payload['path']}",
        "",
        f"- Kind: `{payload['kind']}`",
        f"- Size: `{payload['size']}`",
        f"- SHA256: `{payload['sha256']}`",
        "",
    ]
    if payload.get("text"):
        lines.extend(["## Text", "", "```text", str(payload["text"]), "```", ""])
    if payload.get("strings"):
        lines.extend(["## Strings", ""])
        lines.extend(f"- `{line}`" for line in payload["strings"])
        lines.append("")
    if payload.get("analysis") or payload.get("container"):
        lines.extend(["## Structured Data", "", "```json", json.dumps({k: v for k, v in payload.items() if k not in {"text"}}, indent=2, sort_keys=True), "```", ""])
    return "\n".join(lines)
