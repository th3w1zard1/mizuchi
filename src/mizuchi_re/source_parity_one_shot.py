"""Orchestrate source-parity one-shot pipeline stages for game binaries."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .source_export import collect_vacuum_prompt_matches, count_vacuum_matched_prompts
from .state import atomic_write_json
from .targets import resolve_target, sha256_file

ROOT = Path(__file__).resolve().parents[2]

STAGES: tuple[str, ...] = (
    "discover",
    "prepare",
    "inventory",
    "match-trivial",
    "match-reloc-wrappers",
    "export-source",
    "compile-source",
    "derive-coverage",
    "queue",
    "index-examples",
    "profile-corpus",
    "synthesize-candidates",
)

DEFAULT_SWKOTOR_BINARY = Path(
    "/run/media/brunner56/MyBook/SteamLibrary/steamapps/common/swkotor/swkotor.exe"
)
DEFAULT_JKA_BINARY = Path(
    "/run/media/brunner56/MyBook/SteamLibrary/steamapps/common/Jedi Academy/GameData/jamp.exe"
)

LEGACY_STAGE_ALIASES: dict[str, tuple[str, ...]] = {
    "match-reloc-wrappers": ("match-reloc",),
}


@dataclass(frozen=True)
class ProfileConfig:
    slug: str
    default_binary: Path
    unpack_dir: Path
    inventory_jsonl: Path
    inventory_summary: Path
    trivial_matches_dir: Path
    trivial_out_jsonl: Path
    trivial_summary: Path
    reloc_matches_dir: Path
    reloc_out_jsonl: Path
    reloc_summary: Path
    recovered_dir: Path
    compile_summary: Path
    coverage_json: Path
    queue_jsonl: Path
    index_out_dir: Path
    synthesis_out_dir: Path
    state_dir: Path
    text_section: str
    match_root: Path

    @staticmethod
    def for_slug(slug: str) -> "ProfileConfig":
        prefix = "swkotor" if slug in {"swkotor", "kotor"} else slug.replace("_", "-")
        trivial_dir = ROOT / f"target/{prefix}-trivial-matches"
        reloc_dir = ROOT / f"target/{prefix}-reloc-wrapper-matches"
        text_section = ".textV" if slug in {"swkotor", "kotor"} else ".textU"
        return ProfileConfig(
            slug=slug,
            default_binary=DEFAULT_SWKOTOR_BINARY if slug in {"swkotor", "kotor"} else DEFAULT_JKA_BINARY,
            unpack_dir=ROOT / f"target/{prefix}-unpack",
            inventory_jsonl=ROOT / f"target/{prefix}-unpack/facts/function-inventory.jsonl",
            inventory_summary=ROOT / f"target/{prefix}-unpack/facts/inventory-summary.json",
            trivial_matches_dir=trivial_dir,
            trivial_out_jsonl=trivial_dir / "summary.jsonl",
            trivial_summary=trivial_dir / "summary.json",
            reloc_matches_dir=reloc_dir,
            reloc_out_jsonl=reloc_dir / "summary.jsonl",
            reloc_summary=reloc_dir / "summary.json",
            recovered_dir=ROOT / f"target/{prefix}-recovered",
            compile_summary=ROOT / f"target/{prefix}-recovered/compile-summary.json",
            coverage_json=ROOT / f"target/{prefix}-recovered/coverage.json",
            queue_jsonl=ROOT / f"target/{prefix}-recovery-queue/queue.jsonl",
            index_out_dir=ROOT / f"target/source-parity-index/{prefix}",
            synthesis_out_dir=ROOT / f"target/source-parity-synthesis/{prefix}",
            state_dir=ROOT / f"target/source-parity-one-shot/{prefix}",
            text_section=text_section,
            match_root=ROOT / f"target/{prefix}-match",
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_script(script: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(ROOT / "scripts" / script), *args]
    return subprocess.run(cmd, cwd=ROOT, check=check, text=True, capture_output=True)

def append_event(state_dir: Path, event: dict[str, Any]) -> None:
    path = state_dir / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def load_state(state_path: Path) -> dict[str, Any]:
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        normalize_legacy_stages(state)
        return state
    return {
        "profile": "",
        "binaryPath": "",
        "binarySha256": "",
        "stages": {name: {"status": "pending"} for name in STAGES},
        "updatedAt": now_iso(),
    }


def normalize_legacy_stages(state: dict[str, Any]) -> None:
    """Map pre-orchestrator stage keys and heal failed reruns when artifacts exist."""
    stages = state.setdefault("stages", {})
    for canonical, legacy_names in LEGACY_STAGE_ALIASES.items():
        current = stages.get(canonical, {})
        if current.get("status") == "complete":
            continue
        for legacy in legacy_names:
            legacy_entry = stages.get(legacy, {})
            if legacy_entry.get("status") != "complete":
                continue
            stages[canonical] = {
                **legacy_entry,
                "status": "complete",
                "migratedFrom": legacy,
            }
            break


def save_state(state_path: Path, state: dict[str, Any]) -> None:
    state["updatedAt"] = now_iso()
    atomic_write_json(state_path, state)


def mark_stage(state: dict[str, Any], name: str, status: str, **extra: Any) -> None:
    entry = state.setdefault("stages", {}).setdefault(name, {})
    if status == "complete":
        for stale in ("reused", "error"):
            if stale not in extra:
                entry.pop(stale, None)
    entry["status"] = status
    entry["updatedAt"] = now_iso()
    entry.update(extra)


def stage_index(name: str) -> int:
    return STAGES.index(name)


def should_run_stage(state: dict[str, Any], name: str, resume: bool) -> bool:
    if not resume:
        return True
    stages = state.get("stages", {})
    status = (stages.get(name) or {}).get("status")
    if status == "complete":
        return False
    for legacy in LEGACY_STAGE_ALIASES.get(name, ()):
        if (stages.get(legacy) or {}).get("status") == "complete":
            return False
    return status != "complete"


def resolve_profile_binary(input_path: Path, profile_slug: str) -> Path:
    """Resolve the analysis binary for a profile (e.g. JKA launcher -> GameData/jamp.exe)."""
    path = input_path.expanduser().resolve()
    if profile_slug in {"jedi-academy", "jedi_academy"}:
        candidates: list[Path] = []
        roots: list[Path] = []
        if path.is_file():
            roots.append(path.parent)
            if path.name.lower() == "jediacademy.exe":
                roots.append(path.parent / "GameData")
        elif path.is_dir():
            roots.append(path)
            roots.append(path / "GameData")
        for root in roots:
            if not root.is_dir():
                continue
            for name in ("jamp.exe", "jasp.exe"):
                exe = root / name
                if exe.is_file():
                    candidates.append(exe)
        if candidates:
            return sorted(candidates, key=lambda item: item.stat().st_size, reverse=True)[0]
    return resolve_target(path)


def reconcile_binary_identity(state: dict[str, Any], binary: Path, profile: ProfileConfig) -> Path:
    """Invalidate stale stage receipts when the resolved analysis binary changes."""
    target = resolve_profile_binary(binary, profile.slug)
    digest = sha256_file(target)
    prior = state.get("binarySha256")
    if prior and prior != digest:
        state["stages"] = {name: {"status": "pending"} for name in STAGES}
        state.pop("binaryPath", None)
        state.pop("binarySha256", None)
        for path in (
            profile.inventory_jsonl,
            profile.trivial_summary,
            profile.reloc_summary,
            profile.coverage_json,
        ):
            if path.exists():
                path.unlink()
    return target


def detect_profile(input_path: Path) -> str:
    name = input_path.name.lower()
    if "jedi" in name or "academy" in name:
        return "jedi-academy"
    if "swkotor" in name or "kotor" in name:
        return "swkotor"
    if input_path.is_dir():
        if (input_path / "swkotor.exe").exists():
            return "swkotor"
        if (input_path / "JediAcademy.exe").exists():
            return "jedi-academy"
    return "swkotor"

def stage_discover(binary: Path, profile: ProfileConfig, state: dict[str, Any]) -> None:
    target = resolve_profile_binary(binary, profile.slug)
    digest = sha256_file(target)
    state["profile"] = profile.slug
    state["binaryPath"] = str(target)
    state["binarySha256"] = digest
    mark_stage(state, "discover", "complete", binaryPath=str(target), sha256=digest)


def _pe_packed(path: Path) -> bool:
    script = ROOT / "scripts" / "normalize-binary.py"
    if not script.exists() or path.suffix.lower() not in {".exe", ".dll"}:
        return False
    proc = subprocess.run(
        [sys.executable, str(script), str(path), "--detect-only"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return False
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False
    return bool((payload.get("detection") or {}).get("packed"))


def _prepare_analysis_image(dest: Path, *, timeout: int = 900) -> tuple[Path | None, str | None, dict[str, Any]]:
    """Produce an unpacked PE for static analysis when the original is packed."""
    from .tools import inspect_capabilities, resolve_steamless_cli

    meta: dict[str, Any] = {}
    if dest.suffix.lower() not in {".exe", ".dll"}:
        return None, None, meta

    steamless_out = dest.parent / f"{dest.name}.unpacked.exe"
    capabilities = inspect_capabilities(ROOT)
    mono = ((capabilities.get("tools") or {}).get("mono") or {}).get("available")
    steamless = resolve_steamless_cli(ROOT)
    if mono and steamless is not None:
        if not steamless_out.exists():
            proc = subprocess.run(
                [
                    "mono",
                    str(steamless),
                    "--quiet",
                    "--keepbind",
                    "--dumppayload",
                    "--dumpdrmp",
                    str(dest),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
            meta["steamlessReturnCode"] = proc.returncode
            if proc.stdout:
                meta["steamlessStdout"] = proc.stdout[-2000:]
            if proc.stderr:
                meta["steamlessStderr"] = proc.stderr[-2000:]
        if steamless_out.exists():
            return steamless_out, "steamless-unpacked-pe", meta

    norm_script = ROOT / "scripts" / "normalize-binary.py"
    norm_out = dest.parent / f"{dest.stem}.unpacked.exe"
    if norm_script.exists() and not norm_out.exists():
        proc = subprocess.run(
            [sys.executable, str(norm_script), str(dest), "--out", str(norm_out)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        meta["normalizeReturnCode"] = proc.returncode
        if proc.stderr:
            meta["normalizeStderr"] = proc.stderr[-2000:]
    if norm_out.exists():
        return norm_out, "normalize-binary", meta
    return None, None, meta


def stage_prepare(binary: Path, profile: ProfileConfig, state: dict[str, Any]) -> None:
    target = Path(state.get("binaryPath") or resolve_target(binary))
    unpack = profile.unpack_dir
    unpack.mkdir(parents=True, exist_ok=True)
    dest = unpack / target.name
    if not dest.exists() or sha256_file(dest) != state.get("binarySha256"):
        shutil.copy2(target, dest)

    extra: dict[str, Any] = {"unpackDir": str(unpack), "copiedTo": str(dest)}
    if _pe_packed(dest):
        analysis_path, transform, transform_meta = _prepare_analysis_image(dest)
        extra.update(transform_meta)
        if analysis_path is not None:
            extra["analysisBinary"] = str(analysis_path)
            extra["analysisBinarySha256"] = sha256_file(analysis_path)
            extra["transform"] = transform
        else:
            extra["transformAttempted"] = True
            extra["transformResult"] = "not-produced"
    mark_stage(state, "prepare", "complete", **extra)


def inventory_binary(profile: ProfileConfig, state: dict[str, Any]) -> Path:
    """Prefer prepare-stage analysis image, then globbed unpack, then prepared copy."""
    prepare = (state.get("stages") or {}).get("prepare") or {}
    analysis = prepare.get("analysisBinary")
    if analysis and Path(analysis).exists():
        return Path(analysis)
    unpack = profile.unpack_dir
    unpacked = sorted(unpack.glob("*.unpacked.exe"))
    if unpacked:
        return unpacked[-1]
    copied = prepare.get("copiedTo")
    if copied and Path(copied).exists():
        return Path(copied)
    return Path(state["binaryPath"])


def stage_inventory(profile: ProfileConfig, state: dict[str, Any], refresh: bool) -> None:
    jsonl = profile.inventory_jsonl
    digest = state.get("binarySha256")
    analysis_binary = inventory_binary(profile, state)
    analysis_digest = sha256_file(analysis_binary)
    inv_stage = (state.get("stages") or {}).get("inventory") or {}
    if (
        jsonl.exists()
        and not refresh
        and inv_stage.get("binarySha256") == digest
        and inv_stage.get("analysisBinarySha256") == analysis_digest
    ):
        mark_stage(
            state,
            "inventory",
            "complete",
            inventory=str(jsonl),
            reused=True,
            binarySha256=digest,
            analysisBinarySha256=analysis_digest,
            analysisBinary=str(analysis_binary),
        )
        return
    if jsonl.exists():
        jsonl.unlink()
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    binary = inventory_binary(profile, state)
    ghidra = os.environ.get("GHIDRA_INSTALL_DIR", "")
    analyze = Path(ghidra) / "support" / "analyzeHeadless" if ghidra else None
    script_dir = ROOT / "scripts" / "ghidra"
    if analyze and analyze.exists() and (script_dir / "ExportFunctionInventory.java").exists():
        project = profile.unpack_dir / "ghidra-project"
        project.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(analyze),
            str(project),
            profile.slug,
            "-import",
            str(binary),
            "-scriptPath",
            str(script_dir),
            "-deleteProject",
            "-postScript",
            "ExportFunctionInventory.java",
            str(jsonl),
        ]
        subprocess.run(cmd, cwd=ROOT, check=False)
    if not jsonl.exists():
        raise RuntimeError(
            f"inventory missing at {jsonl}; set GHIDRA_INSTALL_DIR or provide function-inventory.jsonl"
        )
    mark_stage(
        state,
        "inventory",
        "complete",
        inventory=str(jsonl),
        binarySha256=digest,
        analysisBinarySha256=analysis_digest,
        analysisBinary=str(analysis_binary),
    )

def stage_match_trivial(profile: ProfileConfig, state: dict[str, Any]) -> None:
    result = run_script(
        "swkotor-match-trivial.py",
        "--inventory",
        str(profile.inventory_jsonl),
        "--out",
        str(profile.trivial_out_jsonl),
        "--summary",
        str(profile.trivial_summary),
        "--text-section",
        profile.text_section,
        "--match-root",
        str(profile.match_root),
        "--limit",
        "0",
    )
    mark_stage(state, "match-trivial", "complete", returncode=result.returncode)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "match-trivial failed")


def stage_match_reloc(profile: ProfileConfig, state: dict[str, Any]) -> None:
    result = run_script(
        "swkotor-match-reloc-wrappers.py",
        "--inventory",
        str(profile.inventory_jsonl),
        "--out",
        str(profile.reloc_out_jsonl),
        "--summary",
        str(profile.reloc_summary),
        "--text-section",
        profile.text_section,
        "--match-root",
        str(profile.reloc_matches_dir),
        "--limit",
        "0",
    )
    mark_stage(state, "match-reloc-wrappers", "complete", returncode=result.returncode)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "match-reloc-wrappers failed")


def stage_export_source(profile: ProfileConfig, state: dict[str, Any]) -> None:
    vacuum_jsonl = profile.state_dir / "vacuum-matches.jsonl"
    vacuum_count = collect_vacuum_prompt_matches(
        ROOT,
        profile_slug=profile.slug,
        out_path=vacuum_jsonl,
    )
    summaries: list[Path] = []
    if profile.trivial_out_jsonl.exists() and profile.trivial_out_jsonl.stat().st_size > 0:
        summaries.append(profile.trivial_out_jsonl)
    if profile.reloc_out_jsonl.exists() and profile.reloc_out_jsonl.stat().st_size > 0:
        summaries.append(profile.reloc_out_jsonl)
    if vacuum_count > 0:
        summaries.append(vacuum_jsonl)
    summaries.extend(_synthesis_summary_paths(profile))
    if not summaries:
        from .source_export import export_recovered_source

        export_recovered_source([], out_dir=profile.recovered_dir)
        mark_stage(
            state,
            "export-source",
            "complete",
            returncode=0,
            vacuumMatchCount=0,
            summaryCount=0,
            emptyExport=True,
        )
        return
    args = ["--out-dir", str(profile.recovered_dir)]
    for summary in summaries:
        args.extend(["--summary", str(summary)])
    result = run_script("swkotor-export-matched-source.py", *args)
    mark_stage(
        state,
        "export-source",
        "complete",
        returncode=result.returncode,
        vacuumMatchCount=vacuum_count,
        programmaticMatchCount=_programmatic_match_count(profile),
        synthesisMatchCount=_synthesis_exportable_count(profile),
        summaryCount=len(summaries),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "export-source failed")


def stage_compile_source(profile: ProfileConfig, state: dict[str, Any]) -> None:
    manifest = profile.recovered_dir / "simple_matches.manifest.json"
    if not manifest.is_file():
        raise RuntimeError(f"compile-source: missing export manifest at {manifest}")
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    if int(manifest_data.get("functionCount") or 0) == 0:
        empty_summary = {
            "schema": "mizuchi.recovered-source-compile-summary.v1",
            "compiled": 0,
            "failed": 0,
            "verifiedMatchedFunctionCount": 0,
            "status": "empty",
        }
        profile.compile_summary.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(profile.compile_summary, empty_summary)
        mark_stage(state, "compile-source", "complete", returncode=0, vacuumMatchCount=0, emptyCompile=True)
        return
    export_stage = (state.get("stages") or {}).get("export-source") or {}
    result = run_script(
        "swkotor-compile-recovered-shard.py",
        "--manifest",
        str(manifest),
        "--out-dir",
        str(profile.recovered_dir / "objects"),
        "--summary",
        str(profile.compile_summary),
        check=False,
    )
    mark_stage(
        state,
        "compile-source",
        "complete",
        returncode=result.returncode,
        vacuumMatchCount=int(export_stage.get("vacuumMatchCount") or 0),
    )
    if result.returncode != 0:
        if profile.compile_summary.exists():
            summary = json.loads(profile.compile_summary.read_text(encoding="utf-8"))
            if int(summary.get("compiled") or 0) > 0:
                return
        raise RuntimeError(result.stderr or "compile-source failed")

def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _programmatic_match_count(profile: ProfileConfig) -> int:
    total = 0
    for summary_path in (profile.trivial_summary, profile.reloc_summary):
        if not summary_path.is_file():
            continue
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        total += int(data.get("matched") or 0)
    return total


def _synthesis_summary_paths(profile: ProfileConfig) -> list[Path]:
    out_dir = profile.synthesis_out_dir
    paths: list[Path] = []
    for name in ("code-slice-matches.jsonl", "accepted.jsonl"):
        path = out_dir / name
        if path.is_file() and path.stat().st_size > 0:
            paths.append(path)
    return paths


def _synthesis_exportable_count(profile: ProfileConfig) -> int:
    from .source_export import is_exportable_match, iter_rows

    total = 0
    for path in _synthesis_summary_paths(profile):
        total += sum(1 for row in iter_rows(path) if is_exportable_match(row))
    return total


def stage_derive_coverage(profile: ProfileConfig, state: dict[str, Any]) -> None:
    function_count = _count_jsonl(profile.inventory_jsonl)
    verified = 0
    if profile.compile_summary.exists():
        summary = json.loads(profile.compile_summary.read_text(encoding="utf-8"))
        verified = int(
            summary.get("verifiedMatchedFunctionCount")
            or summary.get("compiled")
            or summary.get("matched")
            or 0
        )
    manifest_path = profile.recovered_dir / "simple_matches.manifest.json"
    if manifest_path.exists():
        manifest_count = int(json.loads(manifest_path.read_text(encoding="utf-8")).get("functionCount") or 0)
        verified = max(verified, manifest_count)
    if verified == 0 and profile.trivial_summary.exists():
        trivial = json.loads(profile.trivial_summary.read_text(encoding="utf-8"))
        verified = int(trivial.get("matchedCount") or trivial.get("matched") or 0)
    remaining = max(function_count - verified, 0)
    ratio = (verified / function_count) if function_count else 0.0
    coverage = {
        "profile": profile.slug,
        "functionCount": function_count,
        "verifiedMatchedFunctionCount": verified,
        "remainingFunctions": remaining,
        "verifiedRatio": ratio,
        "derivedAt": now_iso(),
    }
    profile.coverage_json.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(profile.coverage_json, coverage)
    if not profile.inventory_summary.exists() and function_count:
        atomic_write_json(
            profile.inventory_summary,
            {"functionCount": function_count, "inventory": str(profile.inventory_jsonl)},
        )
    mark_stage(
        state,
        "derive-coverage",
        "complete",
        coverage=str(profile.coverage_json),
        verifiedRatio=ratio,
        verifiedMatchedFunctionCount=verified,
    )


def stage_queue(profile: ProfileConfig, state: dict[str, Any]) -> None:
    out_dir = profile.queue_jsonl.parent
    args = [
        "--inventory",
        str(profile.inventory_jsonl),
        "--out-dir",
        str(out_dir),
        "--text-section",
        profile.text_section,
    ]
    if profile.trivial_out_jsonl.exists():
        args.extend(["--summary", str(profile.trivial_out_jsonl)])
    if profile.reloc_out_jsonl.exists():
        args.extend(["--summary", str(profile.reloc_out_jsonl)])
    result = run_script("swkotor-recovery-queue.py", *args)
    mark_stage(state, "queue", "complete", returncode=result.returncode)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "queue failed")

def stage_index_examples(profile: ProfileConfig, state: dict[str, Any]) -> None:
    result = run_script(
        "source-parity-feature-index.py",
        "--inventory",
        str(profile.inventory_jsonl),
        "--queue",
        str(profile.queue_jsonl),
        "--out-dir",
        str(profile.index_out_dir),
    )
    mark_stage(state, "index-examples", "complete", returncode=result.returncode)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "index-examples failed")


def stage_profile_corpus(profile: ProfileConfig, state: dict[str, Any]) -> None:
    result = run_script("source-parity-profile-corpus.py", "--max-cases", "50")
    mark_stage(state, "profile-corpus", "complete", returncode=result.returncode)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "profile-corpus failed")


def stage_synthesize(
    profile: ProfileConfig,
    state: dict[str, Any],
    limit: int,
    max_attempts_per_function: int,
    max_attempts_per_function_policy: str,
) -> None:
    args = [
        "--queue",
        str(profile.queue_jsonl),
        "--inventory",
        str(profile.inventory_jsonl),
        "--remaining-features",
        str(profile.index_out_dir / "remaining-features.jsonl"),
        "--retrieval",
        str(profile.index_out_dir / "retrieval.jsonl"),
        "--out-dir",
        str(profile.synthesis_out_dir),
        "--limit",
        str(limit),
    ]
    args.extend(["--max-attempts-per-function", str(max_attempts_per_function)])
    args.extend(["--max-attempts-per-function-policy", max_attempts_per_function_policy])
    if profile.trivial_out_jsonl.exists():
        args.extend(["--matched-summary", str(profile.trivial_out_jsonl)])
    if profile.reloc_out_jsonl.exists():
        args.extend(["--matched-summary", str(profile.reloc_out_jsonl)])
    # JKA inventory functions are mostly byte-pattern stubs; semantic-only skips all of them.
    if profile.slug not in {"jedi-academy", "jedi_academy"}:
        args.append("--semantic-only")
    result = run_script("source-parity-synthesize.py", *args)
    summary = read_json(profile.synthesis_out_dir / "summary.json") if (profile.synthesis_out_dir / "summary.json").is_file() else {}
    mark_kwargs = {
        "returncode": result.returncode,
        "synthesisLimit": limit,
        "synthesisMaxAttemptsPerFunction": max_attempts_per_function,
        "synthesisMaxAttemptsPerFunctionPolicy": max_attempts_per_function_policy,
        "synthesisMatchCount": _synthesis_exportable_count(profile),
    }
    if isinstance(summary, dict):
        if summary.get("attemptLimitPolicy") is not None:
            mark_kwargs["synthesisAttemptLimitPolicy"] = summary.get("attemptLimitPolicy")
        if summary.get("attemptLimitDistribution") is not None:
            mark_kwargs["synthesisAttemptLimitDistribution"] = summary.get("attemptLimitDistribution")
        if summary.get("attemptLimitReasonDistribution") is not None:
            mark_kwargs["synthesisAttemptLimitReasonDistribution"] = summary.get("attemptLimitReasonDistribution")
    mark_stage(
        state,
        "synthesize-candidates",
        "complete",
        **mark_kwargs,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "synthesize-candidates failed")

STAGE_RUNNERS: dict[str, Callable[..., None]] = {}


def _register_runners() -> None:
    STAGE_RUNNERS.update(
        {
            "discover": lambda ctx: stage_discover(ctx["binary"], ctx["profile"], ctx["state"]),
            "prepare": lambda ctx: stage_prepare(ctx["binary"], ctx["profile"], ctx["state"]),
            "inventory": lambda ctx: stage_inventory(ctx["profile"], ctx["state"], ctx["refresh_inventory"]),
            "match-trivial": lambda ctx: stage_match_trivial(ctx["profile"], ctx["state"]),
            "match-reloc-wrappers": lambda ctx: stage_match_reloc(ctx["profile"], ctx["state"]),
            "export-source": lambda ctx: stage_export_source(ctx["profile"], ctx["state"]),
            "compile-source": lambda ctx: stage_compile_source(ctx["profile"], ctx["state"]),
            "derive-coverage": lambda ctx: stage_derive_coverage(ctx["profile"], ctx["state"]),
            "queue": lambda ctx: stage_queue(ctx["profile"], ctx["state"]),
            "index-examples": lambda ctx: stage_index_examples(ctx["profile"], ctx["state"]),
            "profile-corpus": lambda ctx: stage_profile_corpus(ctx["profile"], ctx["state"]),
            "synthesize-candidates": lambda ctx: stage_synthesize(
                ctx["profile"],
                ctx["state"],
                ctx["synthesis_limit"],
                ctx["synthesis_max_attempts_per_function"],
                ctx["synthesis_max_attempts_per_function_policy"],
            ),
        }
    )


def write_report(profile: ProfileConfig, state: dict[str, Any], report_path: Path) -> None:
    coverage = {}
    if profile.coverage_json.exists():
        coverage = json.loads(profile.coverage_json.read_text(encoding="utf-8"))
    report = {
        "profile": profile.slug,
        "binaryPath": state.get("binaryPath"),
        "binarySha256": state.get("binarySha256"),
        "stages": state.get("stages", {}),
        "coverage": coverage,
        "verifiedRatio": coverage.get("verifiedRatio"),
        "functionCount": coverage.get("functionCount"),
        "verifiedMatchedFunctionCount": coverage.get("verifiedMatchedFunctionCount"),
        "remainingFunctions": coverage.get("remainingFunctions"),
        "generatedAt": now_iso(),
    }
    atomic_write_json(report_path, report)

def run_pipeline(
    input_path: Path,
    *,
    profile_slug: str | None = None,
    resume: bool = False,
    stop_after: str | None = None,
    refresh_inventory: bool = False,
    refresh_prepare: bool = False,
    synthesis_limit: int = 25,
    synthesis_max_attempts_per_function: int = 0,
    synthesis_max_attempts_per_function_policy: str = "uniform",
) -> dict[str, Any]:
    _register_runners()
    slug = profile_slug or detect_profile(input_path)
    profile = ProfileConfig.for_slug(slug)
    state_path = profile.state_dir / "state.json"
    report_path = profile.state_dir / "report.json"
    state = load_state(state_path)
    binary = reconcile_binary_identity(state, input_path, profile)
    ctx = {
        "binary": binary,
        "profile": profile,
        "state": state,
        "refresh_inventory": refresh_inventory,
        "refresh_prepare": refresh_prepare,
        "synthesis_limit": synthesis_limit,
        "synthesis_max_attempts_per_function": synthesis_max_attempts_per_function,
        "synthesis_max_attempts_per_function_policy": synthesis_max_attempts_per_function_policy,
        "force_export_downstream": False,
    }
    stop_idx = stage_index(stop_after) if stop_after else len(STAGES) - 1
    for idx, name in enumerate(STAGES):
        if idx > stop_idx:
            break
        force_stage = False
        if name == "inventory" and ctx["refresh_inventory"]:
            force_stage = True
        inv_stage = (state.get("stages") or {}).get("inventory") or {}
        prep_stage = (state.get("stages") or {}).get("prepare") or {}
        if name == "inventory" and inv_stage.get("status") == "complete":
            if inv_stage.get("binarySha256") != state.get("binarySha256"):
                force_stage = True
            elif inv_stage.get("analysisBinarySha256") != prep_stage.get("analysisBinarySha256"):
                force_stage = True
        if name == "prepare":
            if ctx["refresh_prepare"]:
                force_stage = True
            elif prep_stage.get("status") == "complete" and not prep_stage.get("analysisBinary"):
                copied = prep_stage.get("copiedTo")
                if copied and _pe_packed(Path(copied)):
                    force_stage = True
        vacuum_matched = count_vacuum_matched_prompts(ROOT, profile_slug=profile.slug)
        export_stage = (state.get("stages") or {}).get("export-source") or {}
        compile_stage = (state.get("stages") or {}).get("compile-source") or {}
        stored_vacuum = int(export_stage.get("vacuumMatchCount") or 0)
        programmatic_matched = _programmatic_match_count(profile)
        stored_programmatic = int(export_stage.get("programmaticMatchCount") or 0)
        synthesis_matched = _synthesis_exportable_count(profile)
        stored_synthesis = int(export_stage.get("synthesisMatchCount") or 0)
        if vacuum_matched > stored_vacuum and name == "export-source":
            force_stage = True
            ctx["force_export_downstream"] = True
        if programmatic_matched > stored_programmatic and name == "export-source":
            force_stage = True
            ctx["force_export_downstream"] = True
        if synthesis_matched > stored_synthesis and name == "export-source":
            force_stage = True
            ctx["force_export_downstream"] = True
        synth_stage = (state.get("stages") or {}).get("synthesize-candidates") or {}
        if (
            name == "synthesize-candidates"
            and int(synth_stage.get("synthesisLimit") or 0) < int(synthesis_limit)
        ):
            force_stage = True
        if (
            name == "synthesize-candidates"
            and int(synth_stage.get("synthesisMaxAttemptsPerFunction") or 0)
            < int(synthesis_max_attempts_per_function)
        ):
            force_stage = True
        if (
            name == "synthesize-candidates"
            and (synth_stage.get("synthesisMaxAttemptsPerFunctionPolicy") or "uniform")
            != synthesis_max_attempts_per_function_policy
        ):
            force_stage = True
        if ctx.get("force_export_downstream") and name in {"compile-source", "derive-coverage"}:
            force_stage = True
        if name == "compile-source":
            if int(export_stage.get("vacuumMatchCount") or 0) > int(compile_stage.get("vacuumMatchCount") or 0):
                force_stage = True
        if name == "derive-coverage":
            manifest_path = profile.recovered_dir / "simple_matches.manifest.json"
            if manifest_path.is_file():
                manifest_count = int(
                    json.loads(manifest_path.read_text(encoding="utf-8")).get("functionCount") or 0
                )
                derive_stage = (state.get("stages") or {}).get("derive-coverage") or {}
                coverage_verified = int(derive_stage.get("verifiedMatchedFunctionCount") or 0)
                if manifest_count > coverage_verified:
                    force_stage = True
        if not should_run_stage(state, name, resume) and not force_stage:
            continue
        mark_stage(state, name, "running")
        save_state(state_path, state)
        append_event(profile.state_dir, {"stage": name, "status": "running", "at": now_iso()})
        try:
            STAGE_RUNNERS[name](ctx)
            save_state(state_path, state)
            append_event(profile.state_dir, {"stage": name, "status": "complete", "at": now_iso()})
        except Exception as exc:
            mark_stage(state, name, "failed", error=str(exc))
            save_state(state_path, state)
            append_event(profile.state_dir, {"stage": name, "status": "failed", "error": str(exc), "at": now_iso()})
            write_report(profile, state, report_path)
            raise
    write_report(profile, state, report_path)
    return json.loads(report_path.read_text(encoding="utf-8"))


def self_check() -> dict[str, Any]:
    scripts = [
        "swkotor-match-trivial.py",
        "swkotor-match-reloc-wrappers.py",
        "swkotor-export-matched-source.py",
        "swkotor-compile-recovered-shard.py",
        "swkotor-recovery-queue.py",
        "source-parity-feature-index.py",
        "source-parity-profile-corpus.py",
        "source-parity-synthesize.py",
    ]
    missing = [name for name in scripts if not (ROOT / "scripts" / name).exists()]
    ghidra_script = ROOT / "scripts" / "ghidra" / "ExportFunctionInventory.java"
    return {
        "ok": not missing and ghidra_script.exists(),
        "missingScripts": missing,
        "ghidraExportScript": str(ghidra_script),
        "ghidraExportScriptPresent": ghidra_script.exists(),
        "stages": list(STAGES),
    }

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Source parity one-shot pipeline orchestrator")
    parser.add_argument("input", type=Path, nargs="?", help="Game binary or install folder")
    parser.add_argument("--profile", help="Profile slug (swkotor, jedi-academy)")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--stop-after", choices=STAGES)
    parser.add_argument("--refresh-inventory", action="store_true")
    parser.add_argument("--refresh-prepare", action="store_true")
    parser.add_argument("--synthesis-limit", type=int, default=25)
    parser.add_argument(
        "--synthesis-max-attempts-per-function",
        type=int,
        default=0,
        help=(
            "Maximum generated candidate attempts per synthesis function. "
            "0 uses source-parity-synthesize's --max-variants-per-function fallback."
        ),
    )
    parser.add_argument(
        "--synthesis-max-attempts-per-function-policy",
        choices=["uniform", "adaptive"],
        default="uniform",
        help="uniform keeps a fixed per-function cap; adaptive reduces caps for partial/source-slice rows.",
    )
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    if args.self_check:
        result = self_check()
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    if args.input is None:
        parser.error("input is required unless --self-check is set")
    try:
        report = run_pipeline(
            args.input,
            profile_slug=args.profile,
            resume=args.resume,
            stop_after=args.stop_after,
            refresh_inventory=args.refresh_inventory,
            refresh_prepare=args.refresh_prepare,
            synthesis_limit=args.synthesis_limit,
            synthesis_max_attempts_per_function=args.synthesis_max_attempts_per_function,
            synthesis_max_attempts_per_function_policy=args.synthesis_max_attempts_per_function_policy,
        )
    except Exception as exc:
        print(f"source-parity-one-shot failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
