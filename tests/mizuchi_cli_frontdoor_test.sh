#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 - <<PY
from pathlib import Path

import json
from contextlib import redirect_stdout
from io import StringIO

from mizuchi_re.mizuchi_cli import main
from mizuchi_re.tools import resolve_script_asset

root = Path("$ROOT")
work_dir = Path("$TMP_DIR/run")
script = resolve_script_asset(root, "one-shot-source.py")
assert script is not None, "one-shot-source.py should resolve from checkout scripts"

buf = StringIO()
with redirect_stdout(buf):
    rc = main(["self-check", "--repo-root", str(root), "--json"])
assert rc == 0, rc
self_check = json.loads(buf.getvalue())
assert self_check["status"] == "ok", self_check
assert self_check["scriptAssets"]["one-shot-source.py"]["available"], self_check

buf = StringIO()
with redirect_stdout(buf):
    rc = main(["upstream-status", "--json"])
assert rc == 0, rc
upstream_status = json.loads(buf.getvalue())
assert upstream_status["upstream"]["commands"] == ["run", "atlas", "index-codebase"], upstream_status

rc = main([
    "/bin/true",
    "--work-dir",
    str(work_dir),
    "--no-resume",
    "--source-synthesis",
    "none",
    "--no-byte-authority",
    "--stop-after",
    "discover",
    "--json",
])
assert rc == 0, rc
assert (work_dir / "target.json").exists(), "front door should write target identity"
assert not (work_dir / "report.json").exists(), "discover-only smoke should not run full report"
print("ok")
PY
