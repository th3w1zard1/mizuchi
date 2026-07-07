#!/usr/bin/env bash
# Report whether one prompt has the concrete inputs needed for production matching.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/prompt-settings.sh
. "$ROOT/scripts/lib/prompt-settings.sh"
# shellcheck source=scripts/lib/case-metadata.sh
. "$ROOT/scripts/lib/case-metadata.sh"
# shellcheck source=scripts/lib/reconkit-config.sh
. "$ROOT/scripts/lib/reconkit-config.sh"

usage() {
  cat <<'EOF'
usage: decomp-readiness.sh --prompt <prompts/<name>>
       decomp-readiness.sh --all [--prompts-dir <prompts/>]

Checks the non-negotiable inputs for a real matching-decompilation attempt:
settings/prompt/case metadata, proof target object, non-placeholder compiler
command, and objdiff availability. Exits 0 only when production matching is
ready to run.
EOF
}

prompt_dir=""
all_prompts=0
prompts_dir="$ROOT/prompts"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) prompt_dir="$2"; shift 2 ;;
    --all) all_prompts=1; shift ;;
    --prompts-dir) prompts_dir="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ "$all_prompts" -eq 1 && -n "$prompt_dir" ]]; then
  echo "--all and --prompt are mutually exclusive" >&2
  usage
  exit 2
fi

if [[ "$all_prompts" -eq 0 && -z "$prompt_dir" ]]; then
  echo "missing --prompt" >&2
  usage
  exit 2
fi

blockers=()
warnings=()

add_blocker() {
  blockers+=("$1")
}

add_warning() {
  warnings+=("$1")
}

json_array_from_lines() {
  if [[ "$#" -eq 0 ]]; then
    printf '[]'
  else
    printf '%s\n' "$@" | jq -R . | jq -s .
  fi
}

json_bool() {
  if [[ "$1" -eq 1 ]]; then
    printf 'true'
  else
    printf 'false'
  fi
}

