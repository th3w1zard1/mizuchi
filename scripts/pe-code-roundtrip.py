#!/usr/bin/env python3
"""Rebuild a PE executable's code sections from verified Mizuchi task candidates.

This is intentionally a code-section parity tool, not a full semantic recovery
claim. It copies the original PE container, patches verified candidate bytes
over their target slices, and compares executable sections only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise SystemExit(f"JSON root must be object: {path}")
    return data


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_json(args: list[str]) -> dict[str, Any]:
    proc = subprocess.run(args, text=True, capture_output=True, check=False, timeout=60)
    if proc.returncode != 0:
        raise SystemExit((proc.stderr or proc.stdout).strip() or f"command failed: {' '.join(args)}")
    data = json.loads(proc.stdout)
    if not isinstance(data, dict):
        raise SystemExit(f"JSON root must be object from {' '.join(args)}")
    return data


def yaml_scalar_unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, str) else value[1:-1]
        except json.JSONDecodeError:
            return value[1:-1]
    return value


def read_simple_case_yaml(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(errors="replace").splitlines():
        match = re.match(r"^([A-Za-z][A-Za-z0-9_]*):\s*(.*)$", line)
        if not match:
            continue
        key, raw = match.groups()
        if raw in {"|", ">"}:
            continue
        out[key] = yaml_scalar_unquote(raw)
    return out


def executable_sections(binary: Path) -> list[dict[str, Any]]:
    data = run_json(["rabin2", "-S", "-j", str(binary)])
    sections = data.get("sections")
    if not isinstance(sections, list):
        return []
    out: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        if "x" not in str(section.get("perm") or ""):
            continue
        paddr = int(section.get("paddr") or 0)
        size = int(section.get("size") or section.get("vsize") or 0)
        if paddr < 0 or size <= 0:
            continue
        out.append(
            {
                "name": section.get("name"),
                "paddr": paddr,
                "vaddr": section.get("vaddr"),
                "size": size,
                "sha256": sha256_bytes(binary.read_bytes()[paddr : paddr + size]),
            }
        )
    return out


def prompt_case_index(prompts_dir: Path, package: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    if not prompts_dir.exists():
        return out
    for prompt_dir in sorted(path for path in prompts_dir.iterdir() if path.is_dir()):
        case_data = read_simple_case_yaml(prompt_dir / "case.yaml")
        if not case_data.get("oneShotPackagePath") or not case_data.get("oneShotTaskPath"):
            continue
        try:
            same_package = Path(case_data["oneShotPackagePath"]).resolve() == package
        except OSError:
            same_package = False
        if same_package:
            out[Path(case_data["oneShotTaskPath"]).as_posix().strip("/")] = prompt_dir
    return out


def interval_covered_by_sections(offset: int, size: int, sections: list[dict[str, Any]]) -> bool:
    for section in sections:
        start = int(section["paddr"])
        end = start + int(section["size"])
        if start <= offset and offset + size <= end:
            return True
    return False


def union_size(intervals: list[tuple[int, int]]) -> int:
    if not intervals:
        return 0
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return sum(end - start for start, end in merged)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--prompts-dir", type=Path, default=ROOT / "prompts")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    package = args.package.resolve()
    receipt = read_json(package / "one-shot-source-receipt.json")
    binary = Path(str(receipt.get("binary") or "")).resolve()
    if not binary.is_file():
        raise SystemExit(f"binary not found: {binary}")
    tasks_doc = read_json(package / "FUNCTION_RECONSTRUCTION_TASKS.json")
    sections = executable_sections(binary)
    if not sections:
        raise SystemExit(f"no executable PE sections found: {binary}")

    original = bytearray(binary.read_bytes())
    rebuilt = bytearray(original)
    prompts = prompt_case_index(args.prompts_dir, package)
    patched: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    intervals: list[tuple[int, int]] = []

    for task in tasks_doc.get("tasks", []):
        if not isinstance(task, dict):
            continue
        task_path = str(task.get("path") or "").strip("/")
        task_json_path = package / str(task.get("taskJson") or "")
        if not task_json_path.exists():
            skipped.append({"taskPath": task_path, "reason": "missing-task-json"})
            continue
        task_json = read_json(task_json_path)
        target = task_json.get("target") if isinstance(task_json.get("target"), dict) else {}
        offset = target.get("fileOffset")
        size = target.get("size")
        if not isinstance(offset, int) or not isinstance(size, int) or size <= 0:
            skipped.append({"taskPath": task_path, "reason": "missing-target-offset-or-size"})
            continue
        if not interval_covered_by_sections(offset, size, sections):
            skipped.append({"taskPath": task_path, "reason": "target-not-inside-executable-section"})
            continue
        prompt_dir = prompts.get(task_path)
        if prompt_dir is None:
            skipped.append({"taskPath": task_path, "reason": "prompt-not-imported"})
            continue
        build_report = prompt_dir / "build" / "build-and-verify.json"
        if not build_report.exists():
            skipped.append({"taskPath": task_path, "prompt": prompt_dir.name, "reason": "prompt-not-verified"})
            continue
        report = read_json(build_report)
        candidate_object = Path(str(report.get("candidate_object") or ""))
        if report.get("status") != "matched" or report.get("byte_identical") is not True:
            skipped.append({"taskPath": task_path, "prompt": prompt_dir.name, "reason": "prompt-report-not-byte-identical"})
            continue
        if not candidate_object.is_file():
            skipped.append({"taskPath": task_path, "prompt": prompt_dir.name, "reason": "candidate-output-missing"})
            continue
        candidate = candidate_object.read_bytes()
        target_bytes = (package / str(task.get("targetBytes") or "")).read_bytes()
        original_slice = bytes(original[offset : offset + size])
        if len(candidate) != size or candidate != target_bytes or candidate != original_slice:
            skipped.append({"taskPath": task_path, "prompt": prompt_dir.name, "reason": "candidate-does-not-match-target-slice"})
            continue
        rebuilt[offset : offset + size] = candidate
        intervals.append((offset, offset + size))
        patched.append(
            {
                "taskPath": task_path,
                "prompt": prompt_dir.name,
                "name": task.get("name"),
                "fileOffset": offset,
                "size": size,
                "sha256": sha256_bytes(candidate),
            }
        )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    rebuilt_path = args.out_dir / "swkotor-code-rebuilt.exe"
    rebuilt_path.write_bytes(rebuilt)
    section_reports = []
    for section in sections:
        start = int(section["paddr"])
        end = start + int(section["size"])
        original_section = bytes(original[start:end])
        rebuilt_section = bytes(rebuilt[start:end])
        section_reports.append(
            {
                **section,
                "rebuiltSha256": sha256_bytes(rebuilt_section),
                "byteIdentical": original_section == rebuilt_section,
            }
        )
    executable_size = sum(int(section["size"]) for section in sections)
    covered = union_size(intervals)
    code_identical = all(section["byteIdentical"] for section in section_reports)
    report = {
        "schema": "mizuchi.pe-code-roundtrip.v1",
        "status": "matched" if code_identical else "mismatched",
        "binary": str(binary),
        "package": str(package),
        "rebuiltExe": str(rebuilt_path),
        "wholeFileByteIdentical": bytes(original) == bytes(rebuilt),
        "originalSha256": sha256_bytes(bytes(original)),
        "rebuiltSha256": sha256_file(rebuilt_path),
        "codeSectionsByteIdentical": code_identical,
        "executableSectionBytes": executable_size,
        "verifiedCandidateBytes": covered,
        "carriedOriginalCodeBytes": executable_size - covered,
        "verifiedCandidateCoverageRatio": covered / executable_size if executable_size else 0,
        "patchedTaskCount": len(patched),
        "skippedTaskCount": len(skipped),
        "sections": section_reports,
        "patched": patched,
        "skipped": skipped,
        "claimBoundary": (
            "This rebuild proves PE executable code-section parity. Bytes outside patched verified candidates are "
            "carried from the original executable container and are not semantic C/C++ recovery."
        ),
    }
    report_path = args.out_dir / "pe-code-roundtrip.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if code_identical else 1


if __name__ == "__main__":
    raise SystemExit(main())
