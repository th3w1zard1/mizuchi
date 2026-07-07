#!/usr/bin/env bash
# Machine-readable gate for the Mizuchi source-parity Ralph loop.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SWKOTOR_COVERAGE="${ROOT}/target/swkotor-recovered/coverage.json"
SWKOTOR_REPORT="${ROOT}/target/source-parity-one-shot/swkotor/report.json"
JKA_BINARY="/run/media/brunner56/MyBook/SteamLibrary/steamapps/common/Jedi Academy/GameData/jamp.exe"
JKA_LAUNCHER="/run/media/brunner56/MyBook/SteamLibrary/steamapps/common/Jedi Academy/JediAcademy.exe"
JKA_COVERAGE="${ROOT}/target/jedi-academy-recovered/coverage.json"
JKA_INVENTORY="${ROOT}/target/jedi-academy-unpack/facts/function-inventory.jsonl"
TARGET_RATIO="${RALPH_SWKOTOR_TARGET:-0.90}"
JKA_TARGET_RATIO="${RALPH_JKA_TARGET:-0.90}"
export ROOT SWKOTOR_COVERAGE SWKOTOR_REPORT JKA_BINARY JKA_LAUNCHER JKA_COVERAGE JKA_INVENTORY
export RALPH_SWKOTOR_TARGET="${TARGET_RATIO}" RALPH_JKA_TARGET="${JKA_TARGET_RATIO}"

python3 - <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path

root = Path(os.environ["ROOT"])
target_ratio = float(os.environ.get("RALPH_SWKOTOR_TARGET", "0.90"))
jka_target_ratio = float(os.environ.get("RALPH_JKA_TARGET", "0.90"))

checks: list[dict] = []


def add(name: str, ok: bool, detail: dict) -> None:
    checks.append({"name": name, "ok": ok, **detail})


def read_ratio(path: Path) -> tuple[float, dict]:
    if not path.exists():
        return 0.0, {"missing": str(path)}
    data = json.loads(path.read_text(encoding="utf-8"))
    count = int(data.get("functionCount") or 0)
    verified = int(data.get("verifiedMatchedFunctionCount") or 0)
    ratio = float(data.get("verifiedRatio") or ((verified / count) if count else 0.0))
    return ratio, {
        "functionCount": count,
        "verifiedMatchedFunctionCount": verified,
        "verifiedRatio": ratio,
        "path": str(path),
    }


sw_ratio, sw_detail = read_ratio(root / "target/swkotor-recovered/coverage.json")
add(
    "swkotor-coverage",
    sw_ratio >= target_ratio,
    {"target": target_ratio, **sw_detail},
)

jka_binary = Path(os.environ["JKA_BINARY"])
jka_launcher = Path(os.environ.get("JKA_LAUNCHER", ""))
add("jka-binary-present", jka_binary.exists(), {"path": str(jka_binary), "launcher": str(jka_launcher)})

jka_ratio, jka_detail = read_ratio(root / "target/jedi-academy-recovered/coverage.json")
inventory = root / "target/jedi-academy-unpack/facts/function-inventory.jsonl"
if not jka_detail.get("functionCount") and inventory.exists():
    lines = sum(1 for line in inventory.read_text(encoding="utf-8").splitlines() if line.strip())
    jka_detail["functionCount"] = lines
    jka_detail["inventoryOnly"] = True
add(
    "jka-coverage",
    jka_ratio >= jka_target_ratio,
    {"target": jka_target_ratio, **jka_detail},
)

orch = root / "scripts/source-parity-one-shot.py"
self_check_ok = False
self_check_detail: dict = {}
if orch.exists():
    proc = subprocess.run(
        [sys.executable, str(orch), "--self-check"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        self_check_detail = json.loads(proc.stdout or "{}")
        self_check_ok = bool(self_check_detail.get("ok")) and proc.returncode == 0
    except json.JSONDecodeError:
        self_check_detail = {"stdout": (proc.stdout or "")[:500], "stderr": (proc.stderr or "")[:500]}
add("source-parity-one-shot", self_check_ok, self_check_detail)

core_scripts = [
    "scripts/vacuum.sh",
    "scripts/decomp-cli.sh",
    "scripts/run-programmatic-phase.sh",
    "scripts/source-parity-synthesize.py",
    "scripts/ghidra/ExportFunctionInventory.java",
    "src/mizuchi_re/source_parity_one_shot.py",
    "src/mizuchi_re/source_parity_synthesize.py",
    "src/mizuchi_re/package_verify.py",
]
missing = [rel for rel in core_scripts if not (root / rel).exists()]
add("core-surfaces", not missing, {"missing": missing})

bridges = {
    "vacuum": (root / "scripts/vacuum.sh").exists(),
    "decomp-atlas": True,
    "scorer": (root / "scripts/lib/scorer.sh").exists() or (root / "scripts/decomp-cli.sh").exists(),
    "matcher": (root / "scripts/decomp-cli.sh").exists(),
}
add("workspace-bridges", all(bridges.values()), bridges)

upstream = subprocess.run(
    [sys.executable, "-m", "mizuchi_re.mizuchi_cli", "upstream-status", "--json"],
    cwd=root,
    text=True,
    capture_output=True,
    env={**os.environ, "PYTHONPATH": str(root / "src")},
    check=False,
)
upstream_ok = False
upstream_detail: dict = {}
if upstream.returncode == 0:
    try:
        payload = json.loads(upstream.stdout or "{}")
        mapped = {row.get("upstreamSurface") for row in payload.get("mappedSurfaces", [])}
        upstream_ok = {"run", "atlas", "index-codebase"}.issubset(mapped)
        upstream_detail = {"mapped": sorted(mapped)}
    except json.JSONDecodeError:
        upstream_detail = {"stdout": (upstream.stdout or "")[:300]}
else:
    upstream_detail = {"stderr": (upstream.stderr or "")[:300], "returncode": upstream.returncode}
add("upstream-cli-bridge", upstream_ok, upstream_detail)

complete = all(row["ok"] for row in checks)
report = {
    "schema": "mizuchi.ralph-verify.v1",
    "complete": complete,
    "checks": checks,
    "completionPromise": "SOURCE_PARITY_LOOP_COMPLETE",
}
print(json.dumps(report, indent=2))
sys.exit(0 if complete else 1)
PY
