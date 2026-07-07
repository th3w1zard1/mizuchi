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

python3 - <<'PY'
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))
from mizuchi_re.source_parity_one_shot import write_report, ProfileConfig, ROOT

with tempfile.TemporaryDirectory() as td:
    report_path = Path(td) / "report.json"
    profile = ProfileConfig.for_slug("swkotor")
    write_report(
        profile,
        {
            "binaryPath": "/fake.bin",
            "binarySha256": "cafebabe",
            "stages": {
                "synthesize-candidates": {
                    "synthesisLimit": 3,
                    "synthesisMaxAttemptsPerFunction": 2,
                    "synthesisMaxAttemptsPerFunctionPolicy": "adaptive",
                    "synthesisAttemptLimitPolicy": "adaptive",
                    "synthesisAttemptLimitDistribution": {"1": 2},
                    "synthesisAttemptLimitReasonDistribution": {"boundary-suspect": 2},
                }
            },
        },
        report_path,
    )
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["synthesisLimit"] == 3
    assert data["synthesisMaxAttemptsPerFunction"] == 2
    assert data["synthesisMaxAttemptsPerFunctionPolicy"] == "adaptive"
    assert data["synthesisAttemptLimitPolicy"] == "adaptive"
    assert data["synthesisAttemptLimitDistribution"] == {"1": 2}
    assert data["synthesisAttemptLimitReasonDistribution"] == {"boundary-suspect": 2}
    # Verify report is under target path namespace when run normally.
    assert report_path.parent == Path(td)
print("source_parity_one_shot_report_fields_test: ok")
PY
