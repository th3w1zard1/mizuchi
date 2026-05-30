#!/usr/bin/env bash
# Orchestrate Mizuchi programmatic phase: get-context → m2c → compile/objdiff → permuter.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/check-log.sh
source "$ROOT/scripts/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$ROOT/scripts/lib/guide-manifest.sh"

usage() {
  cat <<'EOF'
Usage: run-programmatic-phase.sh --prompt <prompt-dir> [options]

Runs the one-way programmatic pipeline from the Macabeus/Mizuchi article:
  1. Get Context (get-context.sh)
  2. m2c (run-m2c.sh)
  3. compile + objdiff gate (compile-trial.sh)
  4. decomp-permuter if still not matched (run-permuter.sh + compile-trial)

Options:
  --skip-context          Do not run get-context.sh
  --skip-m2c              Skip m2c (use existing candidate.c)
  --skip-permuter         Stop after m2c compile attempt
  --quiet                 Suppress verbose trace (keep summary + result token)
  -h, --help              Show help

Examples:
  ./scripts/run-programmatic-phase.sh --prompt prompts/fun_00148020/
  ./scripts/run-programmatic-phase.sh --prompt prompts/fun_00148020/ --skip-permuter --quiet

Exit 0 only when objdiff reports 0 differences for m2c or permuter output.
EOF
}

PROMPT_DIR=""
SKIP_CONTEXT=0
SKIP_M2C=0
SKIP_PERMUTER=0
quiet=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) PROMPT_DIR="$2"; shift 2 ;;
    --skip-context) SKIP_CONTEXT=1; shift ;;
    --skip-m2c) SKIP_M2C=1; shift ;;
    --skip-permuter) SKIP_PERMUTER=1; shift ;;
    --quiet) quiet=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

CHECK_LOG_QUIET=$quiet
check_log_init "run-programmatic-phase"
guide_manifest_load "$ROOT"
check_log_trace "root  $(guide_manifest_rel "$ROOT" "$ROOT")"
check_log_trace "output prompts=$(guide_manifest_rel "$ROOT" "${GUIDE_OUTPUT_DIRS[0]}") context=$(guide_manifest_rel "$ROOT" "${GUIDE_OUTPUT_DIRS[1]}")"

if [[ -z "$PROMPT_DIR" ]]; then
  check_log_fail "missing --prompt"
  check_log_summary "PROGRAMMATIC_PHASE_FAIL"
  usage
  exit 2
fi

PROMPT_DIR="$(cd "$PROMPT_DIR" && pwd)"
mkdir -p "$PROMPT_DIR/build"
check_log_file_op "$(guide_manifest_rel "$ROOT" "$PROMPT_DIR/build")" "ensure-dir"

if [[ "$SKIP_CONTEXT" -eq 0 ]]; then
  check_log_run_step "get-context"
  if "$ROOT/scripts/get-context.sh" --prompt "$PROMPT_DIR" ${quiet:+--quiet}; then
    check_log_pass "get-context.sh"
  else
    check_log_trace "warn  get-context failed; continuing with context/ctx.h"
  fi
fi

if [[ "$SKIP_M2C" -eq 0 ]]; then
  check_log_run_step "m2c"
  if "$ROOT/scripts/run-m2c.sh" --prompt "$PROMPT_DIR"; then
    M2C_OUT="$PROMPT_DIR/build/m2c.c"
    if [[ -f "$M2C_OUT" ]]; then
      check_log_file_op "$(guide_manifest_rel "$ROOT" "$M2C_OUT")" "read"
      check_log_run_step "compile+objdiff (m2c)"
      if "$ROOT/scripts/compile-trial.sh" "$PROMPT_DIR" "$M2C_OUT"; then
        check_log_summary "PROGRAMMATIC_PHASE_OK"
        echo "PROGRAMMATIC_PHASE_OK match=m2c prompt=$(guide_manifest_rel "$ROOT" "$PROMPT_DIR")"
        exit 0
      fi
    fi
  else
    m2c_rc=$?
    if [[ "$m2c_rc" -eq 4 ]]; then
      check_log_trace "warn  m2c unsupported for this target"
    else
      check_log_trace "warn  m2c skipped or failed exit=$m2c_rc"
    fi
  fi
fi

if [[ "$SKIP_PERMUTER" -eq 1 ]]; then
  check_log_fail "no perfect match; permuter skipped"
  check_log_summary "PROGRAMMATIC_PHASE_FAIL"
  exit 1
fi

check_log_run_step "decomp-permuter"
PERM_OUT="$PROMPT_DIR/build/permuter-best.c"
if "$ROOT/scripts/run-permuter.sh" --prompt "$PROMPT_DIR" --out "$PERM_OUT"; then
  perm_rc=0
else
  perm_rc=$?
fi

if [[ -f "$PERM_OUT" ]]; then
  check_log_file_op "$(guide_manifest_rel "$ROOT" "$PERM_OUT")" "read"
  check_log_run_step "compile+objdiff (permuter)"
  if "$ROOT/scripts/compile-trial.sh" "$PROMPT_DIR" "$PERM_OUT"; then
    check_log_summary "PROGRAMMATIC_PHASE_OK"
    echo "PROGRAMMATIC_PHASE_OK match=permuter prompt=$(guide_manifest_rel "$ROOT" "$PROMPT_DIR")"
    exit 0
  fi
fi

if [[ "$perm_rc" -eq 0 ]]; then
  check_log_fail "permuter perfect score but objdiff gate failed"
else
  check_log_fail "programmatic phase ended without objdiff 0 (permuter exit $perm_rc)"
fi
check_log_summary "PROGRAMMATIC_PHASE_FAIL"
exit 1
