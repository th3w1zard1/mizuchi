#!/usr/bin/env python3
"""Launch a PE under Wine and dump its mapped executable sections.

This is an acquisition tool for packed/protected Windows binaries. It does not
claim semantic recovery; it captures the process image bytes that Wine maps for
the target executable so later stages can target the runtime code image instead
of only the packed on-disk bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any


def run_json(args: list[str]) -> dict[str, Any]:
    proc = subprocess.run(args, text=True, capture_output=True, check=False, timeout=60)
    if proc.returncode != 0:
        raise SystemExit((proc.stderr or proc.stdout).strip() or f"command failed: {' '.join(args)}")
    data = json.loads(proc.stdout)
    if not isinstance(data, dict):
        raise SystemExit(f"JSON root must be object: {' '.join(args)}")
    return data


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_maps(pid: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(f"/proc/{pid}/maps").open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split(maxsplit=5)
            if len(parts) < 5:
                continue
            start_s, end_s = parts[0].split("-", 1)
            rows.append(
                {
                    "start": int(start_s, 16),
                    "end": int(end_s, 16),
                    "perms": parts[1],
                    "offset": int(parts[2], 16),
                    "dev": parts[3],
                    "inode": parts[4],
                    "path": parts[5] if len(parts) > 5 else "",
                }
            )
    return rows


def find_target_pid(binary: Path, launcher_pid: int, timeout: float) -> tuple[int, list[dict[str, Any]]]:
    target_real = str(binary.resolve())
    deadline = time.time() + timeout
    while time.time() < deadline:
        proc_root = Path("/proc")
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            if pid == os.getpid():
                continue
            try:
                maps = parse_maps(pid)
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            target_maps = [row for row in maps if row.get("path") == target_real]
            if target_maps:
                return pid, target_maps
        if launcher_pid and Path(f"/proc/{launcher_pid}").exists() is False:
            break
        time.sleep(0.25)
    raise SystemExit(f"target process for {binary} did not appear within {timeout:.1f}s")


def executable_sections(binary: Path) -> list[dict[str, Any]]:
    info = run_json(["rabin2", "-I", "-j", str(binary)])
    sections_json = run_json(["rabin2", "-S", "-j", str(binary)])
    image_base = 0
    info_obj = info.get("info") if isinstance(info.get("info"), dict) else {}
    if isinstance(info_obj.get("baddr"), int):
        image_base = int(info_obj["baddr"])
    entries = run_json(["rabin2", "-e", "-j", str(binary)]).get("entries")
    if image_base <= 0 and isinstance(entries, list) and entries:
        first = entries[0]
        if isinstance(first, dict) and isinstance(first.get("baddr"), int):
            image_base = int(first["baddr"])
    if image_base <= 0:
        image_base = 0x400000
    raw = binary.read_bytes()
    sections: list[dict[str, Any]] = []
    for section in sections_json.get("sections", []):
        if not isinstance(section, dict) or "x" not in str(section.get("perm") or ""):
            continue
        paddr = int(section.get("paddr") or 0)
        size = int(section.get("size") or section.get("vsize") or 0)
        vaddr = int(section.get("vaddr") or 0)
        if paddr < 0 or size <= 0 or paddr + size > len(raw):
            continue
        sections.append(
            {
                "name": section.get("name"),
                "paddr": paddr,
                "vaddr": vaddr,
                "size": size,
                "imageBase": image_base,
                "diskSha256": sha256_bytes(raw[paddr : paddr + size]),
            }
        )
    return sections


def read_mem(pid: int, address: int, size: int) -> bytes:
    with Path(f"/proc/{pid}/mem").open("rb", buffering=0) as mem:
        mem.seek(address)
        data = mem.read(size)
    if len(data) != size:
        raise SystemExit(f"short read from pid {pid} at 0x{address:x}: got {len(data)} expected {size}")
    return data


def safe_map_name(index: int, row: dict[str, Any]) -> str:
    path = str(row.get("path") or "anonymous")
    leaf = Path(path).name if path else "anonymous"
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in leaf)[:80]
    return f"map-{index:04d}-{row['start']:08x}-{row['end']:08x}-{safe or 'anonymous'}.bin"


def dump_executable_maps(pid: int, maps: list[dict[str, Any]], out_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    maps_dir = out_dir / "exec-maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    for index, row in enumerate(maps):
        perms = str(row.get("perms") or "")
        if "x" not in perms:
            continue
        start = int(row["start"])
        end = int(row["end"])
        size = end - start
        out = maps_dir / safe_map_name(index, row)
        try:
            data = read_mem(pid, start, size)
        except Exception as exc:
            rows.append({**row, "size": size, "dumped": False, "error": str(exc)})
            continue
        out.write_bytes(data)
        rows.append(
            {
                **row,
                "size": size,
                "dumped": True,
                "runtimePath": str(out),
                "runtimeSha256": sha256_bytes(data),
                "startsWithMz": data.startswith(b"MZ"),
                "containsMz": data.find(b"MZ"),
                "containsPe": data.find(b"PE\0\0"),
            }
        )
    return rows


def terminate(proc: subprocess.Popen[str], wineprefix: Path) -> None:
    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    wine = shutil.which("wineserver")
    if wine:
        subprocess.run(
            [wine, "-k"],
            env={**os.environ, "WINEPREFIX": str(wineprefix)},
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--wineprefix", type=Path, default=Path("target/wine-runtime-dump-prefix"))
    parser.add_argument("--wait", type=float, default=12.0)
    parser.add_argument("--discover-timeout", type=float, default=30.0)
    parser.add_argument("--xvfb-run", default="xvfb-run")
    parser.add_argument("--wine", default="wine")
    parser.add_argument("--dump-exec-maps", action="store_true")
    args = parser.parse_args()

    binary = args.binary.resolve()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.wineprefix.mkdir(parents=True, exist_ok=True)
    sections = executable_sections(binary)
    if not sections:
        raise SystemExit("no executable sections found")

    env = {**os.environ, "WINEPREFIX": str(args.wineprefix.resolve()), "WINEDEBUG": "-all"}
    command = [args.xvfb_run, "-a", args.wine, binary.name]
    stdout = (args.out_dir / "wine.stdout").open("w", encoding="utf-8")
    stderr = (args.out_dir / "wine.stderr").open("w", encoding="utf-8")
    proc = subprocess.Popen(command, cwd=binary.parent, env=env, text=True, stdout=stdout, stderr=stderr)
    try:
        pid, target_maps = find_target_pid(binary, proc.pid, args.discover_timeout)
        time.sleep(max(0.0, args.wait))
        maps = parse_maps(pid)
        exec_maps = dump_executable_maps(pid, maps, args.out_dir) if args.dump_exec_maps else []
        runtime_sections: list[dict[str, Any]] = []
        for section in sections:
            vaddr = int(section["vaddr"])
            size = int(section["size"])
            data = read_mem(pid, vaddr, size)
            out = args.out_dir / f"{str(section['name']).strip('.')}.runtime.bin"
            out.write_bytes(data)
            disk_same = sha256_bytes(data) == section["diskSha256"]
            runtime_sections.append(
                {
                    **section,
                    "runtimePath": str(out),
                    "runtimeSha256": sha256_bytes(data),
                    "runtimeMatchesDisk": disk_same,
                    "runtimeDifferentFromDisk": not disk_same,
                }
            )
    finally:
        stdout.close()
        stderr.close()
        terminate(proc, args.wineprefix.resolve())

    report = {
        "schema": "reconkit.pe-runtime-code-dump.v1",
        "status": "dumped",
        "binary": str(binary),
        "pid": pid,
        "wineprefix": str(args.wineprefix.resolve()),
        "command": command,
        "targetMaps": target_maps,
        "maps": [row for row in maps if row.get("path") == str(binary)],
        "allMapsPath": str(args.out_dir / "process-maps.json"),
        "dumpExecMaps": args.dump_exec_maps,
        "execMapCount": len(exec_maps),
        "execMaps": exec_maps,
        "sectionCount": len(runtime_sections),
        "sections": runtime_sections,
        "allRuntimeSectionsMatchDisk": all(section["runtimeMatchesDisk"] for section in runtime_sections),
        "anyRuntimeSectionDiffersFromDisk": any(section["runtimeDifferentFromDisk"] for section in runtime_sections),
        "claimBoundary": (
            "Runtime bytes are process-image acquisition evidence only. They are useful for unpacked-code targeting, "
            "but do not by themselves prove semantic C/C++ recovery."
        ),
    }
    (args.out_dir / "process-maps.json").write_text(json.dumps(maps, indent=2, sort_keys=True) + "\n")
    (args.out_dir / "pe-runtime-code-dump.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
