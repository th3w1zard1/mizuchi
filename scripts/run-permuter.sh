#!/usr/bin/env bash
# Run decomp-permuter for a prompt folder (Cursor-native bridge).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/mizuchi-config.sh
source "$ROOT/scripts/lib/mizuchi-config.sh"
# shellcheck source=scripts/lib/check-log.sh
source "$ROOT/scripts/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$ROOT/scripts/lib/guide-manifest.sh"
# shellcheck source=scripts/lib/cli-agent.sh
source "$ROOT/scripts/lib/cli-agent.sh"

usage() {
  cat <<'EOF'
Usage: run-permuter.sh --prompt <prompt-dir> [options]

Runs decomp-permuter via scripts/lib/permuter-run.py. Defaults use guide build/ paths.

Options:
  --base-c <path>         Seed C (default: build/m2c.c or candidate.c)
  --permuter-dir <path>   decomp-permuter root (default: vendor/decomp-permuter or PERMUTER_DIR)
  --max-iterations <n>    Override plugins.decomp-permuter.maxIterations
  --timeout-ms <n>        Override plugins.decomp-permuter.timeoutMs
  --flags "<args>"        Extra flags passed to permuter.py
  --out <path>            Best permuted C (default: <prompt>/build/permuter-best.c)
  --quiet                 Suppress verbose trace (keep summary + exit code)
  -h, --help              Show help

Examples:
  ./scripts/run-permuter.sh --prompt prompts/fun_00148020/
  ./scripts/run-permuter.sh --prompt prompts/fun_00148020/ --out prompts/fun_00148020/build/permuter-best.c --quiet

Exit codes:
  0  Perfect match (score 0) or permuter ran successfully
  4  No base C seed
  5  decomp-permuter not installed
  6  Timeout
  7  Permuter failed to start
  8  No perfect match
EOF
}

PROMPT_DIR=""
BASE_C=""
PERMUTER_DIR="${PERMUTER_DIR:-}"
MAX_ITERATIONS=0
TIMEOUT_MS=0
FLAGS=""
OUT=""
quiet=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) PROMPT_DIR="$2"; shift 2 ;;
    --base-c) BASE_C="$2"; shift 2 ;;
    --permuter-dir) PERMUTER_DIR="$2"; shift 2 ;;
    --max-iterations) MAX_ITERATIONS="$2"; shift 2 ;;
    --timeout-ms) TIMEOUT_MS="$2"; shift 2 ;;
    --flags) FLAGS="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --quiet) quiet=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) cli_agent_missing_arg "run-permuter.sh" "unknown option: $1" "./scripts/run-permuter.sh --prompt prompts/fun_00148020/" ;;
  esac
done

CHECK_LOG_QUIET=$quiet
check_log_init "run-permuter"
guide_manifest_load "$ROOT"
guide_manifest_trace_defaults "$ROOT"

if [[ -z "$PROMPT_DIR" ]]; then
  check_log_fail "missing --prompt"
  check_log_summary "RUN_PERMUTER_FAIL"
  cli_agent_missing_arg "run-permuter.sh" "missing --prompt" "./scripts/run-permuter.sh --prompt prompts/fun_00148020/"
fi

PROMPT_DIR="$(cd "$PROMPT_DIR" && pwd)"
check_log_trace "prompt $(guide_manifest_rel "$ROOT" "$PROMPT_DIR")"

if [[ -z "$OUT" ]]; then
  OUT="$(guide_prompt_build_path "$PROMPT_DIR" "permuter-best.c")"
fi
check_log_trace "out   $(guide_manifest_rel "$ROOT" "$OUT")"

if [[ -z "$BASE_C" ]]; then
  for seed in "$(guide_prompt_build_path "$PROMPT_DIR" "m2c.c")" "$PROMPT_DIR/candidate.c"; do
    if [[ -f "$seed" ]]; then
      BASE_C="$seed"
      check_log_read_file "$seed" "$(guide_manifest_rel "$ROOT" "$seed")" "permuter seed"
      break
    fi
  done
elif [[ -f "$BASE_C" ]]; then
  check_log_read_file "$BASE_C" "$(guide_manifest_rel "$ROOT" "$BASE_C")" "permuter seed"
fi

CONFIG="$(mizuchi_config_resolve "$ROOT")"
check_log_read_file "$CONFIG" "$(guide_manifest_rel "$ROOT" "$CONFIG")" "mizuchi config"

args=(python3 "$ROOT/scripts/lib/permuter-run.py" --prompt-dir "$PROMPT_DIR" --config "$CONFIG")
[[ -n "$BASE_C" ]] && args+=(--base-c "$BASE_C")
[[ -n "$PERMUTER_DIR" ]] && args+=(--permuter-dir "$PERMUTER_DIR")
[[ "$MAX_ITERATIONS" -gt 0 ]] && args+=(--max-iterations "$MAX_ITERATIONS")
[[ "$TIMEOUT_MS" -gt 0 ]] && args+=(--timeout-ms "$TIMEOUT_MS")
[[ -n "$FLAGS" ]] && args+=(--flags "$FLAGS")
[[ -n "$OUT" ]] && args+=(--out "$OUT")

out_existed=0
[[ -f "$OUT" ]] && out_existed=1

check_log_run_cmd "permuter-run.py" "${args[*]}"

set +e
"${args[@]}"
perm_rc=$?
set -e

if [[ -f "$OUT" ]]; then
  check_log_file_written "$OUT" "$ROOT" "$out_existed"
fi

if [[ "$perm_rc" -eq 0 ]]; then
  check_log_summary "RUN_PERMUTER_OK"
  echo "RUN_PERMUTER_OK out=$(guide_manifest_rel "$ROOT" "$OUT")"
else
  check_log_fail "permuter exit ${perm_rc}"
  check_log_summary "RUN_PERMUTER_FAIL"
  echo "RUN_PERMUTER_FAIL exit=${perm_rc}" >&2
fi
exit "$perm_rc"
