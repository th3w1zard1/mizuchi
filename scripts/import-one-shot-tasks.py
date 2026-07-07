#!/usr/bin/env python3
"""Import one-shot-source reconstruction tasks as Mizuchi prompt folders."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise SystemExit(f"JSON root must be object: {path}")
    return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slugify(value: str, fallback: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("._-")
    return slug or fallback


def yaml_scalar(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def package_relative_path(package: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str):
        raise SystemExit(f"task missing {label} path")
    rel = Path(value)
    if rel.is_absolute() or ".." in rel.parts:
        raise SystemExit(f"unsafe {label} path: {value!r}")
    path = (package / rel).resolve()
    package_root = package.resolve()
    if path != package_root and package_root not in path.parents:
        raise SystemExit(f"{label} path escapes package: {value!r}")
    return path


def write_prompt_from_task(
    package: Path,
    prompts_dir: Path,
    task: dict[str, Any],
    *,
    prefix: str,
    copy_candidates: bool,
    overwrite: bool,
) -> dict[str, Any]:
    task_path = task.get("path")
    if not isinstance(task_path, str) or task_path.startswith("/") or ".." in Path(task_path).parts:
        raise SystemExit(f"unsafe task path: {task_path!r}")
    task_dir = package_relative_path(package, task_path, "task")
    task_json = task_dir / "task.json"
    if not task_json.exists():
        raise SystemExit(f"missing task.json: {task_json}")
    task_doc = read_json(task_json)
    name = str(task.get("name") or task_doc.get("name") or Path(task_path).name)
    prompt_name = slugify(f"{prefix}{name}", f"{prefix}{Path(task_path).name}")
    prompt_dir = prompts_dir / prompt_name
    if prompt_dir.exists() and not overwrite:
        raise SystemExit(f"prompt already exists, pass --overwrite: {prompt_dir}")
    prompt_dir.mkdir(parents=True, exist_ok=True)

    target = task_doc.get("target") if isinstance(task_doc.get("target"), dict) else {}
    target_rel = target.get("path") or task.get("targetBytes")
    target_path = package_relative_path(package, target_rel, "target")
    if not target_path.exists():
        raise SystemExit(f"task target bytes missing: {target_path}")
    verifier_rel = task.get("candidateVerifier") or task_doc.get("acceptance", {}).get("candidateVerifier")
    verifier_path = package_relative_path(package, verifier_rel, "verifier")
    if not verifier_path.exists():
        raise SystemExit(f"task verifier missing: {verifier_path}")
    task_root = task_dir.resolve()
    verifier_abs = verifier_path.resolve()
    if verifier_abs != task_root and task_root not in verifier_abs.parents:
        raise SystemExit(f"task verifier must be inside task directory: {verifier_rel!r}")
    verifier_inside_task = verifier_abs.relative_to(task_root).as_posix()

    data = target_path.read_bytes()
    byte_rows = []
    for i in range(0, len(data), 12):
        row = ", ".join(f"0x{b:02x}" for b in data[i : i + 12])
        byte_rows.append(f"      .byte {row}")
    asm = "\n".join([f"  {name}:", *byte_rows]) if byte_rows else f"  {name}:\n      # empty target"

    settings = "\n".join(
        [
            f"functionName: {yaml_scalar(name)}",
            f"targetObjectPath: {yaml_scalar(str(target_path.resolve()))}",
            "asm: |",
            asm,
            "",
        ]
    )
    (prompt_dir / "settings.yaml").write_text(settings)

    verifier_command = (
        f"bash ./scripts/verify-reconstruction-task.sh --task-dir {yaml_scalar(str(task_dir.resolve()))} "
        f"--verifier {yaml_scalar(verifier_inside_task)} "
        '--candidate "{{candidateSourcePath}}" --candidate-output "{{candidateOutputPath}}"'
    )
    case_yaml = "\n".join(
        [
            f"caseId: {prompt_name}",
            f"functionName: {yaml_scalar(name)}",
            f"targetObjectPath: {yaml_scalar(str(target_path.resolve()))}",
            "candidateSourcePath: prompt:/candidate.c",
            "targetFamily: byte-slice",
            "proof: task-byte-identical",
            "status: pending",
            f"oneShotPackagePath: {yaml_scalar(str(package.resolve()))}",
            f"oneShotTaskPath: {yaml_scalar(task_path)}",
            f"oneShotTaskJson: {yaml_scalar(str(task_json.resolve()))}",
            f"targetBytesSha256: {yaml_scalar(sha256_file(target_path))}",
            "verifierCommand: |",
            f"  {verifier_command}",
            "",
        ]
    )
    (prompt_dir / "case.yaml").write_text(case_yaml)

    prompt_source = task_dir / "ONE_SHOT_SOURCE_PROMPT.md"
    prompt_text = prompt_source.read_text() if prompt_source.exists() else ""
    prompt_md = "\n".join(
        [
            f"# {name}",
            "",
            "Imported from a one-shot-source function reconstruction task.",
            "",
            f"- Package: `{package.resolve()}`",
            f"- Task: `{task_path}`",
            f"- Target bytes: `{target_path.resolve()}`",
            f"- Verifier: `{verifier_path.resolve()}`",
            f"- Target SHA256: `{sha256_file(target_path)}`",
            "",
            "Success requires `build-and-verify.sh` to run the task verifier and produce byte-identical target/candidate bytes.",
            "",
            "## Original Task Prompt",
            "",
            prompt_text,
            "",
        ]
    )
    (prompt_dir / "prompt.md").write_text(prompt_md)
    notes = "\n".join(
        [
            f"Imported from `{package / 'FUNCTION_RECONSTRUCTION_TASKS.json'}`.",
            "Proof scope: task-local byte-slice verifier; not whole-app semantic source recovery.",
            "",
        ]
    )
    (prompt_dir / "notes.md").write_text(notes)

    candidate = task_dir / "candidate.c"
    copied_candidate = False
    if copy_candidates and candidate.exists():
        shutil.copyfile(candidate, prompt_dir / "candidate.c")
        copied_candidate = True

    return {
        "name": prompt_name,
        "functionName": name,
        "promptDir": str(prompt_dir),
        "taskPath": task_path,
        "targetBytes": str(target_path),
        "targetBytesSha256": sha256_file(target_path),
        "copiedCandidate": copied_candidate,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True, help="one-shot-source package directory")
    parser.add_argument("--prompts-dir", type=Path, default=ROOT / "prompts")
    parser.add_argument("--prefix", default="oss_", help="prefix for imported prompt folder names")
    parser.add_argument("--copy-candidates", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
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

    args.prompts_dir.mkdir(parents=True, exist_ok=True)
    imported = [
        write_prompt_from_task(
            package,
            args.prompts_dir,
            task,
            prefix=args.prefix,
            copy_candidates=args.copy_candidates,
            overwrite=args.overwrite,
        )
        for task in tasks
        if isinstance(task, dict)
    ]
    report = {
        "schema": "mizuchi.import-one-shot-tasks.v1",
        "status": "imported",
        "package": str(package),
        "manifest": str(manifest_path),
        "promptsDir": str(args.prompts_dir),
        "taskCount": len(tasks),
        "importedCount": len(imported),
        "prompts": imported,
    }
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
