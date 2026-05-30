#!/usr/bin/env bash
set -euo pipefail

verify_with_objdiff() {
  local target="${1:?missing target object}"
  local candidate="${2:?missing candidate object}"
  local root_dir="${MIZUCHI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
  "$root_dir/scripts/run-objdiff.sh" "$target" "$candidate"
}
