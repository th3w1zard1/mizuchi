"""Export verified recovered-source rows into bounded source shards."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def iter_rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def is_exportable_match(row: dict[str, Any]) -> bool:
    status = row.get("status")
    differences = row.get("differences")
    return (
        (status == "matched" and differences == 0)
        or (status == "code-slice-matched" and differences == 0)
        or status == "source-shape-code-slice-matched"
    )


def source_path_for(row: dict[str, Any]) -> Path:
    source = row.get("source")
    if source:
        return Path(str(source))
    out_dir = row.get("outDir") or row.get("parentAttemptDir")
    if out_dir:
        candidate = Path(str(out_dir)) / "candidate.c"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"candidate source missing for {row.get('name')}: no source path in row")


def export_suffix_for(candidate_path: Path) -> str:
    suffix = candidate_path.suffix
    if suffix.lower() in {".c", ".asm", ".s"}:
        return suffix
    return ".txt"


def source_language_for(candidate_path: Path) -> str:
    suffix = candidate_path.suffix.lower()
    if suffix == ".c":
        return "c"
    if suffix == ".asm":
        return "masm"
    if suffix == ".s":
        return "gas"
    return "text"


def split_source_header(row: dict[str, Any], *, language: str) -> str:
    name = row.get("name")
    entry = row.get("entry")
    kind = row.get("kind")
    symbol = row.get("symbol")
    authority = authority_for(row)
    boundary = claim_boundary_for(row)
    if language == "masm":
        return "\n".join(
            [
                f"; {name} entry={entry} kind={kind} symbol={symbol}",
                f"; Authority: {authority}.",
                f"; Boundary: {boundary}.",
            ]
        )
    if language == "gas":
        return "\n".join(
            [
                f"# {name} entry={entry} kind={kind} symbol={symbol}",
                f"# Authority: {authority}.",
                f"# Boundary: {boundary}.",
            ]
        )
    return "\n".join(
        [
            "/*",
            f" * {name} entry={entry} kind={kind} symbol={symbol}",
            f" * Authority: {authority}.",
            f" * Boundary: {boundary}.",
            " */",
        ]
    )


def authority_for(row: dict[str, Any]) -> str:
    status = row.get("status")
    if status == "matched":
        return "full-object-function-match"
    if status == "source-shape-code-slice-matched":
        return "semantic-source-shape-code-slice"
    return "semantic-source-slice"


def claim_boundary_for(row: dict[str, Any]) -> str:
    if row.get("status") == "matched":
        return "objdiff-zero function/object candidate; still partial recovered source, not whole-program parity"
    return "bounded target code-slice objdiff/byte match; not full executable or full original object parity"


def export_recovered_source(
    summaries: list[Path],
    *,
    out_dir: Path,
    source_name: str = "simple_matches.c",
) -> dict[str, Any]:
    matched: list[dict[str, Any]] = []
    for summary in summaries:
        if summary.exists():
            matched.extend(row for row in iter_rows(summary) if is_exportable_match(row))
    matched = dedupe_best_matches(matched)
    if not matched:
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = out_dir / "simple_matches.manifest.json"
        manifest = {
            "schema": "reconkit.swkotor-recovered-source-shard.v1",
            "status": "empty",
            "summaries": [str(summary) for summary in summaries],
            "functionCount": 0,
            "functions": [],
            "authoritativeScope": "no verified function/source slices",
            "claimBoundary": "No verified recovered source rows were available to export.",
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {
            "schema": "reconkit.recovered-source-export.v1",
            "status": "empty",
            "manifest": str(manifest_path),
            "functionCount": 0,
            "claimBoundary": manifest["claimBoundary"],
        }

    chunks = [
        "/*",
        " * Generated from objdiff/code-slice-verified function candidates.",
        " * This combined view is for reading only; compile the split source tree",
        " * because per-function extern prototypes are not globally reconciled yet.",
        " * This is partial recovered source, not whole-program or full-object parity.",
        " */",
        "",
    ]
    manifest_rows: list[dict[str, Any]] = []
    build_units: list[dict[str, Any]] = []
    functions_dir = out_dir / "functions"
    functions_dir.mkdir(parents=True, exist_ok=True)
    for row in matched:
        name = str(row["name"])
        candidate_path = source_path_for(row)
        if not candidate_path.is_file():
            raise FileNotFoundError(f"candidate source missing for {name}: {candidate_path}")
        source = candidate_path.read_text(encoding="utf-8").strip()
        split_suffix = export_suffix_for(candidate_path)
        split_name = f"{row.get('entry', 'unknown')}_{name}{split_suffix}"
        split_path = functions_dir / split_name
        source_language = source_language_for(candidate_path)
        split_path.write_text(
            "\n".join(
                [
                    split_source_header(row, language=source_language),
                    source,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        candidate_compile = row.get("candidateCompile") if isinstance(row.get("candidateCompile"), dict) else {}
        build_unit = {
            "name": name,
            "entry": row.get("entry"),
            "symbol": row.get("symbol"),
            "language": source_language,
            "source": str(split_path),
            "originalSource": str(candidate_path),
            "sourceSha256": row.get("sourceSha256"),
            "sourceQuality": row.get("sourceQuality"),
            "sourceRecoveryScope": row.get("sourceRecoveryScope"),
            "compiler": row.get("compiler"),
            "compilerProfileName": row.get("compilerProfileName"),
            "compilerProfileArgs": row.get("compilerProfileArgs"),
            "verifiedObject": candidate_compile.get("object"),
            "verifiedCompileCommand": candidate_compile.get("command"),
            "verificationTier": row.get("verificationTier"),
            "claimBoundary": claim_boundary_for(row),
        }
        build_units.append(build_unit)
        chunks.extend(
            [
                f"/* {name} entry={row.get('entry')} kind={row.get('kind')} symbol={row.get('symbol')} authority={authority_for(row)} */",
                source,
                "",
            ]
        )
        manifest_rows.append(
            {
                "name": name,
                "entry": row.get("entry"),
                "kind": row.get("kind"),
                "symbol": row.get("symbol"),
                "status": row.get("status"),
                "differences": row.get("differences"),
                "authority": authority_for(row),
                "claimBoundary": claim_boundary_for(row),
                "verificationTier": row.get("verificationTier"),
                "sourceQuality": row.get("sourceQuality"),
                "sourceRecoveryScope": row.get("sourceRecoveryScope"),
                "sourceLanguage": source_language,
                "sourceSuffix": split_suffix,
                "source": str(candidate_path),
                "exportedSource": str(split_path),
                "verify": row.get("verifyReport") or row.get("searchReport"),
            }
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    source_path = out_dir / source_name
    manifest_path = out_dir / "simple_matches.manifest.json"
    build_manifest_path = out_dir / "build_manifest.json"
    source_path.write_text("\n".join(chunks), encoding="utf-8")
    build_manifest = {
        "schema": "reconkit.recovered-source-build-manifest.v1",
        "status": "complete",
        "functionCount": len(build_units),
        "claimBoundary": "Compile units reproduce verified function/source-slice objects only. This is not a whole executable link recipe.",
        "units": build_units,
    }
    build_manifest_path.write_text(json.dumps(build_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema": "reconkit.swkotor-recovered-source-shard.v1",
        "status": "complete",
        "summaries": [str(summary) for summary in summaries],
        "combinedSource": str(source_path),
        "sourceRoot": str(functions_dir),
        "buildManifest": str(build_manifest_path),
        "functionCount": len(manifest_rows),
        "authoritativeScope": "partial verified function/source slices only",
        "claimBoundary": "Exports may include bounded code-slice matches. They are source recovery evidence for named slices, not a whole-program rebuild or full original-object parity claim.",
        "functions": manifest_rows,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "schema": "reconkit.recovered-source-export.v1",
        "status": "complete",
        "source": str(source_path),
        "sourceRoot": str(functions_dir),
        "buildManifest": str(build_manifest_path),
        "manifest": str(manifest_path),
        "functionCount": len(manifest_rows),
        "claimBoundary": manifest["claimBoundary"],
    }


def profile_prompt_prefixes(profile_slug: str) -> tuple[str, ...]:
    if profile_slug in {"swkotor", "kotor"}:
        return ("swkotor_",)
    if profile_slug in {"jedi-academy", "jedi_academy", "jka"}:
        return ("jedi-academy_", "jka_")
    prefix = profile_slug.replace("_", "-")
    return (f"{prefix}_",)


def prompt_matches_profile(name: str, profile_slug: str) -> bool:
    return any(name.startswith(prefix) for prefix in profile_prompt_prefixes(profile_slug))


def entry_for_function_name(function_name: str) -> str:
    fn = function_name.strip()
    if fn.startswith("fcn."):
        return fn[4:]
    if fn.startswith("FUN_"):
        return fn[4:]
    return fn


def canonical_export_name(function_name: str) -> str:
    entry = entry_for_function_name(function_name)
    if entry and all(ch in "0123456789abcdefABCDEF" for ch in entry):
        return f"FUN_{entry.upper()}"
    return function_name


def iter_vacuum_matched_prompts(
    root: Path,
    *,
    profile_slug: str,
    queue_path: Path | None = None,
    prompts_dir: Path | None = None,
) -> Iterable[tuple[str, dict[str, Any]]]:
    queue_path = queue_path or root / "state" / "queue.json"
    prompts_dir = prompts_dir or root / "prompts"
    if not queue_path.is_file():
        return
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    for entry in queue.get("matched", []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "")
        if not name or not prompt_matches_profile(name, profile_slug):
            continue
        verify_path = prompts_dir / name / "build" / "build-and-verify.json"
        if not verify_path.is_file():
            continue
        verify = json.loads(verify_path.read_text(encoding="utf-8"))
        if verify.get("status") != "matched":
            continue
        yield name, verify


def count_vacuum_matched_prompts(root: Path, *, profile_slug: str) -> int:
    return sum(1 for _ in iter_vacuum_matched_prompts(root, profile_slug=profile_slug))


def vacuum_row_from_verify(prompt_name: str, verify: dict[str, Any]) -> dict[str, Any] | None:
    function_name = str(verify.get("function_name") or prompt_name.split("_", 1)[-1])
    source = verify.get("candidate_source")
    if not source:
        candidate = Path(str(verify.get("prompt") or prompt_name))
        # prompt field is name not path; resolve via prompts dir in caller if needed
        return None
    source_path = Path(str(source))
    if not source_path.is_file():
        return None
    entry = entry_for_function_name(function_name)
    return {
        "schema": "reconkit.vacuum-prompt-match.v1",
        "status": "matched",
        "differences": 0,
        "name": canonical_export_name(function_name),
        "entry": entry,
        "source": str(source_path),
        "kind": "vacuum-object-match",
        "prompt": prompt_name,
        "sourceQuality": "high-level-c",
    }


def collect_vacuum_prompt_matches(
    root: Path,
    *,
    profile_slug: str,
    out_path: Path,
    queue_path: Path | None = None,
    prompts_dir: Path | None = None,
) -> int:
    prompts_dir = prompts_dir or root / "prompts"
    rows: list[dict[str, Any]] = []
    for prompt_name, verify in iter_vacuum_matched_prompts(
        root,
        profile_slug=profile_slug,
        queue_path=queue_path,
        prompts_dir=prompts_dir,
    ):
        row = vacuum_row_from_verify(prompt_name, verify)
        if row is None:
            candidate = prompts_dir / prompt_name / "candidate.c"
            if candidate.is_file():
                function_name = str(verify.get("function_name") or prompt_name.split("_", 1)[-1])
                entry = entry_for_function_name(function_name)
                row = {
                    "schema": "reconkit.vacuum-prompt-match.v1",
                    "status": "matched",
                    "differences": 0,
                    "name": canonical_export_name(function_name),
                    "entry": entry,
                    "source": str(candidate),
                    "kind": "vacuum-object-match",
                    "prompt": prompt_name,
                    "sourceQuality": "high-level-c",
                }
        if row is not None:
            rows.append(row)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    return len(rows)


def dedupe_best_matches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_name: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        key = str(row.get("entry") or row.get("name") or "")
        if not key:
            continue
        if key not in best_by_name:
            best_by_name[key] = row
            order.append(key)
            continue
        if match_rank(row) < match_rank(best_by_name[key]):
            best_by_name[key] = row
    return [best_by_name[key] for key in order]


def match_rank(row: dict[str, Any]) -> tuple[int, int]:
    status = str(row.get("status") or "")
    quality = str(row.get("sourceQuality") or "")
    if status == "source-shape-code-slice-matched":
        return (0, 0)
    if quality == "high-level-c":
        return (1, 0)
    if quality == "inline-asm-c":
        return (2, 0)
    if quality == "byte-emission-asm":
        return (3, 0)
    return (4, 0)
