#!/usr/bin/env bash
set -euo pipefail

matcher_extract_c_block() {
  local input_file="${1:?missing input file}"
  awk '
    /^```c/ { in_c=1; next }
    /^```/ && in_c { exit }
    in_c { print }
  ' "$input_file"
}
