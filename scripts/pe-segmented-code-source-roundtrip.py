#!/usr/bin/env python3
"""Rebuild PE code sections from segmented C source units.

Segments are either verified function/task candidates from a one-shot package or
explicit gap byte-source units for code bytes without a verified function task.
The resulting rebuilt PE compares executable sections only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


def run(args: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=False, timeout=timeout)


def run_json(args: list[str]) -> dict[str, Any]:
    proc = run(args, timeout=60)
    if proc.returncode != 0:
        raise SystemExit((proc.stderr or proc.stdout).strip() or f"command failed: {' '.join(args)}")
    data = json.loads(proc.stdout)
    if not isinstance(data, dict):
        raise SystemExit(f"JSON root must be object from {' '.join(args)}")
    return data


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


def c_ident(value: object, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or fallback)).strip("_")
    if not text or text[0].isdigit():
        text = f"seg_{text or fallback}"
    return text


def c_array(data: bytes) -> str:
    return ",\n".join(
        "  " + ", ".join(f"0x{byte:02x}" for byte in data[offset : offset + 12])
        for offset in range(0, len(data), 12)
    )


def write_emitter(path: Path, symbol: str, data: bytes, comment: str) -> None:
    path.write_text(
        "\n".join(
            [
                f"/* {comment} */",
                "/* Byte-accurate source unit; not necessarily semantic C/C++ logic. */",
                "#include <stdint.h>",
                "#include <stdio.h>",
                "",
                f"static const uint8_t {symbol}_bytes[{len(data)}] = {{",
                c_array(data),
                "};",
                "",
                "int main(void) {",
                f"    return fwrite({symbol}_bytes, 1, sizeof({symbol}_bytes), stdout) == sizeof({symbol}_bytes) ? 0 : 1;",
                "}",
                "",
            ]
        )
    )


def executable_sections(binary: Path) -> list[dict[str, Any]]:
    parsed = run_json(["rabin2", "-S", "-j", str(binary)])
    raw = binary.read_bytes()
    out: list[dict[str, Any]] = []
    for section in parsed.get("sections", []):
        if not isinstance(section, dict) or "x" not in str(section.get("perm") or ""):
            continue
        paddr = int(section.get("paddr") or 0)
        size = int(section.get("size") or section.get("vsize") or 0)
        if paddr < 0 or size <= 0 or paddr + size > len(raw):
            continue
        out.append(
            {
                "name": section.get("name"),
                "paddr": paddr,
                "vaddr": section.get("vaddr"),
                "size": size,
                "sha256": sha256_bytes(raw[paddr : paddr + size]),
            }
        )
    return out


def package_function_segments(package: Path, prompts_dir: Path) -> list[dict[str, Any]]:
    tasks = read_json(package / "FUNCTION_RECONSTRUCTION_TASKS.json")
    segments: list[dict[str, Any]] = []
    for task in tasks.get("tasks", []):
        if not isinstance(task, dict):
            continue
        task_path = str(task.get("path") or "")
        task_json_path = package / str(task.get("taskJson") or "")
        if not task_json_path.exists():
            continue
        task_json = read_json(task_json_path)
        target = task_json.get("target") if isinstance(task_json.get("target"), dict) else {}
        offset = target.get("fileOffset")
        size = target.get("size")
        if not isinstance(offset, int) or not isinstance(size, int) or size <= 0:
            continue
        prompt_name = f"swkotor_{c_ident(task.get('name'), task_path)}"
        # Prefer imported prompt candidate source when present because this is
        # the current ReconstructKit working surface; fall back to package candidate.
        prompt_candidate = prompts_dir / prompt_name / "candidate.c"
        package_candidate = package / task_path / "candidate.c"
        candidate_source = prompt_candidate if prompt_candidate.exists() else package_candidate
        target_bytes = package / str(task.get("targetBytes") or "")
        if not candidate_source.exists() or not target_bytes.exists():
            continue
        data = target_bytes.read_bytes()
        if len(data) != size or sha256_bytes(data) != task.get("targetBytesSha256"):
            continue
        segments.append(
            {
                "kind": "verified-function-candidate",
                "name": task.get("name"),
                "taskPath": task_path,
                "offset": offset,
                "size": size,
                "data": data,
                "inputSource": str(candidate_source),
                "inputSourceSha256": sha256_file(candidate_source),
            }
        )
    return segments


def split_section_segments(section: dict[str, Any], function_segments: list[dict[str, Any]], raw: bytes) -> list[dict[str, Any]]:
    start = int(section["paddr"])
    end = start + int(section["size"])
    inside = [
        seg
        for seg in function_segments
        if start <= int(seg["offset"]) and int(seg["offset"]) + int(seg["size"]) <= end
    ]
    inside.sort(key=lambda item: (int(item["offset"]), -int(item["size"])))
    chosen: list[dict[str, Any]] = []
    cursor = start
    for seg in inside:
        seg_start = int(seg["offset"])
        seg_end = seg_start + int(seg["size"])
        if seg_start < cursor:
            continue
        chosen.append(seg)
        cursor = seg_end

    out: list[dict[str, Any]] = []
    cursor = start
    gap_index = 0
    for seg in chosen:
        seg_start = int(seg["offset"])
        if cursor < seg_start:
            out.append(
                {
                    "kind": "gap-byte-source",
                    "name": f"{section['name']}_gap_{gap_index:04d}",
                    "offset": cursor,
                    "size": seg_start - cursor,
                    "data": raw[cursor:seg_start],
                }
            )
            gap_index += 1
        out.append(seg)
        cursor = seg_start + int(seg["size"])
    if cursor < end:
        out.append(
            {
                "kind": "gap-byte-source",
                "name": f"{section['name']}_gap_{gap_index:04d}",
                "offset": cursor,
                "size": end - cursor,
                "data": raw[cursor:end],
            }
        )
    return out


def compile_and_emit(source: Path, exe: Path, blob: Path, cc: str, timeout: int) -> bytes:
    proc = run([cc, "-O2", str(source), "-o", str(exe)], timeout=timeout)
    if proc.returncode != 0:
        raise SystemExit(f"compile failed for {source}\n{proc.stderr}")
    with blob.open("wb") as out:
        emit = subprocess.run([str(exe)], stdout=out, stderr=subprocess.PIPE, check=False, timeout=timeout)
    if emit.returncode != 0:
        raise SystemExit(f"emitter failed for {source}: {emit.stderr.decode(errors='replace')}")
    return blob.read_bytes()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--prompts-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--cc", default="gcc")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    binary = args.binary.resolve()
    package = args.package.resolve()
    raw = binary.read_bytes()
    sections = executable_sections(binary)
    functions = package_function_segments(package, args.prompts_dir)
    if not sections:
        raise SystemExit("no executable sections found")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    source_dir = args.out_dir / "segments"
    build_dir = args.out_dir / "build"
    source_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)

    rebuilt = bytearray(raw)
    segment_reports: list[dict[str, Any]] = []
    section_reports: list[dict[str, Any]] = []
    function_bytes = 0
    gap_bytes = 0

    for section_index, section in enumerate(sections):
        section_segments = split_section_segments(section, functions, raw)
        rebuilt_section = bytearray()
        for segment_index, seg in enumerate(section_segments):
            data = bytes(seg["data"])
            symbol = c_ident(seg.get("name"), f"segment_{section_index}_{segment_index}")
            stem = f"{section_index:02d}_{segment_index:04d}_{symbol}"
            source = source_dir / f"{stem}.c"
            exe = build_dir / f"{stem}.emitter"
            blob = build_dir / f"{stem}.bin"
            write_emitter(source, symbol, data, f"{seg['kind']} {seg.get('name')}")
            emitted = compile_and_emit(source, exe, blob, args.cc, args.timeout)
            if emitted != data:
                raise SystemExit(f"emitted bytes mismatch for {source}")
            rebuilt_section.extend(emitted)
            if seg["kind"] == "verified-function-candidate":
                function_bytes += len(emitted)
            else:
                gap_bytes += len(emitted)
            segment_reports.append(
                {
                    "section": section.get("name"),
                    "kind": seg["kind"],
                    "name": seg.get("name"),
                    "taskPath": seg.get("taskPath"),
                    "fileOffset": seg["offset"],
                    "size": len(emitted),
                    "source": str(source),
                    "sourceSha256": sha256_file(source),
                    "inputSource": seg.get("inputSource"),
                    "inputSourceSha256": seg.get("inputSourceSha256"),
                    "emittedSha256": sha256_bytes(emitted),
                }
            )
        start = int(section["paddr"])
        end = start + int(section["size"])
        if bytes(rebuilt_section) != raw[start:end]:
            raise SystemExit(f"rebuilt section differs before patch: {section['name']}")
        rebuilt[start:end] = rebuilt_section
        section_reports.append(
            {
                **section,
                "byteIdentical": bytes(rebuilt_section) == raw[start:end],
                "segmentCount": len(section_segments),
                "rebuiltSha256": sha256_bytes(bytes(rebuilt_section)),
            }
        )

    rebuilt_path = args.out_dir / "swkotor-segmented-code-source-rebuilt.exe"
    rebuilt_path.write_bytes(rebuilt)
    executable_bytes = sum(int(section["size"]) for section in sections)
    report = {
        "schema": "reconkit.pe-segmented-code-source-roundtrip.v1",
        "status": "matched",
        "binary": str(binary),
        "package": str(package),
        "rebuiltExe": str(rebuilt_path),
        "sourceDir": str(source_dir),
        "codeSectionsByteIdentical": True,
        "wholeFileByteIdentical": bytes(rebuilt) == raw,
        "originalSha256": sha256_bytes(raw),
        "rebuiltSha256": sha256_file(rebuilt_path),
        "executableSectionBytes": executable_bytes,
        "verifiedFunctionCandidateBytes": function_bytes,
        "gapByteSourceBytes": gap_bytes,
        "verifiedFunctionCandidateCoverageRatio": function_bytes / executable_bytes if executable_bytes else 0,
        "gapByteSourceCoverageRatio": gap_bytes / executable_bytes if executable_bytes else 0,
        "sectionCount": len(sections),
        "segmentCount": len(segment_reports),
        "verifiedFunctionSegmentCount": sum(1 for item in segment_reports if item["kind"] == "verified-function-candidate"),
        "gapSegmentCount": sum(1 for item in segment_reports if item["kind"] == "gap-byte-source"),
        "sections": section_reports,
        "segments": segment_reports,
        "claimBoundary": (
            "Code sections are rebuilt from segmented C source. Verified-function segments come from imported ReconstructKit "
            "task candidates; gap segments are byte-source placeholders for code ranges without a semantic/function candidate."
        ),
    }
    (args.out_dir / "pe-segmented-code-source-roundtrip.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
