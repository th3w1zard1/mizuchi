#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 - <<'PY'
from reconkit_re.package_verify import run_command

proc = run_command(["bash", "-lc", "bash -c 'sleep 20' & wait"], timeout=1)
assert proc.returncode == 124, proc
assert "timed out after 1 seconds" in proc.stderr, proc.stderr
print("ok")
PY
