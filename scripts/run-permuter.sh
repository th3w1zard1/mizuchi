#!/usr/bin/env bash
# Run decomp-permuter for a prompt folder (Cursor-native bridge).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/reconkit-config.sh
source "$ROOT/scripts/lib/reconkit-config.sh"

usage() {
  cat <<'EOF'
Usage: run-permuter.sh --prompt <prompt-dir> [options]

Options:
  --base-c <path>         Seed C (default: build/m2c.c or candidate.c)
  --permuter-dir <path>   decomp-permuter root (default: vendor/decomp-permuter or PERMUTER_DIR)
  --max-iterations <n>    Override plugins.decomp-permuter.maxIterations
  --timeout-ms <n>        Override plugins.decomp-permuter.timeoutMs
  --flags "<args>"        Extra flags passed to permuter.py
  --out <path>            Write best permuted C here (default: <prompt>/build/permuter-best.c)
  -h, --help              Show help

Exit codes:
  0  Perfect match (score 0) or permuter ran successfully with improvement path ready
  4  No base C seed
  5  decomp-permuter not installed
  6  Timeout
  7  Permuter failed to start
  8  No perfect match (run objdiff on permuter-best.c manually)
EOF
}

PROMPT_DIR=""
BASE_C=""
PERMUTER_DIR="${PERMUTER_DIR:-}"
MAX_ITERATIONS=0
TIMEOUT_MS=0
FLAGS=""
OUT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) PROMPT_DIR="$2"; shift 2 ;;
    --base-c) BASE_C="$2"; shift 2 ;;
    --permuter-dir) PERMUTER_DIR="$2"; shift 2 ;;
    --max-iterations) MAX_ITERATIONS="$2"; shift 2 ;;
    --timeout-ms) TIMEOUT_MS="$2"; shift 2 ;;
    --flags) FLAGS="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$PROMPT_DIR" ]]; then
  echo "Missing --prompt" >&2
  usage
  exit 2
fi

PROMPT_DIR="$(cd "$PROMPT_DIR" && pwd)"
CONFIG="$(recovery_config_resolve "$ROOT")"

args=(python3 "$ROOT/scripts/lib/permuter-run.py" --prompt-dir "$PROMPT_DIR" --config "$CONFIG")
[[ -n "$BASE_C" ]] && args+=(--base-c "$BASE_C")
[[ -n "$PERMUTER_DIR" ]] && args+=(--permuter-dir "$PERMUTER_DIR")
[[ "$MAX_ITERATIONS" -gt 0 ]] && args+=(--max-iterations "$MAX_ITERATIONS")
[[ "$TIMEOUT_MS" -gt 0 ]] && args+=(--timeout-ms "$TIMEOUT_MS")
[[ -n "$FLAGS" ]] && args+=(--flags "$FLAGS")
[[ -n "$OUT" ]] && args+=(--out "$OUT")

exec "${args[@]}"
