#!/usr/bin/env bash
# Orchestrate Mizuchi programmatic phase: get-context → m2c → compile/objdiff → permuter.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
  -h, --help              Show help

Exit 0 only when objdiff reports 0 differences for m2c or permuter output.
EOF
}

PROMPT_DIR=""
SKIP_CONTEXT=0
SKIP_M2C=0
SKIP_PERMUTER=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) PROMPT_DIR="$2"; shift 2 ;;
    --skip-context) SKIP_CONTEXT=1; shift ;;
    --skip-m2c) SKIP_M2C=1; shift ;;
    --skip-permuter) SKIP_PERMUTER=1; shift ;;
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
mkdir -p "$PROMPT_DIR/build"

if [[ "$SKIP_CONTEXT" -eq 0 ]]; then
  echo "==> Get Context" >&2
  "$ROOT/scripts/get-context.sh" --prompt "$PROMPT_DIR" || {
    echo "Warning: get-context failed (stub getContextScript?); continuing with context/ctx.h" >&2
  }
fi

if [[ "$SKIP_M2C" -eq 0 ]]; then
  echo "==> m2c" >&2
  if "$ROOT/scripts/run-m2c.sh" --prompt "$PROMPT_DIR"; then
    M2C_OUT="$PROMPT_DIR/build/m2c.c"
    if [[ -f "$M2C_OUT" ]]; then
      echo "==> compile + objdiff (m2c)" >&2
      if "$ROOT/scripts/compile-trial.sh" "$PROMPT_DIR" "$M2C_OUT"; then
        echo "Programmatic phase: PERFECT MATCH via m2c" >&2
        exit 0
      fi
    fi
  else
    m2c_rc=$?
    if [[ "$m2c_rc" -eq 4 ]]; then
      echo "m2c unsupported for this target; need hand-written candidate.c" >&2
    else
      echo "m2c skipped or failed (exit $m2c_rc)" >&2
    fi
  fi
fi

if [[ "$SKIP_PERMUTER" -eq 1 ]]; then
  echo "No perfect match; permuter skipped" >&2
  exit 1
fi

echo "==> decomp-permuter" >&2
PERM_OUT="$PROMPT_DIR/build/permuter-best.c"
if "$ROOT/scripts/run-permuter.sh" --prompt "$PROMPT_DIR" --out "$PERM_OUT"; then
  perm_rc=0
else
  perm_rc=$?
fi

if [[ -f "$PERM_OUT" ]]; then
  echo "==> compile + objdiff (permuter)" >&2
  if "$ROOT/scripts/compile-trial.sh" "$PROMPT_DIR" "$PERM_OUT"; then
    echo "Programmatic phase: PERFECT MATCH via permuter" >&2
    exit 0
  fi
fi

if [[ "$perm_rc" -eq 0 ]]; then
  echo "Permuter reported perfect score but objdiff gate failed — verify toolchain/golden .o" >&2
else
  echo "Programmatic phase ended without objdiff 0 (permuter exit $perm_rc)" >&2
fi
exit 1
