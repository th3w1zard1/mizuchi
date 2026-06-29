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
    ".nfo",
    ".plist",
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
    ".pkg",
    ".rar",
    ".tar",
    ".tgz",
    ".whl",
    ".xar",
    ".zip",
}
BINARY_ANALYSIS_SUFFIXES = {".dll", ".dylib", ".exe", ".so", ".xbe"}


@dataclass(frozen=True)
class ExportConfig:
    input_path: Path
    out_dir: Path
    output_format: str = "json"
    extract_containers: bool = True
    max_files: int = 1000
    max_depth: int = 4
    max_text_bytes: int = 2_000_000
    max_container_members: int = 300
    strings_limit: int = 500


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
        manifest = {
            "schema": "mizuchi.context-export.v1",
            "createdAt": now(),
            "inputPath": str(self.root),
            "outputDirectory": str(self.out_dir),
            "outputFormat": self.config.output_format,
            "extractContainers": self.config.extract_containers,
            "limits": {
                "maxFiles": self.config.max_files,
                "maxDepth": self.config.max_depth,
                "maxTextBytes": self.config.max_text_bytes,
                "maxContainerMembers": self.config.max_container_members,
                "stringsLimit": self.config.strings_limit,
            },
            "filesVisited": self.seen_files,
            "filesExported": len(self.rows),
            "truncated": self.seen_files >= self.config.max_files,
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
        digest = sha256_file(path)
        rel = logical_prefix or path.name
        kind = classify_file(path)
        export_path = self._write_surrogate(path, rel, kind, digest, stat.st_size)
        row: dict[str, Any] = {
            "path": rel,
            "sourcePath": str(path),
            "size": stat.st_size,
            "sha256": digest,
            "kind": kind,
            "mime": mimetypes.guess_type(path.name)[0],
            "export": str(export_path.relative_to(self.out_dir)),
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

    def _write_surrogate(self, path: Path, rel: str, kind: str, digest: str, size: int) -> Path:
        payload = build_file_payload(path, rel, kind, digest, size, self.config)
        out_path = self.files_dir / safe_export_name(rel, self.config.output_format)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if self.config.output_format == "md":
            out_path.write_text(render_markdown(payload), encoding="utf-8")
        else:
            out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return out_path

    def _extract_container(self, path: Path, rel: str, digest: str, depth: int) -> dict[str, Any] | None:
        if shutil.which("7z") is None:
            return {"status": "skipped", "reason": "7z not found"}
        listing = run_command(["7z", "l", "-ba", str(path)], timeout=60)
        if listing["returnCode"] != 0:
            return {"status": "skipped", "reason": "7z list failed", "stderr": listing["stderr"][-1000:]}
        extract_root = self.extracted_dir / safe_component(f"{Path(rel).name}-{digest[:12]}")
        extract_root.mkdir(parents=True, exist_ok=True)
        result = run_command(["7z", "x", "-y", f"-o{extract_root}", str(path)], timeout=180)
        extraction = {
            "status": "complete" if result["returnCode"] == 0 else "failed",
            "tool": "7z",
            "directory": str(extract_root.relative_to(self.out_dir)),
            "returnCode": result["returnCode"],
            "stdoutTail": result["stdout"][-2000:],
            "stderrTail": result["stderr"][-2000:],
        }
        if result["returnCode"] == 0:
            before = len(self.rows)
            self._walk_extracted_members(
                extract_root,
                logical_prefix=f"{rel}::extracted",
                depth=depth + 1,
                limit=self.config.max_container_members,
            )
            extraction["exportedMembers"] = len(self.rows) - before
        return extraction

    def _walk_extracted_members(self, path: Path, *, logical_prefix: str, depth: int, limit: int) -> None:
        exported = 0
        for child in sorted(path.rglob("*"), key=child_priority):
            if exported >= limit or self.seen_files >= self.config.max_files:
                return
            if not child.is_file():
                continue
            rel = child.relative_to(path)
            child_prefix = f"{logical_prefix}/{rel.as_posix()}"
            self._export_file(child, logical_prefix=child_prefix, depth=depth)
            exported += 1


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
    if suffix in TEXT_SUFFIXES:
        return "text"
    if suffix in ARCHIVE_SUFFIXES:
        return "container"
    if is_probably_text(path):
        return "text"
    return "binary"


def should_try_extract(path: Path, kind: str) -> bool:
    return kind in {"container", "pe", "zip", "pdf"} or path.suffix.lower() in ARCHIVE_SUFFIXES


def child_priority(path: Path) -> tuple[int, str]:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if path.is_dir():
        if name in {".rsrc", "resources", "resource", "scripts", "docs", "documentation"}:
            return (0, name)
        if name in {"cursor", "icon", "group_icon"}:
            return (8, name)
        return (3, name)
    if name in {"version.txt", "packageinfo", "distribution", "setup.ini", "manifest", "installscript"}:
        return (0, name)
    if suffix in {".txt", ".xml", ".json", ".ini", ".manifest", ".plist", ".yaml", ".yml", ".md"}:
        return (1, name)
    if suffix in {".msi", ".pkg", ".zip", ".cab", ".7z", ".exe"}:
        return (2, name)
    if name in {".text", ".rdata", ".data", ".idata", "certificate"}:
        return (4, name)
    if suffix in {".ico", ".icns", ".png", ".jpg", ".jpeg", ".bmp"} or "cursor" in str(path).lower():
        return (9, name)
    return (5, name)


def build_file_payload(path: Path, rel: str, kind: str, digest: str, size: int, config: ExportConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "mizuchi.context-file.v1",
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
        payload["analysis"] = binary_analysis(path, config)
    elif kind in {"zip", "container"}:
        payload["container"] = container_listing(path)
    else:
        payload["strings"] = extract_strings(path, config.strings_limit)
    return payload


def binary_analysis(path: Path, config: ExportConfig) -> dict[str, Any]:
    analysis = {
        "file": run_command(["file", "-b", str(path)], timeout=15),
        "strings": extract_strings(path, config.strings_limit),
    }
    if path.suffix.lower() in BINARY_ANALYSIS_SUFFIXES or classify_file(path) == "pe":
        analysis["objdumpHeaders"] = run_command(["objdump", "-x", str(path)], timeout=30, max_chars=60_000)
        analysis["rabinInfo"] = run_command(["rabin2", "-I", str(path)], timeout=30, max_chars=40_000)
        analysis["rabinImports"] = run_command(["rabin2", "-i", str(path)], timeout=30, max_chars=40_000)
        analysis["rabinSections"] = run_command(["rabin2", "-S", str(path)], timeout=30, max_chars=40_000)
    return analysis


def container_listing(path: Path) -> dict[str, Any]:
    if shutil.which("7z") is None:
        return {"status": "skipped", "reason": "7z not found"}
    result = run_command(["7z", "l", str(path)], timeout=60, max_chars=80_000)
    return {"status": "complete" if result["returnCode"] == 0 else "failed", "listing": result}


def pdf_text(path: Path, max_bytes: int) -> str:
    if shutil.which("pdftotext") is None:
        return ""
    with tempfile.TemporaryDirectory(prefix="mizuchi-pdf-") as tmp:
        out = Path(tmp) / "out.txt"
        result = run_command(["pdftotext", "-layout", str(path), str(out)], timeout=60)
        if result["returnCode"] != 0 or not out.exists():
            return ""
        return out.read_text(encoding="utf-8", errors="replace")[:max_bytes]


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


def read_text_preview(path: Path, max_bytes: int) -> str:
    data = path.read_bytes()[:max_bytes]
    return data.decode("utf-8", errors="replace")


def is_probably_text(path: Path) -> bool:
    try:
        data = path.read_bytes()[:8192]
    except OSError:
        return False
    if b"\0" in data:
        return False
    if not data:
        return True
    printable = sum(1 for b in data if b in b"\n\r\t" or 32 <= b < 127)
    return printable / len(data) > 0.85


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
