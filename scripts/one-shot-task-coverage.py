#!/usr/bin/env python3
"""Report one-shot-source reconstruction task coverage across Mizuchi prompts."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise SystemExit(f"JSON root must be object: {path}")
    return data


def read_json_optional(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return read_json(path)


def yaml_scalar_unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        try:
            return json.loads(value)
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


def prompt_dirs(prompts_dir: Path) -> list[Path]:
    if not prompts_dir.exists():
        return []
    return sorted(path for path in prompts_dir.iterdir() if (path / "settings.yaml").is_file())


def package_task_key(path: str) -> str:
    return str(Path(path).as_posix()).strip("/")


def build_report_status(report: dict[str, Any]) -> str:
    if not report:
        return "none"
    if report.get("status") != "matched":
        return str(report.get("status") or "unknown")
    method = report.get("method")
    if method == "objdiff":
        return "matched"
    if method in {"custom", "cmp"} and report.get("byte_identical") is True:
        return "matched"
    return "unaccepted-match"


def candidate_present(prompt_dir: Path, case_data: dict[str, str]) -> bool:
    raw = case_data.get("candidateSourcePath") or "prompt:/candidate.c"
    if raw.startswith("prompt:/"):
        return (prompt_dir / raw.removeprefix("prompt:/")).is_file()
    return Path(raw).is_file()


def load_queue_status(queue_path: Path | None) -> dict[str, str]:
    if queue_path is None or not queue_path.exists():
        return {}
    data = read_json(queue_path)
    statuses: dict[str, str] = {}
    for status in ("pending", "matched", "integrated", "failed", "difficult"):
        bucket = data.get(status)
        if isinstance(bucket, list):
            for item in bucket:
                if isinstance(item, str):
                    statuses[item] = status
                elif isinstance(item, dict) and isinstance(item.get("name"), str):
                    statuses[item["name"]] = status
    return statuses


def classify_prompt(prompt_dir: Path, case_data: dict[str, str], queue_status: str | None) -> dict[str, Any]:
    report = read_json_optional(prompt_dir / "build" / "build-and-verify.json")
    report_status = build_report_status(report)
    case_status = case_data.get("status") or "unknown"
    has_candidate = candidate_present(prompt_dir, case_data)
    if case_status == "integrated":
        classification = "integrated"
    elif case_status == "blocked":
        classification = "blocked"
    elif report_status == "matched":
        classification = "matched"
    elif not has_candidate:
        classification = "candidate_missing"
    elif queue_status in {"pending", "failed", "difficult"}:
        classification = queue_status
    else:
        classification = "candidate_unverified"
    return {
        "prompt": prompt_dir.name,
        "promptDir": str(prompt_dir),
        "caseStatus": case_status,
        "queueStatus": queue_status,
        "buildStatus": report_status,
        "method": report.get("method"),
        "byteIdentical": report.get("byte_identical"),
        "candidatePresent": has_candidate,
        "classification": classification,
    }


def semantic_source_evidence(package: Path) -> dict[str, Any]:
    readiness = read_json_optional(package / "SEMANTIC_READINESS.json")
    evaluation = read_json_optional(package / "SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json")
    missing = readiness.get("missingEvidence") if isinstance(readiness.get("missingEvidence"), list) else []
    status = readiness.get("status") or evaluation.get("status") or "unknown"
    semantic_ready = status in {"ready", "semantic-ready"} and len(missing) == 0
    return {
        "readinessStatus": status,
        "missingEvidenceCount": len(missing),
        "semanticReadyByPackage": semantic_ready,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True, help="one-shot-source package directory")
    parser.add_argument("--prompts-dir", type=Path, default=ROOT / "prompts")
    parser.add_argument("--queue", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    package = args.package.resolve()
    manifest_path = package / "FUNCTION_RECONSTRUCTION_TASKS.json"
    if not manifest_path.exists():
        raise SystemExit(f"missing FUNCTION_RECONSTRUCTION_TASKS.json: {manifest_path}")
    manifest = read_json(manifest_path)
    if manifest.get("schema") != "mizuchi.one-shot-source-function-reconstruction-tasks.v1":
        raise SystemExit("FUNCTION_RECONSTRUCTION_TASKS.json schema mismatch")
    tasks = manifest.get("tasks")
    if not isinstance(tasks, list):
        raise SystemExit("FUNCTION_RECONSTRUCTION_TASKS.json tasks must be an array")

    prompts_by_task: dict[str, list[tuple[Path, dict[str, str]]]] = {}
    for prompt_dir in prompt_dirs(args.prompts_dir):
        case_data = read_simple_case_yaml(prompt_dir / "case.yaml")
        case_package = case_data.get("oneShotPackagePath")
        task_path = case_data.get("oneShotTaskPath")
        if not case_package or not task_path:
            continue
        try:
            same_package = Path(case_package).resolve() == package
        except OSError:
            same_package = False
        if same_package:
            prompts_by_task.setdefault(package_task_key(task_path), []).append((prompt_dir, case_data))

    queue_statuses = load_queue_status(args.queue)
    task_reports = []
    counts: dict[str, int] = {
        "taskCount": len(tasks),
        "imported": 0,
        "notImported": 0,
        "pending": 0,
        "matched": 0,
        "integrated": 0,
        "blocked": 0,
        "failed": 0,
        "difficult": 0,
        "candidateMissing": 0,
        "candidateUnverified": 0,
    }

    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_path = task.get("path")
        if not isinstance(task_path, str):
            continue
        key = package_task_key(task_path)
        prompt_entries = prompts_by_task.get(key, [])
        if not prompt_entries:
            classification = "not_imported"
            counts["notImported"] += 1
            prompt_reports: list[dict[str, Any]] = []
        else:
            counts["imported"] += 1
            prompt_reports = [
                classify_prompt(prompt_dir, case_data, queue_statuses.get(prompt_dir.name))
                for prompt_dir, case_data in prompt_entries
            ]
            priority = [
                "integrated",
                "matched",
                "blocked",
                "candidate_missing",
                "candidate_unverified",
                "pending",
                "failed",
                "difficult",
            ]
            classification = next(
                (name for name in priority if any(p["classification"] == name for p in prompt_reports)),
                prompt_reports[0]["classification"],
            )
            count_key = {
                "candidate_missing": "candidateMissing",
                "candidate_unverified": "candidateUnverified",
            }.get(classification, classification)
            if count_key in counts:
                counts[count_key] += 1
        task_reports.append(
            {
                "name": task.get("name"),
                "taskPath": key,
                "classification": classification,
                "prompts": prompt_reports,
            }
        )

    semantic_evidence = semantic_source_evidence(package)
    all_tasks_verified = counts["taskCount"] > 0 and counts["taskCount"] == counts["matched"] + counts["integrated"]
    semantic_ready = all_tasks_verified and semantic_evidence["semanticReadyByPackage"]
    report = {
        "schema": "mizuchi.one-shot-task-coverage.v1",
        "status": "semantic-ready" if semantic_ready else "incomplete",
        "package": str(package),
        "manifest": str(manifest_path),
        "promptsDir": str(args.prompts_dir),
        "queue": str(args.queue) if args.queue else None,
        "semanticReady": semantic_ready,
        "allTasksVerified": all_tasks_verified,
        "claimBoundary": "Task coverage proves imported prompt verification progress only; it is not whole-app semantic source recovery unless semanticReady is true.",
        "summary": counts,
        "semanticSourceEvidence": semantic_evidence,
        "tasks": task_reports,
    }
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
