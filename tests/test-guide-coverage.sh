#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

out="$(bash "$ROOT/scripts/validate-guide-coverage.sh")"
[[ "$out" == "GUIDE_COVERAGE_OK" ]]

echo "test-guide-coverage: PASS"
