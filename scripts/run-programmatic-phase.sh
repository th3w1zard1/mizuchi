#!/usr/bin/env bash
# Orchestrate ReconstructKit programmatic phase: get-context → m2c → compile/objdiff → permuter.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/case-metadata.sh
. "$ROOT/scripts/lib/case-metadata.sh"

usage() {
  cat <<'EOF'
Usage: run-programmatic-phase.sh --prompt <prompt-dir> [options]

Runs the one-way programmatic pipeline from the Macabeus/ReconstructKit article:
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
REPORT_JSON=""
PROMPT_NAME=""
STAGES=()

json_array_from_lines() {
  if [[ "$#" -eq 0 ]]; then
    printf '[]'
  else
    printf '%s\n' "$@" | jq -R . | jq -s .
  fi
}

record_stage() {
  STAGES+=("$1")
}

write_report() {
  local status="$1" exit_code="$2" matched_stage="${3:-}" reason="${4:-}"
  local verifier_report="$PROMPT_DIR/build/build-and-verify.json"
  local stage_json
  stage_json="$(json_array_from_lines "${STAGES[@]}")"
  jq -n \
    --arg schema "reconkit.programmatic-phase.v1" \
    --arg status "$status" \
    --arg prompt "$PROMPT_NAME" \
    --arg prompt_dir "$PROMPT_DIR" \
    --arg matched_stage "$matched_stage" \
    --arg reason "$reason" \
    --arg verifier_report "$verifier_report" \
    --argjson exit_code "$exit_code" \
    --argjson stages "$stage_json" \
    '{
      schema: $schema,
      status: $status,
      prompt: $prompt,
      promptDir: $prompt_dir,
      exitCode: $exit_code,
      stages: $stages,
      matchedStage: (if $matched_stage == "" then null else $matched_stage end),
      reason: (if $reason == "" then null else $reason end),
      verifierReport: (if ($verifier_report | length) > 0 then $verifier_report else null end)
    }' >"$REPORT_JSON"
}

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
PROMPT_NAME="$(basename "$PROMPT_DIR")"
REPORT_JSON="$PROMPT_DIR/build/programmatic-phase.json"
mkdir -p "$PROMPT_DIR/build"
case_status="$(case_metadata_get_default "$PROMPT_DIR" status "")"
if [[ "$case_status" == "blocked" ]]; then
  blocked_reason="$(case_metadata_get_default "$PROMPT_DIR" blockedReason "case.yaml status is blocked")"
  record_stage "blocked"
  write_report "blocked" 3 "" "$blocked_reason"
  echo "run-programmatic-phase: prompt is blocked: $blocked_reason" >&2
  exit 3
fi

if [[ "$SKIP_CONTEXT" -eq 0 ]]; then
  echo "==> Get Context" >&2
  if "$ROOT/scripts/get-context.sh" --prompt "$PROMPT_DIR"; then
    record_stage "context:ok"
  else
    echo "Warning: get-context failed (stub getContextScript?); continuing with context/ctx.h" >&2
    record_stage "context:failed-continued"
  fi
else
  record_stage "context:skipped"
fi

if [[ "$SKIP_M2C" -eq 0 ]]; then
  echo "==> m2c" >&2
  if "$ROOT/scripts/run-m2c.sh" --prompt "$PROMPT_DIR"; then
    record_stage "m2c:generated"
    M2C_OUT="$PROMPT_DIR/build/m2c.c"
    if [[ -f "$M2C_OUT" ]]; then
      echo "==> compile + objdiff (m2c)" >&2
      if "$ROOT/scripts/compile-trial.sh" "$PROMPT_DIR" "$M2C_OUT"; then
        echo "Programmatic phase: PERFECT MATCH via m2c" >&2
        record_stage "m2c:matched"
        write_report "matched" 0 "m2c" ""
        exit 0
      else
        record_stage "m2c:mismatched"
      fi
    fi
  else
    m2c_rc=$?
    if [[ "$m2c_rc" -eq 4 ]]; then
      echo "m2c unsupported for this target; need hand-written candidate.c" >&2
      record_stage "m2c:unsupported"
    else
      echo "m2c skipped or failed (exit $m2c_rc)" >&2
      record_stage "m2c:failed:$m2c_rc"
    fi
  fi
else
  record_stage "m2c:skipped"
fi

CANDIDATE_C="$PROMPT_DIR/candidate.c"
if [[ -f "$CANDIDATE_C" ]]; then
  echo "==> compile + objdiff (candidate.c)" >&2
  if "$ROOT/scripts/compile-trial.sh" "$PROMPT_DIR" "$CANDIDATE_C"; then
    echo "Programmatic phase: PERFECT MATCH via candidate.c" >&2
    record_stage "candidate:matched"
    write_report "matched" 0 "candidate" ""
    exit 0
  else
    record_stage "candidate:mismatched"
  fi
else
  record_stage "candidate:missing"
fi

if [[ "$SKIP_PERMUTER" -eq 1 ]]; then
  echo "No perfect match; permuter skipped" >&2
  record_stage "permuter:skipped"
  write_report "no-match" 1 "" "permuter skipped"
  exit 1
fi

echo "==> decomp-permuter" >&2
PERM_OUT="$PROMPT_DIR/build/permuter-best.c"
if "$ROOT/scripts/run-permuter.sh" --prompt "$PROMPT_DIR" --out "$PERM_OUT"; then
  perm_rc=0
  record_stage "permuter:ran"
else
  perm_rc=$?
  record_stage "permuter:failed:$perm_rc"
fi

if [[ -f "$PERM_OUT" ]]; then
  echo "==> compile + objdiff (permuter)" >&2
  if "$ROOT/scripts/compile-trial.sh" "$PROMPT_DIR" "$PERM_OUT"; then
    echo "Programmatic phase: PERFECT MATCH via permuter" >&2
    record_stage "permuter:matched"
    write_report "matched" 0 "permuter" ""
    exit 0
  else
    record_stage "permuter:mismatched"
  fi
fi

if [[ "$perm_rc" -eq 0 ]]; then
  echo "Permuter reported perfect score but objdiff gate failed — verify toolchain/golden .o" >&2
  write_report "no-match" 1 "" "permuter reported success but verifier did not match"
else
  echo "Programmatic phase ended without objdiff 0 (permuter exit $perm_rc)" >&2
  write_report "no-match" 1 "" "permuter exit $perm_rc"
fi
exit 1
