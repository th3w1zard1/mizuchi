#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="$(bash "$ROOT/scripts/validate-capability-parity.sh")"
[[ "$out" == "CAPABILITY_PARITY_OK" ]]

echo "test-capability-parity: PASS"
