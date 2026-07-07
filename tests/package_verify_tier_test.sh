#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 - <<'PY'
from reconkit_re.package_verify import (
    acceptance_gate_for_tier,
    verification_tier_for_package_status,
    verification_tier_for_result,
)

assert verification_tier_for_package_status("syntax-failed", "not-run") == "generated"
assert verification_tier_for_package_status("object-ok", "not-run") == "object-compilable"
assert verification_tier_for_package_status("code-match", "not-run") == "code-slice"
assert verification_tier_for_package_status(
    "code-relocation-masked-match", "not-run"
) == "relocation-aware-code-slice"
assert verification_tier_for_package_status("object-ok", "matched") == "target-object-objdiff"

assert verification_tier_for_result(
    row_status="syntax-ok",
    object_result={"status": "not-run"},
    code_compare_result={"status": "not-run"},
    objdiff_result={"status": "not-run"},
) == "generated"

assert verification_tier_for_result(
    row_status="object-ok",
    object_result={"status": "ok"},
    code_compare_result={"status": "not-run"},
    objdiff_result={"status": "not-run"},
) == "object-compilable"

assert verification_tier_for_result(
    row_status="object-ok",
    object_result={"status": "ok"},
    code_compare_result={"status": "match"},
    objdiff_result={"status": "not-run"},
) == "code-slice"

assert verification_tier_for_result(
    row_status="object-ok",
    object_result={"status": "ok"},
    code_compare_result={"status": "relocation-masked-match"},
    objdiff_result={"status": "not-run"},
) == "relocation-aware-code-slice"

assert verification_tier_for_result(
    row_status="object-ok",
    object_result={"status": "ok"},
    code_compare_result={"status": "not-run"},
    objdiff_result={"status": "matched"},
) == "target-object-objdiff"

assert "objdiff" in acceptance_gate_for_tier("code-slice").lower()
assert "accepted" in acceptance_gate_for_tier("target-object-objdiff").lower()

print("ok")
PY
