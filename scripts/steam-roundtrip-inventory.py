#!/usr/bin/env python3
"""Inventory Steam apps for decompilation and byte-roundtrip feasibility."""

from __future__ import annotations

import argparse
import datetime as _datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


DEFAULT_STEAMAPPS = Path("/run/media/brunner56/MyBook/SteamLibrary/steamapps")
EXEC_SUFFIXES = {".exe", ".dll", ".so", ".bin"}
SKIP_DIR_NAMES = {
    "__pycache__",
    ".git",
    "binaries",
    "data",
    "data_win64",
    "engine",
    "fmv",
    "fonts",
    "lang",
    "levelpacks",
    "movies",
    "music",
    "resource",
    "resources",
    "soundtracks",
    "steamassets",
    "streammusic",
    "streamsounds",
    "streamwaves",
    "texturepacks",
    "videos",
}
SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hpp",
    ".cs",
    ".java",
    ".lua",
    ".py",
    ".js",
    ".ts",
    ".el",
    ".nut",
    ".gd",
    ".ini",
}


def parse_acf(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for key in ("appid", "name", "installdir"):
        match = re.search(rf'"{re.escape(key)}"\s+"([^"]*)"', text)
        if match:
            out[key] = match.group(1)
    return out


def file_type(path: Path) -> str:
    try:
        return subprocess.check_output(
            ["file", "-b", str(path)],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).strip()
    except Exception as exc:
        return f"file-error: {exc}"


def likely_executable(path: Path) -> bool:
    if path.suffix.lower() in EXEC_SUFFIXES:
        return True
    try:
        return os.access(path, os.X_OK) and path.is_file() and "." not in path.name
    except OSError:
        return False


def walk_relevant(root: Path, max_files: int = 12000):
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel_parts = Path(dirpath).relative_to(root).parts
        dirnames[:] = [
            d
            for d in dirnames
            if d.lower() not in SKIP_DIR_NAMES
            and not d.endswith("_Data")
            and not d.endswith(".app")
        ]
        if any(part.lower() in SKIP_DIR_NAMES for part in rel_parts):
            continue
        for filename in filenames:
            seen += 1
            if seen > max_files:
                return
            yield Path(dirpath) / filename


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_binary(kind: str) -> str:
    low = kind.lower()
    if "pe32+" in low or "pe32 executable" in low:
        return "pe"
    if "elf" in low:
        return "elf"
    if "ms-dos executable" in low:
        return "dos"
    if "mono/.net assembly" in low or ".net assembly" in low:
        return "dotnet"
    if "text" in low or "script" in low or "shell" in low:
        return "script"
    return "other"


def detect_engines(root: Path) -> list[str]:
    engines: list[str] = []
    names = {p.name for p in root.iterdir()} if root.exists() else set()
    if "UnityPlayer.dll" in names or any(p.name.endswith("_Data") for p in root.iterdir() if p.is_dir()):
        engines.append("unity")
    if (root / "Engine").exists() or (root / "Binaries").exists():
        engines.append("unreal-or-ue-derived")
    if (root / "DosBox").exists() or (root / "dosbox.exe").exists():
        engines.append("dosbox-packaged")
    if (root / "Src").exists() or (root / "scripts").exists():
        engines.append("script-source-present")
    if any(p.name == "game.unx" for p in walk_relevant(root, max_files=3000)):
        engines.append("gamemaker")
    return sorted(set(engines))


def source_summary(root: Path) -> dict[str, object]:
    counts: dict[str, int] = {}
    examples: list[str] = []
    for path in walk_relevant(root):
        suffix = path.suffix.lower()
        if suffix not in SOURCE_SUFFIXES:
            continue
        counts[suffix] = counts.get(suffix, 0) + 1
        if len(examples) < 8:
            examples.append(str(path.relative_to(root)))
    return {"counts": counts, "examples": examples}


def executable_score(root: Path, path: Path) -> tuple[int, int, str]:
    rel = path.relative_to(root)
    parts = [p.lower() for p in rel.parts]
    name = path.name.lower()
    stem = path.stem.lower()
    root_words = re.findall(r"[a-z0-9]+", root.name.lower())
    score = 0

    if path.suffix.lower() == ".exe":
        score += 500
    elif path.suffix.lower() in {".dll", ".so"}:
        score -= 200

    if len(rel.parts) == 1:
        score += 200
    elif len(rel.parts) <= 3:
        score += 60
    else:
        score -= len(rel.parts) * 8

    if any(word and word in stem for word in root_words):
        score += 180
    if stem in {"game", "main", "nwmain-linux"}:
        score += 80
    if any(token in name for token in ("launcher", "setup", "install", "unins", "crash", "diagnose", "autorun")):
        score -= 250
    if any(part in {"redist", "_commonredist", "directx", "vcredist", "install", "support", "utils"} for part in parts):
        score -= 200
    if any(token in name for token in ("steam", "mss", "bink", "eax", "vorbis", "ogg", "sdl", "zlib")):
        score -= 80

    return (-score, len(rel.parts), str(rel).lower())


def executable_summary(root: Path) -> list[dict[str, object]]:
    candidates: list[Path] = []
    for path in walk_relevant(root):
        if likely_executable(path):
            candidates.append(path)

    records: list[dict[str, object]] = []
    for path in sorted(candidates, key=lambda p: executable_score(root, p))[:250]:
        kind = file_type(path)
        binary_class = classify_binary(kind)
        if binary_class == "script" and path.suffix.lower() not in {".sh", ".py"}:
            continue
        try:
            digest = sha256_file(path)
        except OSError:
            digest = ""
        records.append(
            {
                "path": str(path.relative_to(root)),
                "size": path.stat().st_size,
                "class": binary_class,
                "file": kind,
                "sha256": digest,
            }
        )
    return records


def native_targets(record: dict[str, object]) -> list[dict[str, object]]:
    return [b for b in record["executables"] if b["class"] in {"pe", "elf", "dos", "dotnet"}]


def feasibility(record: dict[str, object]) -> tuple[str, str]:
    engines = set(record["engines"])
    native = native_targets(record)
    source_counts = record["source"]["counts"]

    if not native and source_counts:
        return "source-assets-only", "Source/script assets are present, but no native target was identified for byte roundtrip."
    if not native:
        return "no-target", "No executable target identified."
    if "script-source-present" in engines and source_counts:
        return "partial-source-present", "Some shipped scripts/source assets exist; native executable roundtrip still lacks build recipe and object/function map."
    if "unity" in engines:
        return "blocked-unity", "Unity player/native runtime plus asset bundles; full byte-matching source requires engine version, project source, native plugins, build settings, and deterministic toolchain."
    if "gamemaker" in engines:
        return "blocked-gamemaker", "GameMaker data/runtime can sometimes expose logic, but byte-matching native rebuild requires original project/compiler/runtime pipeline."
    if any(b["class"] == "dos" for b in native):
        return "blocked-dos", "DOS packaged target found; full roundtrip requires segment map, compiler/linker version, memory model, and per-function object boundaries."
    return "blocked-native", "Native binary found; full byte-matching source requires symbols or function boundaries, matching compiler/linker, libraries, build flags, and per-function proof targets."


def roundtrip_evidence(record: dict[str, object]) -> dict[str, object]:
    native = native_targets(record)
    target = native[0] if native else None
    status = record["roundtrip_status"]
    requirements = []
    if target:
        requirements.extend(
            [
                "function/object boundary map",
                "matching compiler and linker versions",
                "original build flags and libraries",
                "generated candidate source for every code unit",
                "byte-identical object or executable comparison",
            ]
        )
    if status in {"blocked-unity", "blocked-gamemaker"}:
        requirements.append("engine/project build pipeline and exact runtime version")
    if status == "blocked-dos":
        requirements.append("original DOS memory model, segment layout, and compiler")

    achieved = status == "matched"
    return {
        "status": "matched" if achieved else "not-roundtripped",
        "byteIdentical": achieved,
        "primaryTarget": target,
        "decompiledSourceTree": None,
        "rebuiltArtifact": None,
        "comparison": "not-run" if not achieved else "byte-identical",
        "missingInputs": requirements,
    }


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "app"


def emit_app_workspaces(inventory: dict[str, object], workspace_root: Path) -> None:
    workspace_root.mkdir(parents=True, exist_ok=True)
    for app in inventory["apps"]:
        app_dir = workspace_root / f"{app['appid'] or 'unknown'}-{slugify(app['name'])}"
        app_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema": "mizuchi.steam-app-roundtrip-workspace.v1",
            "generatedAt": inventory["generatedAt"],
            "app": app,
        }
        (app_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

        target = app["roundtrip_evidence"]["primaryTarget"]
        lines = [
            f"# {app['name']}",
            "",
            f"Status: `{app['roundtrip_status']}`",
            f"Steam path: `{app['path']}`",
            "",
        ]
        if target:
            target_path = Path(app["path"]) / target["path"]
            lines.extend(
                [
                    "## Primary Target",
                    "",
                    f"- Path: `{target_path}`",
                    f"- Class: `{target['class']}`",
                    f"- Size: `{target['size']}`",
                    f"- SHA256: `{target['sha256']}`",
                    "",
                ]
            )
            (app_dir / "original.sha256").write_text(f"{target['sha256']}  {target_path}\n")
        else:
            lines.extend(["## Primary Target", "", "No native executable target was identified.", ""])

        lines.extend(
            [
                "## Roundtrip Gate",
                "",
                f"- Byte identical: `{str(app['roundtrip_evidence']['byteIdentical']).lower()}`",
                f"- Comparison: `{app['roundtrip_evidence']['comparison']}`",
                "",
                "## Missing Inputs",
                "",
            ]
        )
        missing = app["roundtrip_evidence"]["missingInputs"]
        if missing:
            lines.extend(f"- {item}" for item in missing)
        else:
            lines.append("- No native target; choose a target binary before decompilation.")
        lines.append("")
        (app_dir / "README.md").write_text("\n".join(lines))


def build_inventory(steamapps: Path, app_filter: str | None = None, limit: int | None = None) -> dict[str, object]:
    apps = []
    for manifest in sorted(steamapps.glob("appmanifest_*.acf")):
        meta = parse_acf(manifest)
        installdir = meta.get("installdir")
        if not installdir:
            continue
        if meta.get("name", "").lower().startswith("proton "):
            continue
        if app_filter:
            haystack = " ".join([meta.get("appid", ""), meta.get("name", ""), installdir]).lower()
            if app_filter.lower() not in haystack:
                continue
        root = steamapps / "common" / installdir
        if not root.exists():
            continue
        app = {
            "appid": meta.get("appid", ""),
            "name": meta.get("name", installdir),
            "installdir": installdir,
            "path": str(root),
            "engines": detect_engines(root),
            "executables": executable_summary(root),
            "source": source_summary(root),
        }
        status, reason = feasibility(app)
        app["roundtrip_status"] = status
        app["roundtrip_reason"] = reason
        app["roundtrip_evidence"] = roundtrip_evidence(app)
        apps.append(app)
        if limit is not None and len(apps) >= limit:
            break

    toolchain = {
        "file": shutil.which("file"),
        "objdump": shutil.which("objdump"),
        "ghidra_headless": shutil.which("analyzeHeadless"),
        "objdiff": shutil.which("objdiff"),
    }
    matched = sum(1 for app in apps if app["roundtrip_evidence"]["byteIdentical"])
    return {
        "schema": "mizuchi.steam-roundtrip-inventory.v1",
        "generatedAt": _datetime.datetime.now(_datetime.UTC).isoformat(),
        "steamapps": str(steamapps),
        "app_count": len(apps),
        "matched_count": matched,
        "all_byte_identical": matched == len(apps) and bool(apps),
        "toolchain": toolchain,
        "apps": apps,
    }


def print_markdown(inventory: dict[str, object]) -> None:
    print("# Steam Roundtrip Inventory")
    print()
    print(f"Steam apps root: `{inventory['steamapps']}`")
    print(f"Apps scanned: {inventory['app_count']}")
    print(f"Byte-identical full-app roundtrips: {inventory['matched_count']}")
    print()
    print("| App | Status | Primary target | Engines/signals | Native targets | Source signals | Reason |")
    print("| --- | --- | --- | --- | ---: | --- | --- |")
    for app in inventory["apps"]:
        native = native_targets(app)
        native_count = len(native)
        primary = "-"
        if app["roundtrip_evidence"]["primaryTarget"]:
            primary = app["roundtrip_evidence"]["primaryTarget"]["path"]
        source_counts = ", ".join(f"{k}:{v}" for k, v in sorted(app["source"]["counts"].items())) or "-"
        engines = ", ".join(app["engines"]) or "-"
        reason = str(app["roundtrip_reason"]).replace("|", "\\|")
        print(
            f"| {app['name']} | `{app['roundtrip_status']}` | `{primary}` | {engines} | {native_count} | {source_counts} | {reason} |"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--steamapps", type=Path, default=DEFAULT_STEAMAPPS)
    parser.add_argument("--app", help="Substring filter over app id, name, or install dir")
    parser.add_argument("--limit", type=int, help="Stop after N matching apps")
    parser.add_argument("--out", type=Path, help="Write the JSON inventory to this path")
    parser.add_argument("--emit-workspaces", type=Path, help="Create per-app roundtrip workspaces under this directory")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    inventory = build_inventory(args.steamapps, app_filter=args.app, limit=args.limit)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n")
    if args.emit_workspaces:
        emit_app_workspaces(inventory, args.emit_workspaces)
    if args.json:
        print(json.dumps(inventory, indent=2, sort_keys=True))
    else:
        print_markdown(inventory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
