"""Snapshot previously verified recovery artifacts for a target."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def matching_source_parity_reports(binary_sha256: str) -> list[Path]:
    base = ROOT / "target/source-parity-one-shot"
    if not base.exists():
        return []
    reports: list[Path] = []
    for report in base.glob("*/report.json"):
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(data.get("binarySha256")) == binary_sha256:
            reports.append(report)
    return sorted(reports)


def collect_snapshot_sources(report_path: Path) -> list[Path]:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    paths = {
        report_path,
        report_path.with_name("state.json"),
        report_path.with_name("events.jsonl"),
    }
    coverage = data.get("coverage") or {}
    index = data.get("index") or {}
    profile = data.get("profileCorpus") or {}
    synthesis = data.get("synthesis") or {}

    for value in [
        coverage.get("compileSummary"),
        coverage.get("manifest"),
        coverage.get("inventory"),
        index.get("matchedExamplesPath"),
        index.get("remainingFeaturesPath"),
        index.get("retrievalPath"),
        index.get("strategyPath"),
        profile.get("selectedCasesPath"),
        profile.get("summaryJsonl"),
        profile.get("summaryTsv"),
        synthesis.get("attemptsPath"),
        synthesis.get("acceptedPath"),
    ]:
        if value:
            paths.add(Path(value))

    recovered_dir = ROOT / "target/swkotor-recovered"
    if recovered_dir.exists():
        paths.add(recovered_dir)
    trivial_dir = ROOT / "target/swkotor-trivial-matches"
    if trivial_dir.exists():
        paths.add(trivial_dir)
    reloc_dir = ROOT / "target/swkotor-reloc-wrapper-matches"
    if reloc_dir.exists():
        paths.add(reloc_dir)
    synthesis_dir = ROOT / "target/source-parity-synthesis/swkotor"
    if synthesis_dir.exists():
        paths.add(synthesis_dir)
    return sorted(paths, key=lambda path: str(path))


def snapshot_existing_recovery(binary_sha256: str, out_dir: Path, label: str) -> dict[str, Any]:
    reports = matching_source_parity_reports(binary_sha256)
    if not reports:
        return {
            "status": "missing",
            "reason": "no matching source-parity report found for target sha256",
            "binarySha256": binary_sha256,
            "label": label,
        }

    report = reports[0]
    destination = out_dir / f"{label}"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    manifest_rows: list[dict[str, Any]] = []
    copied_roots: list[str] = []
    for source in collect_snapshot_sources(report):
        if not source.exists():
            continue
        rel = source.relative_to(ROOT)
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        copied_roots.append(str(rel))
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
            for child in sorted(path for path in target.rglob("*") if path.is_file()):
                manifest_rows.append(
                    {
                        "path": str(child.relative_to(destination)),
                        "size": child.stat().st_size,
                        "sha256": sha256_file(child),
                    }
                )
        else:
            shutil.copy2(source, target)
            manifest_rows.append(
                {
                    "path": str(target.relative_to(destination)),
                    "size": target.stat().st_size,
                    "sha256": sha256_file(target),
                }
            )

    manifest = {
        "schema": "mizuchi.recovery-snapshot.v1",
        "label": label,
        "binarySha256": binary_sha256,
        "sourceParityReport": str(report.relative_to(ROOT)),
        "destination": str(destination),
        "copiedRoots": copied_roots,
        "fileCount": len(manifest_rows),
        "files": manifest_rows,
    }
    manifest_path = destination / "snapshot-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "status": "snapshotted",
        "label": label,
        "destination": str(destination),
        "manifest": str(manifest_path),
        "fileCount": len(manifest_rows),
        "sourceParityReport": str(report),
    }
