#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

out="$(python3 scripts/source-parity-one-shot.py --self-check)"
echo "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("ok") is True, d'

# Legacy stage migration: match-reloc complete should satisfy match-reloc-wrappers skip.
python3 - <<'PY'
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))
from mizuchi_re.source_parity_one_shot import load_state, should_run_stage

state_path = Path("target/source-parity-one-shot/swkotor/state.json")
if not state_path.exists():
    print("skip: no swkotor state fixture")
    sys.exit(0)

state = load_state(state_path)
stages = state.get("stages", {})
assert stages.get("match-reloc-wrappers", {}).get("status") == "complete", stages.get("match-reloc-wrappers")
assert should_run_stage(state, "match-reloc-wrappers", resume=True) is False
print("legacy stage migration ok")
PY

echo "source_parity_one_shot_self_check_test: ok"
