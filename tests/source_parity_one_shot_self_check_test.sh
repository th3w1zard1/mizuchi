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
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))
from mizuchi_re.source_parity_one_shot import load_state, should_run_stage

with tempfile.TemporaryDirectory() as td:
    state_path = Path(td) / "state.json"
    state_path.write_text(
        json.dumps({"stages": {"match-reloc": {"status": "complete", "artifact": "legacy"}}}),
        encoding="utf-8",
    )
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

python3 - <<'PY'
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))
from mizuchi_re.source_parity_one_shot import ProfileConfig, detect_profile, stage_discover, stage_inventory
from mizuchi_re.targets import is_pe_binary, resolve_target, sha256_file

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    host_true = root / "true"
    host_true.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    host_true.chmod(0o755)
    game = root / "game.exe"
    game.write_bytes(b"MZ" + b"\0" * 50000)

    assert resolve_target(root) == game
    assert is_pe_binary(game)
    assert not is_pe_binary(root / "tiny.exe")

    tiny = root / "tiny.exe"
    tiny.write_bytes(b"MZ" + b"\0" * 100)
    state = {"stages": {}}
    try:
        stage_discover(tiny, ProfileConfig.for_slug("swkotor"), state)
    except ValueError as exc:
        assert "requires a Windows PE game binary" in str(exc)
    else:
        raise AssertionError("small PE-like file should not satisfy game profile discovery")

assert detect_profile(Path("jamp.exe")) == "jedi-academy"
assert detect_profile(Path("jasp.exe")) == "jedi-academy"
assert detect_profile(Path("JediAcademy.exe")) == "jedi-academy"

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    binary = root / "analysis.exe"
    binary.write_bytes(b"MZ" + b"\0" * 50000)
    inv = root / "facts" / "function-inventory.jsonl"
    summary = root / "facts" / "inventory-summary.json"
    inv.parent.mkdir(parents=True)
    inv.write_text('{"name":"a"}\n{"name":"b"}\n', encoding="utf-8")
    summary.write_text(json.dumps({"functionCount": 2}), encoding="utf-8")
    profile = ProfileConfig(
        slug="swkotor",
        default_binary=binary,
        unpack_dir=root,
        inventory_jsonl=inv,
        inventory_summary=summary,
        trivial_matches_dir=root / "trivial",
        trivial_out_jsonl=root / "trivial" / "summary.jsonl",
        trivial_summary=root / "trivial" / "summary.json",
        reloc_matches_dir=root / "reloc",
        reloc_out_jsonl=root / "reloc" / "summary.jsonl",
        reloc_summary=root / "reloc" / "summary.json",
        recovered_dir=root / "recovered",
        compile_summary=root / "recovered" / "compile-summary.json",
        coverage_json=root / "recovered" / "coverage.json",
        queue_jsonl=root / "queue" / "queue.jsonl",
        index_out_dir=root / "index",
        synthesis_out_dir=root / "synth",
        state_dir=root / "state",
        text_section=".text",
        match_root=root / "match",
    )
    digest = sha256_file(binary)
    state = {
        "binarySha256": digest,
        "stages": {
            "prepare": {"copiedTo": str(binary)},
            "inventory": {"status": "pending"},
        },
    }
    stage_inventory(profile, state, refresh=False)
    inventory_stage = state["stages"]["inventory"]
    assert inventory_stage["status"] == "complete", inventory_stage
    assert inventory_stage["reused"] is True, inventory_stage
    assert inventory_stage["functionCount"] == 2, inventory_stage

print("source_parity_target_hardening_test: ok")
PY

python3 - <<'PY'
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))
from mizuchi_re.source_parity_one_shot import ProfileConfig, stage_derive_coverage

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    inv = root / "facts" / "function-inventory.jsonl"
    inv.parent.mkdir(parents=True)
    inv.write_text("".join(f'{{"name":"fn_{index}"}}\n' for index in range(10)), encoding="utf-8")
    recovered = root / "recovered"
    recovered.mkdir(parents=True)
    (recovered / "compile-summary.json").write_text(
        json.dumps({"attempted": 3, "verifiedMatchedFunctionCount": 3}),
        encoding="utf-8",
    )
    (recovered / "simple_matches.manifest.json").write_text(
        json.dumps({"functionCount": 10}),
        encoding="utf-8",
    )
    profile = ProfileConfig(
        slug="swkotor",
        default_binary=root / "game.exe",
        unpack_dir=root,
        inventory_jsonl=inv,
        inventory_summary=root / "facts" / "inventory-summary.json",
        trivial_matches_dir=root / "trivial",
        trivial_out_jsonl=root / "trivial" / "summary.jsonl",
        trivial_summary=root / "trivial" / "summary.json",
        reloc_matches_dir=root / "reloc",
        reloc_out_jsonl=root / "reloc" / "summary.jsonl",
        reloc_summary=root / "reloc" / "summary.json",
        recovered_dir=recovered,
        compile_summary=recovered / "compile-summary.json",
        coverage_json=recovered / "coverage.json",
        queue_jsonl=root / "queue" / "queue.jsonl",
        index_out_dir=root / "index",
        synthesis_out_dir=root / "synth",
        state_dir=root / "state",
        text_section=".text",
        match_root=root / "match",
    )
    state = {"stages": {}}
    stage_derive_coverage(profile, state)
    coverage = json.loads(profile.coverage_json.read_text(encoding="utf-8"))
    assert coverage["functionCount"] == 10, coverage
    assert coverage["verifiedMatchedFunctionCount"] == 3, coverage
    assert coverage["remainingFunctions"] == 7, coverage
    assert state["stages"]["derive-coverage"]["verifiedMatchedFunctionCount"] == 3, state

print("source_parity_partial_compile_coverage_test: ok")
PY
