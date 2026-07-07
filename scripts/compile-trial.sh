#!/usr/bin/env bash
# Compile a candidate C file for a ReconstructKit prompt folder and verify the proof target.
#
# Usage:
#   compile-trial.sh <prompts/<name>/> [candidate.c]
#
# If candidate.c is omitted, uses $dir/candidate.c
# Writes $dir/build/candidate.o (and logs under $dir/build/).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

dir="${1:-}"
candidate="${2:-}"

if [[ -z "$dir" ]]; then
  echo "usage: $0 <prompts/<name>/> [candidate.c]" >&2
  exit 2
fi

if [[ -z "$candidate" ]]; then
  candidate="$dir/candidate.c"
fi

exec "$ROOT/scripts/build-and-verify.sh" --prompt "$dir" --candidate "$candidate"