prompt_dirs_under_root() {
  local root="$1"
  if [[ ! -d "$root" ]]; then
    return 0
  fi
  for dir in "$root"/*; do
    [[ -d "$dir" ]] || continue
    [[ "$(basename "$dir")" != "_template" ]] || continue
    [[ -f "$dir/settings.yaml" ]] || continue
    printf '%s\n' "$dir"
  done
}

if [[ "$all_prompts" -eq 1 ]]; then
  if [[ ! -d "$prompts_dir" ]]; then
    jq -n \
      --arg schema "reconkit.decomp-readiness-summary.v1" \
      --arg promptsDir "$prompts_dir" \
      '{
        schema: $schema,
        status: "not-ready",
        promptsDir: $promptsDir,
        total: 0,
        ready: 0,
        notReady: 0,
        blockersTotal: 1,
        warningsTotal: 0,
        blockerSummary: {"prompts directory does not exist": 1},
        prompts: []
      }'
    exit 1
  fi

  prompt_reports=()
  while IFS= read -r dir; do
    set +e
    report="$("$0" --prompt "$dir")"
    rc=$?
    set -e
    if [[ "$rc" -ne 0 && "$rc" -ne 1 ]]; then
      exit "$rc"
    fi
    prompt_reports+=("$report")
  done < <(prompt_dirs_under_root "$prompts_dir")

  if [[ "${#prompt_reports[@]}" -eq 0 ]]; then
    jq -n \
      --arg schema "reconkit.decomp-readiness-summary.v1" \
      --arg promptsDir "$prompts_dir" \
      '{
        schema: $schema,
        status: "not-ready",
        promptsDir: $promptsDir,
        total: 0,
        ready: 0,
        notReady: 0,
        blockersTotal: 1,
        warningsTotal: 0,
        blockerSummary: {"no prompt folders found": 1},
        prompts: []
      }'
    exit 1
  fi

  reports_json="$(printf '%s\n' "${prompt_reports[@]}" | jq -s '.')"
  summary="$(jq -n \
    --arg schema "reconkit.decomp-readiness-summary.v1" \
    --arg promptsDir "$prompts_dir" \
    --argjson prompts "$reports_json" \
    '{
      schema: $schema,
      status: (if ([ $prompts[].status ] | all(. == "ready")) then "ready" else "not-ready" end),
      promptsDir: $promptsDir,
      total: ($prompts | length),
      ready: ([ $prompts[] | select(.status == "ready") ] | length),
      notReady: ([ $prompts[] | select(.status != "ready") ] | length),
      blockersTotal: ([ $prompts[].blockers[]? ] | length),
      warningsTotal: ([ $prompts[].warnings[]? ] | length),
      blockerSummary: ([ $prompts[].blockers[]? ] | group_by(.) | map({key: .[0], value: length}) | from_entries),
      prompts: $prompts
    }')"
  printf '%s\n' "$summary"
  [[ "$(jq -r '.status' <<<"$summary")" == "ready" ]]
  exit $?
fi

if [[ ! -d "$prompt_dir" ]]; then
  jq -n \
    --arg schema "reconkit.decomp-readiness.v1" \
    --arg promptDir "$prompt_dir" \
    --argjson blockers "$(json_array_from_lines "prompt directory does not exist")" \
    '{schema: $schema, status: "not-ready", prompt: null, promptDir: $promptDir, blockers: $blockers, warnings: []}'
  exit 1
fi

prompt_dir="$(cd "$prompt_dir" && pwd)"
prompt_name="$(basename "$prompt_dir")"

settings_valid=0
prompt_md_present=0
case_present=0
target_present=0
candidate_present=0
compiler_configured=0
compiler_placeholder=0
objdiff_present=0
custom_verifier_configured=0

if "$ROOT/scripts/validate-prompt-settings.sh" "$prompt_dir" >/dev/null 2>&1; then
  settings_valid=1
else
  add_blocker "settings.yaml or prompt.md failed validation"
fi

[[ -f "$prompt_dir/prompt.md" ]] && prompt_md_present=1
case_metadata_has_file "$prompt_dir" && case_present=1 || add_blocker "case.yaml is missing"

function_name=""
target_raw=""
target_object=""
case_status=""
target_family=""
binary_path=""
compiler_command=""
compiler_source="none"
candidate_source=""
verifier_command=""

if [[ "$settings_valid" -eq 1 ]]; then
  function_name="$(prompt_settings_get "$prompt_dir" functionName)"
  target_raw="$(prompt_settings_get "$prompt_dir" targetObjectPath)"
  target_raw="$(case_metadata_expand "$target_raw" "$function_name" "$prompt_name")"
  target_object="$(case_metadata_resolve_path "$ROOT" "$prompt_dir" "$target_raw")"
  if [[ -f "$target_object" ]]; then
    target_present=1
  else
    add_blocker "targetObjectPath does not exist: $target_object"
  fi

  candidate_source="$(case_metadata_get_default "$prompt_dir" candidateSourcePath "prompt:/candidate.c")"
  candidate_source="$(case_metadata_expand "$candidate_source" "$function_name" "$prompt_name")"
  candidate_source="$(case_metadata_resolve_path "$ROOT" "$prompt_dir" "$candidate_source")"
  if [[ -f "$candidate_source" ]]; then
    candidate_present=1
  else
    add_warning "candidate source is not present yet: $candidate_source"
  fi
fi

if [[ "$case_present" -eq 1 ]]; then
  case_status="$(case_metadata_get_default "$prompt_dir" status "")"
  target_family="$(case_metadata_get_default "$prompt_dir" targetFamily "")"
  binary_path="$(case_metadata_get_default "$prompt_dir" binaryPath "")"
  verifier_command="$(case_metadata_get_default "$prompt_dir" verifierCommand "")"
  [[ -n "$verifier_command" ]] && custom_verifier_configured=1
  if [[ "$case_status" == "blocked" ]]; then
    blocked_reason="$(case_metadata_get_default "$prompt_dir" blockedReason "case.yaml status is blocked")"
    add_blocker "case.yaml status is blocked: $blocked_reason"
  fi
  [[ -n "$target_family" ]] || add_warning "case.yaml has no targetFamily"
  [[ -n "$binary_path" ]] || add_warning "case.yaml has no binaryPath provenance"
fi

if [[ "$custom_verifier_configured" -eq 1 ]]; then
  compiler_source="custom verifier"
else
  compiler_command="$(case_metadata_get_default "$prompt_dir" compilerCommand "")"
  if [[ -n "$compiler_command" ]]; then
    compiler_source="case.yaml"
  else
    runtime_cfg=""
    if recovery_config_resolve >/dev/null 2>&1; then
      runtime_cfg="$RECONKIT_CONFIG_PATH"
      compiler_command="$(recovery_config_get "global.compilerScript" optional || true)"
      [[ -n "$compiler_command" ]] && compiler_source="$runtime_cfg"
    fi
  fi

  if [[ -n "$compiler_command" ]]; then
    compiler_configured=1
    if [[ "$compiler_command" == *"compile-placeholder.sh"* ]]; then
      compiler_placeholder=1
      add_blocker "compiler command is still the placeholder"
    fi
  else
    add_blocker "compiler command is missing"
  fi
fi

if [[ "$custom_verifier_configured" -eq 1 ]]; then
  objdiff_present=0
else
  if command -v objdiff >/dev/null 2>&1; then
    objdiff_present=1
  else
    add_blocker "objdiff is not on PATH"
  fi
fi

status="ready"
if [[ "${#blockers[@]}" -gt 0 ]]; then
  status="not-ready"
fi

jq -n \
  --arg schema "reconkit.decomp-readiness.v1" \
  --arg status "$status" \
  --arg prompt "$prompt_name" \
  --arg promptDir "$prompt_dir" \
  --arg functionName "$function_name" \
  --arg targetObject "$target_object" \
  --arg targetFamily "$target_family" \
  --arg binaryPath "$binary_path" \
  --arg caseStatus "$case_status" \
  --arg candidateSource "$candidate_source" \
  --arg compilerSource "$compiler_source" \
  --argjson settingsValid "$(json_bool "$settings_valid")" \
  --argjson promptMdPresent "$(json_bool "$prompt_md_present")" \
  --argjson casePresent "$(json_bool "$case_present")" \
  --argjson targetObjectPresent "$(json_bool "$target_present")" \
  --argjson candidateSourcePresent "$(json_bool "$candidate_present")" \
  --argjson compilerConfigured "$(json_bool "$compiler_configured")" \
  --argjson compilerPlaceholder "$(json_bool "$compiler_placeholder")" \
  --argjson objdiffPresent "$(json_bool "$objdiff_present")" \
  --argjson customVerifierConfigured "$(json_bool "$custom_verifier_configured")" \
  --argjson blockers "$(json_array_from_lines "${blockers[@]}")" \
  --argjson warnings "$(json_array_from_lines "${warnings[@]}")" \
  '{
    schema: $schema,
    status: $status,
    prompt: $prompt,
    promptDir: $promptDir,
    functionName: (if $functionName == "" then null else $functionName end),
    targetObject: (if $targetObject == "" then null else $targetObject end),
    targetFamily: (if $targetFamily == "" then null else $targetFamily end),
    binaryPath: (if $binaryPath == "" then null else $binaryPath end),
    caseStatus: (if $caseStatus == "" then null else $caseStatus end),
    candidateSource: (if $candidateSource == "" then null else $candidateSource end),
    compilerSource: $compilerSource,
    checks: {
      settingsValid: $settingsValid,
      promptMdPresent: $promptMdPresent,
      casePresent: $casePresent,
      targetObjectPresent: $targetObjectPresent,
      candidateSourcePresent: $candidateSourcePresent,
      compilerConfigured: $compilerConfigured,
      compilerPlaceholder: $compilerPlaceholder,
      objdiffPresent: $objdiffPresent,
      customVerifierConfigured: $customVerifierConfigured
    },
    blockers: $blockers,
    warnings: $warnings
  }'

[[ "$status" == "ready" ]]
