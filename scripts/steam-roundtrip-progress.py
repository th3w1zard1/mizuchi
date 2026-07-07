#!/usr/bin/env python3
"""Summarize saved Steam roundtrip manifests against the current Steam inventory."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_TOOL = ROOT / "scripts" / "steam-roundtrip-inventory.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inventory_mod = load_module("steam_roundtrip_inventory", INVENTORY_TOOL)


def read_json(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def app_key(value: object) -> str:
    return str(value or "").strip()


def manifest_score(manifest: dict[str, object]) -> tuple[int, int, int]:
    full = 1 if manifest.get("fullAppByteIdentical") is True else 0
    matched = int(manifest.get("appFileRoundtripMatched") or 0)
    semantic = int(manifest.get("matchedFunctions") or 0)
    return full, matched, semantic


def steam_apps(steamapps: Path, app_filter: str | None = None) -> list[dict[str, object]]:
    apps: list[dict[str, object]] = []
    for manifest in sorted(steamapps.glob("appmanifest_*.acf")):
        meta = inventory_mod.parse_acf(manifest)
        installdir = str(meta.get("installdir") or "")
        if not installdir:
            continue
        name = str(meta.get("name") or installdir)
        if name.lower().startswith("proton "):
            continue
        if app_filter:
            haystack = " ".join([str(meta.get("appid") or ""), name, installdir]).lower()
            if app_filter.lower() not in haystack:
                continue
        if not (steamapps / "common" / installdir).exists():
            continue
        apps.append({"appid": meta.get("appid", ""), "name": name, "installdir": installdir})
    return apps


def collect_best_manifests(search_roots: list[Path]) -> dict[str, dict[str, object]]:
    best: dict[str, dict[str, object]] = {}
    for root in search_roots:
        if not root.exists():
            continue
        manifest_paths = set(root.glob("*/apps/*/source-roundtrip-manifest.json"))
        manifest_paths.update(root.glob("apps/*/source-roundtrip-manifest.json"))
        for path in sorted(manifest_paths):
            manifest = read_json(path)
            if not manifest:
                continue
            key = app_key(manifest.get("appid")) or app_key(manifest.get("app"))
            if not key:
                continue
            manifest = {**manifest, "manifest": str(path)}
            previous = best.get(key)
            if previous is None or manifest_score(manifest) > manifest_score(previous):
                best[key] = manifest
    return best


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--steamapps", type=Path, default=inventory_mod.DEFAULT_STEAMAPPS)
    parser.add_argument("--search-root", type=Path, action="append", default=[ROOT / "target"])
    parser.add_argument("--app", help="Substring filter over app id, name, or install dir")
    args = parser.parse_args()

    inventory_apps = steam_apps(args.steamapps, app_filter=args.app)
    manifests = collect_best_manifests(args.search_root)

    apps: list[dict[str, object]] = []
    full_count = 0
    covered_files = 0
    total_files = 0
    semantic_matches = 0
    for app in inventory_apps:
        appid = app_key(app.get("appid"))
        manifest = manifests.get(appid)
        total = int(manifest.get("appFileRoundtripTotal") or 0) if manifest else 0
        matched = int(manifest.get("appFileRoundtripMatched") or 0) if manifest else 0
        full = bool(manifest and manifest.get("fullAppByteIdentical") is True)
        if full:
            full_count += 1
        covered_files += matched
        total_files += total
        semantic = int(manifest.get("matchedFunctions") or 0) if manifest else 0
        semantic_matches += semantic
        apps.append(
            {
                "appid": app.get("appid"),
                "name": app.get("name"),
                "fullAppByteIdentical": full,
                "appFileRoundtripMatched": matched,
                "appFileRoundtripTotal": total,
                "matchedFunctions": semantic,
                "manifest": manifest.get("manifest") if manifest else None,
            }
        )

    report = {
        "schema": "mizuchi.steam-roundtrip-progress.v1",
        "steamapps": str(args.steamapps),
        "searchRoots": [str(path) for path in args.search_root],
        "inventoryAppCount": len(inventory_apps),
        "manifestAppCount": len(manifests),
        "fullAppByteIdenticalApps": full_count,
        "appFileRoundtripMatched": covered_files,
        "appFileRoundtripTotalFromManifests": total_files,
        "matchedFunctions": semantic_matches,
        "apps": apps,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
