#!/usr/bin/env bash
# Compile trial.c and verify with objdiff; JSON on stdout, trace on stderr.
set -euo pipefail

root_dir="${MIZUCHI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=scripts/lib/build-defensive.sh
source "$root_dir/scripts/lib/build-defensive.sh"
# shellcheck source=scripts/lib/verify-objdiff.sh
source "$root_dir/scripts/lib/verify-objdiff.sh"
# shellcheck source=scripts/lib/check-log.sh
source "$root_dir/scripts/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$root_dir/scripts/lib/guide-manifest.sh"
# shellcheck source=scripts/lib/cli-agent.sh
source "$root_dir/scripts/lib/cli-agent.sh"

usage() {
  cat <<EOF
Usage: build-and-verify.sh --prompt <prompt-name> --target <target.o> [--commit] [--quiet]

Compiles prompts/<name>/trial.c to build/candidate.o and runs objdiff verification.

Options:
  --commit  git add/commit trial.c and candidate.o on 0-diff match
  --quiet   Suppress verbose trace (keep summary + JSON)
  -h, --help  Show help

Examples:
  ./scripts/build-and-verify.sh --prompt fun_00148020 --target path/to/target.o
  ./scripts/build-and-verify.sh --prompt fun_00148020 --target path/to/target.o --commit --quiet
EOF
}

prompt_name=""
target_obj=""
commit_on_match=0
quiet=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) prompt_name="${2:-}"; shift 2 ;;
    --target) target_obj="${2:-}"; shift 2 ;;
    --commit) commit_on_match=1; shift ;;
    --quiet) quiet=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Error: unknown argument: $1" >&2; echo "  ./scripts/build-and-verify.sh --help" >&2; exit 2 ;;
  esac
done

[[ "$quiet" -eq 1 ]] && check_log_configure --quiet
check_log_init "build-and-verify"
guide_manifest_load "$root_dir"
guide_manifest_trace_defaults "$root_dir"

if [[ -z "$prompt_name" || -z "$target_obj" ]]; then
  cli_agent_missing_arg "build-and-verify.sh" "requires --prompt and --target" \
    "./scripts/build-and-verify.sh --prompt fun_00148020 --target path/to/target.o"
fi

prompt_dir="$GUIDE_PROMPTS_DIR/$prompt_name"
trial_c="$prompt_dir/trial.c"
build_dir="$(guide_prompt_build_path "$prompt_dir" "")"
build_dir="${build_dir%/}"
candidate_o="$(guide_prompt_build_path "$prompt_dir" "candidate.o")"
mkdir -p "$build_dir"
check_log_file_op "$(guide_manifest_rel "$root_dir" "$build_dir")" "ensure-dir"

if [[ ! -f "$trial_c" ]]; then
  check_log_fail "missing $(guide_manifest_rel "$root_dir" "$trial_c")"
  check_log_summary "BUILD_AND_VERIFY_FAIL"
  echo "{\"status\":\"compile_error\",\"message\":\"trial.c missing\"}"
  exit 1
fi
check_log_read_file "$trial_c" "$(guide_manifest_rel "$root_dir" "$trial_c")" "trial.c"

candidate_existed=0
[[ -f "$candidate_o" ]] && candidate_existed=1

check_log_run_cmd "compile" "$(guide_manifest_rel "$root_dir" "$trial_c") -> $(guide_manifest_rel "$root_dir" "$candidate_o")"
if ! build_compile_defensive "$trial_c" "$candidate_o" >/dev/null; then
  check_log_fail "compile failed"
  check_log_summary "BUILD_AND_VERIFY_FAIL"
  echo "{\"status\":\"compile_error\",\"message\":\"BUILD FAILED. Treat this as a failed attempt and retry with simpler code.\"}"
  exit 1
fi
check_log_file_written "$candidate_o" "$root_dir" "$candidate_existed"

if [[ -f "$target_obj" ]]; then
  check_log_read_file "$target_obj" "$(guide_manifest_rel "$root_dir" "$target_obj")" "golden target"
else
  check_log_fail "missing target $(guide_manifest_rel "$root_dir" "$target_obj")"
  check_log_summary "BUILD_AND_VERIFY_FAIL"
  echo "{\"status\":\"infra_error\",\"message\":\"target object missing\"}"
  exit 2
fi

check_log_run_step "objdiff verify"
set +e
verify_json="$(verify_with_objdiff "$target_obj" "$candidate_o" 2>&1)"
verify_rc=$?
set -e
if [[ "$verify_rc" -ne 0 ]]; then
  check_log_fail "verify_with_objdiff failed"
  check_log_summary "BUILD_AND_VERIFY_FAIL"
  jq -n \
    --arg status "infra_error" \
    --arg message "Verification tooling failed" \
    --arg raw "$verify_json" \
    '{status:$status,message:$message,raw:$raw}'
  exit 2
fi

status="$(jq -r '.status // "unknown"' <<<"$verify_json")"
diffs="$(jq -r '.differences // -1' <<<"$verify_json")"
if [[ "$status" == "error" ]]; then
  check_log_fail "objdiff error: $(jq -r '.message // "unknown"' <<<"$verify_json")"
  check_log_summary "BUILD_AND_VERIFY_FAIL"
  jq -n \
    --arg status "infra_error" \
    --arg message "$(jq -r '.message // "Verification tooling failed"' <<<"$verify_json")" \
    --argjson upstream "$verify_json" \
    '{status:$status,message:$message,upstream:$upstream}'
  exit 2
fi

if [[ "$status" == "matched" && "$diffs" == "0" && "$commit_on_match" -eq 1 ]]; then
  check_log_trace "run   git add $(guide_manifest_rel "$root_dir" "$trial_c") $(guide_manifest_rel "$root_dir" "$candidate_o")"
  git add "$trial_c" "$candidate_o" 2>/dev/null || true
  if ! git diff --cached --quiet; then
    check_log_trace "run   git commit match($prompt_name)"
    git commit -m "match($prompt_name): verified objdiff 0"
    check_log_pass "git commit match($prompt_name)"
  else
    check_log_trace "run   git commit skipped (nothing staged)"
  fi
fi

if [[ "$status" == "matched" && "$diffs" == "0" ]]; then
  check_log_summary "BUILD_AND_VERIFY_OK"
  printf 'BUILD_AND_VERIFY_OK prompt=%s differences=0\n' "$prompt_name" >&2
else
  check_log_summary "BUILD_AND_VERIFY_MISMATCH"
  printf 'BUILD_AND_VERIFY_OK prompt=%s status=%s differences=%s\n' "$prompt_name" "$status" "$diffs" >&2
fi

echo "$verify_json"
