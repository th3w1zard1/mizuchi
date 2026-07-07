#!/usr/bin/env bash
# Validate prompt case.yaml files and their agreement with settings.yaml.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/prompt-settings.sh
. "$ROOT/scripts/lib/prompt-settings.sh"
# shellcheck source=scripts/lib/case-metadata.sh
. "$ROOT/scripts/lib/case-metadata.sh"

prompt_root="${1:-$ROOT/prompts}"

if [[ ! -d "$prompt_root" ]]; then
  echo "validate-case-manifests: not a directory: $prompt_root" >&2
  exit 2
fi

prompt_dirs=()
if [[ -f "$prompt_root/settings.yaml" ]]; then
  prompt_dirs=("$prompt_root")
else
  for prompt_dir in "$prompt_root"/*; do
    [[ -d "$prompt_dir" ]] || continue
    [[ "$(basename "$prompt_dir")" != "_template" ]] || continue
    [[ -f "$prompt_dir/settings.yaml" ]] || continue
    prompt_dirs+=("$prompt_dir")
  done
fi

checked=0
missing=0
invalid=0

case_path_label() {
  local path="$1"
  if [[ "$path" == "$ROOT/"* ]]; then
    printf '%s' "${path#$ROOT/}"
  else
    printf '%s' "$path"
  fi
}

normalize_prompt_status() {
  case "$1" in
    pending|matched|in_progress|in-progress|integrated|blocked)
      printf '%s' "${1//-/_}"
      ;;
    *)
      return 1
      ;;
  esac
}

record_invalid() {
  local prompt_dir="$1" message="$2"
  echo "invalid case manifest in $(case_path_label "$prompt_dir"): $message" >&2
  invalid=$((invalid + 1))
}

require_case_field() {
  local prompt_dir="$1" field="$2"
  local value
  value="$(case_metadata_get "$prompt_dir" "$field" 2>/dev/null || true)"
  if [[ -z "$value" ]]; then
    record_invalid "$prompt_dir" "missing $field"
    return 1
  fi
  printf '%s' "$value"
}

json_get() {
  local file="$1" query="$2"
  jq -r "$query // \"\"" "$file" 2>/dev/null || true
}

sha256_file() {
  sha256sum "$1" | awk '{print $1}'
}

size_file() {
  stat -c %s "$1"
}

validate_verifier_report() {
  local prompt_dir="$1" report="$2" expected_status="${3:-}"
  local prompt_name
  prompt_name="$(basename "$prompt_dir")"

  if [[ -z "$report" || ! -f "$report" ]]; then
    record_invalid "$prompt_dir" "verifier report missing: ${report:-<empty>}"
    return 1
  fi

  if ! jq -e --arg prompt "$prompt_name" '
    .schema == "mizuchi.build-and-verify.v1"
    and .prompt == $prompt
    and (.status as $status | ["matched", "mismatched"] | index($status))
    and (.method as $method | ["objdiff", "cmp", "custom"] | index($method))
    and (.target_object | type == "string" and length > 0)
    and (.candidate_object | type == "string" and length > 0)
    and (.target_sha256 | test("^[0-9a-f]{64}$"))
    and (.candidate_sha256 | test("^[0-9a-f]{64}$"))
    and (.target_size | type == "number" and . > 0)
    and (.candidate_size | type == "number" and . > 0)
    and (.byte_identical | type == "boolean")
  ' "$report" >/dev/null; then
    record_invalid "$prompt_dir" "verifier report schema/proof mismatch"
    return 1
  fi

  local status method target_object candidate_object target_sha candidate_sha target_size candidate_size byte_identical
  status="$(json_get "$report" '.status')"
  method="$(json_get "$report" '.method')"
  target_object="$(json_get "$report" '.target_object')"
  candidate_object="$(json_get "$report" '.candidate_object')"
  target_sha="$(json_get "$report" '.target_sha256')"
  candidate_sha="$(json_get "$report" '.candidate_sha256')"
  target_size="$(json_get "$report" '.target_size')"
  candidate_size="$(json_get "$report" '.candidate_size')"
  byte_identical="$(json_get "$report" '.byte_identical')"

  if [[ -n "$expected_status" && "$status" != "$expected_status" ]]; then
    record_invalid "$prompt_dir" "verifier report status mismatch: expected $expected_status got $status"
  fi

  if [[ ! -f "$target_object" ]]; then
    record_invalid "$prompt_dir" "verifier report target object missing: $target_object"
  else
    actual_target_sha="$(sha256_file "$target_object")"
    actual_target_size="$(size_file "$target_object")"
    if [[ "$target_sha" != "$actual_target_sha" ]]; then
      record_invalid "$prompt_dir" "verifier report target hash mismatch"
    fi
    if [[ "$target_size" != "$actual_target_size" ]]; then
      record_invalid "$prompt_dir" "verifier report target size mismatch"
    fi
  fi
  if [[ ! -f "$candidate_object" ]]; then
    record_invalid "$prompt_dir" "verifier report candidate object missing: $candidate_object"
  else
    actual_candidate_sha="$(sha256_file "$candidate_object")"
    actual_candidate_size="$(size_file "$candidate_object")"
    if [[ "$candidate_sha" != "$actual_candidate_sha" ]]; then
      record_invalid "$prompt_dir" "verifier report candidate hash mismatch"
    fi
    if [[ "$candidate_size" != "$actual_candidate_size" ]]; then
      record_invalid "$prompt_dir" "verifier report candidate size mismatch"
    fi
  fi

  if [[ "$status" == "matched" && ( "$method" == "cmp" || "$method" == "custom" ) ]]; then
    if [[ "$byte_identical" != "true" || "$target_sha" != "$candidate_sha" || "$target_size" != "$candidate_size" ]]; then
      record_invalid "$prompt_dir" "matched verifier report is not byte-identical"
    fi
  elif [[ "$status" != "matched" && ( "$method" == "cmp" || "$method" == "custom" ) ]]; then
    if [[ "$byte_identical" == "true" && "$target_sha" == "$candidate_sha" && "$target_size" == "$candidate_size" ]]; then
      record_invalid "$prompt_dir" "mismatched verifier report is byte-identical"
    fi
  fi
}

validate_prompt_dir_field() {
  local prompt_dir="$1" report="$2" label="$3"
  local report_prompt_dir
  report_prompt_dir="$(json_get "$report" '.promptDir')"
  if [[ -z "$report_prompt_dir" || "$(readlink -f "$report_prompt_dir" 2>/dev/null || true)" != "$(readlink -f "$prompt_dir")" ]]; then
    record_invalid "$prompt_dir" "$label promptDir mismatch"
  fi
}

validate_programmatic_phase_report() {
  local prompt_dir="$1" report="$2"
  [[ -f "$report" ]] || return 0
  local prompt_name
  prompt_name="$(basename "$prompt_dir")"

  if ! jq -e --arg prompt "$prompt_name" '
    .schema == "mizuchi.programmatic-phase.v1"
    and .prompt == $prompt
    and (.exitCode | type == "number")
    and (.stages | type == "array")
    and (.status as $status | ["matched", "no-match", "blocked"] | index($status))
  ' "$report" >/dev/null; then
    record_invalid "$prompt_dir" "programmatic phase report schema/status mismatch"
    return 0
  fi

  validate_prompt_dir_field "$prompt_dir" "$report" "programmatic phase report"

  local status exit_code matched_stage reason verifier_report
  status="$(json_get "$report" '.status')"
  exit_code="$(json_get "$report" '.exitCode')"
  matched_stage="$(json_get "$report" '.matchedStage')"
  reason="$(json_get "$report" '.reason')"
  verifier_report="$(json_get "$report" '.verifierReport')"

  case "$status" in
    matched)
      if [[ "$exit_code" != "0" || -z "$matched_stage" ]]; then
        record_invalid "$prompt_dir" "matched programmatic phase has inconsistent outcome"
      fi
      if [[ "$matched_stage" != "m2c" && "$matched_stage" != "candidate" && "$matched_stage" != "permuter" ]]; then
        record_invalid "$prompt_dir" "matched programmatic phase has unknown matchedStage"
      fi
      validate_verifier_report "$prompt_dir" "$verifier_report" "matched" || true
      ;;
    no-match)
      if [[ "$exit_code" != "1" || -n "$matched_stage" || -z "$reason" ]]; then
        record_invalid "$prompt_dir" "no-match programmatic phase has inconsistent outcome"
      fi
      if [[ -n "$verifier_report" && -f "$verifier_report" ]]; then
        validate_verifier_report "$prompt_dir" "$verifier_report" || true
      fi
      ;;
    blocked)
      if [[ "$exit_code" != "3" || -n "$matched_stage" || -z "$reason" ]]; then
        record_invalid "$prompt_dir" "blocked programmatic phase has inconsistent outcome"
      fi
      if ! jq -e '.stages | index("blocked")' "$report" >/dev/null; then
        record_invalid "$prompt_dir" "blocked programmatic phase missing blocked stage"
      fi
      ;;
  esac
}

validate_ai_phase_report() {
  local prompt_dir="$1" report="$2"
  [[ -f "$report" ]] || return 0
  local prompt_name
  prompt_name="$(basename "$prompt_dir")"

  if ! jq -e --arg prompt "$prompt_name" '
    .schema == "mizuchi.ai-phase.v1"
    and .prompt == $prompt
    and (.status as $status | ["started", "matched", "failed", "manual-required", "blocked"] | index($status))
    and (.anthropicApiKeyPresent | type == "boolean")
    and ((.exitCode == null) or (.exitCode | type == "number"))
  ' "$report" >/dev/null; then
    record_invalid "$prompt_dir" "ai phase report schema/status mismatch"
    return 0
  fi

  validate_prompt_dir_field "$prompt_dir" "$report" "ai phase report"

  local status exit_code runner reason
  status="$(json_get "$report" '.status')"
  exit_code="$(json_get "$report" '.exitCode')"
  runner="$(json_get "$report" '.runner')"
  reason="$(json_get "$report" '.reason')"

  case "$status" in
    blocked)
      if [[ "$exit_code" != "3" || -n "$runner" || -z "$reason" ]]; then
        record_invalid "$prompt_dir" "blocked ai phase has inconsistent outcome"
      fi
      ;;
    manual-required)
      if [[ "$exit_code" != "3" || "$runner" != "cursor-native" || -z "$reason" ]]; then
        record_invalid "$prompt_dir" "manual-required ai phase has inconsistent outcome"
      fi
      ;;
    started)
      if [[ -n "$exit_code" || -z "$runner" || -z "$reason" ]]; then
        record_invalid "$prompt_dir" "started ai phase has inconsistent outcome"
      fi
      ;;
    matched)
      if [[ "$exit_code" != "0" || -z "$runner" || -z "$reason" ]]; then
        record_invalid "$prompt_dir" "matched ai phase has inconsistent outcome"
      fi
      ;;
    failed)
      if [[ -z "$exit_code" || "$exit_code" == "0" || -z "$runner" || -z "$reason" ]]; then
        record_invalid "$prompt_dir" "failed ai phase has inconsistent outcome"
      fi
      ;;
  esac
}

validate_decomp_function_receipt() {
  local prompt_dir="$1"
  local receipt="$prompt_dir/build/decomp-function.json"
  [[ -f "$receipt" ]] || return 0

  local prompt_name
  prompt_name="$(basename "$prompt_dir")"

  if ! jq -e --arg prompt "$prompt_name" '
    .schema == "mizuchi.decomp-function.v1"
    and .prompt == $prompt
    and (.exitCode | type == "number")
    and (.status as $status | ["matched", "manual-required", "blocked", "failed"] | index($status))
    and (.terminalPhase as $terminalPhase | ["programmatic", "ai"] | index($terminalPhase))
  ' "$receipt" >/dev/null; then
    record_invalid "$prompt_dir" "decomp-function receipt schema/status mismatch"
    return 0
  fi

  local receipt_prompt_dir
  receipt_prompt_dir="$(json_get "$receipt" '.promptDir')"
  if [[ -z "$receipt_prompt_dir" || "$(readlink -f "$receipt_prompt_dir" 2>/dev/null || true)" != "$(readlink -f "$prompt_dir")" ]]; then
    record_invalid "$prompt_dir" "decomp-function receipt promptDir mismatch"
  fi

  local terminal_phase status exit_code programmatic_report programmatic_status matched_stage ai_report ai_status
  terminal_phase="$(json_get "$receipt" '.terminalPhase')"
  status="$(json_get "$receipt" '.status')"
  exit_code="$(json_get "$receipt" '.exitCode')"
  programmatic_report="$(json_get "$receipt" '.programmaticReport')"
  programmatic_status="$(json_get "$receipt" '.programmaticStatus')"
  matched_stage="$(json_get "$receipt" '.matchedStage')"
  ai_report="$(json_get "$receipt" '.aiReport')"
  ai_status="$(json_get "$receipt" '.aiStatus')"

  if [[ -z "$programmatic_report" || ! -f "$programmatic_report" ]]; then
    record_invalid "$prompt_dir" "decomp-function receipt missing programmatic report"
  elif ! jq -e --arg status "$programmatic_status" '
    .schema == "mizuchi.programmatic-phase.v1"
    and .status == $status
  ' "$programmatic_report" >/dev/null; then
    record_invalid "$prompt_dir" "decomp-function programmatic report status mismatch"
  else
    phase_verifier_report="$(json_get "$programmatic_report" '.verifierReport')"
    if [[ "$programmatic_status" == "matched" ]]; then
      validate_verifier_report "$prompt_dir" "$phase_verifier_report" "matched" || true
    elif [[ -n "$phase_verifier_report" && -f "$phase_verifier_report" ]]; then
      validate_verifier_report "$prompt_dir" "$phase_verifier_report" || true
    fi
  fi

  case "$terminal_phase" in
    programmatic)
      if [[ -n "$ai_report" || -n "$ai_status" ]]; then
        record_invalid "$prompt_dir" "programmatic-terminal decomp-function receipt must not link ai report"
      fi
      case "$programmatic_status" in
        matched)
          if [[ "$status" != "matched" || "$exit_code" != "0" || -z "$matched_stage" ]]; then
            record_invalid "$prompt_dir" "matched programmatic receipt has inconsistent outcome"
          fi
          ;;
        blocked)
          if [[ "$status" != "blocked" || "$exit_code" != "3" ]]; then
            record_invalid "$prompt_dir" "blocked programmatic receipt has inconsistent outcome"
          fi
          ;;
      esac
      ;;
    ai)
      if [[ -z "$ai_report" || ! -f "$ai_report" ]]; then
        record_invalid "$prompt_dir" "decomp-function receipt missing ai report"
      elif ! jq -e --arg status "$ai_status" '
        .schema == "mizuchi.ai-phase.v1"
        and .status == $status
      ' "$ai_report" >/dev/null; then
        record_invalid "$prompt_dir" "decomp-function ai report status mismatch"
      fi
      if [[ "$ai_status" == "manual-required" && ( "$status" != "manual-required" || "$exit_code" != "3" ) ]]; then
        record_invalid "$prompt_dir" "manual-required ai receipt has inconsistent outcome"
      fi
      if [[ "$ai_status" == "blocked" && ( "$status" != "blocked" || "$exit_code" != "3" ) ]]; then
        record_invalid "$prompt_dir" "blocked ai receipt has inconsistent outcome"
      fi
      if [[ "$ai_status" == "started" && ( "$status" != "matched" || "$exit_code" != "0" ) ]]; then
        record_invalid "$prompt_dir" "started ai receipt has inconsistent terminal outcome"
      fi
      if [[ "$ai_status" == "matched" && ( "$status" != "matched" || "$exit_code" != "0" ) ]]; then
        record_invalid "$prompt_dir" "matched ai receipt has inconsistent terminal outcome"
      fi
      if [[ "$ai_status" == "failed" && "$status" != "failed" ]]; then
        record_invalid "$prompt_dir" "failed ai receipt has inconsistent terminal outcome"
      fi
      ;;
  esac
}

validate_lifecycle_metadata() {
  local prompt_dir="$1"
  local status
  status="$(case_metadata_get "$prompt_dir" status 2>/dev/null || true)"
  [[ -n "$status" ]] || return 0

  local normalized_status
  if ! normalized_status="$(normalize_prompt_status "$status")"; then
    record_invalid "$prompt_dir" "unknown status: $status"
    return 0
  fi

  case "$normalized_status" in
    blocked)
      require_case_field "$prompt_dir" blockedReason >/dev/null || true
      ;;
    integrated)
      local source_path receipt_path integrated_at
      source_path="$(require_case_field "$prompt_dir" integratedSourcePath || true)"
      receipt_path="$(require_case_field "$prompt_dir" integrationReceiptPath || true)"
      integrated_at="$(require_case_field "$prompt_dir" integratedAt || true)"
      [[ -n "$integrated_at" ]] || true

      if [[ -n "$source_path" ]]; then
        source_path="$(case_metadata_resolve_path "$ROOT" "$prompt_dir" "$source_path")"
        if [[ ! -f "$source_path" ]]; then
          record_invalid "$prompt_dir" "integratedSourcePath does not exist: $source_path"
        fi
      fi

      if [[ -n "$receipt_path" ]]; then
        receipt_path="$(case_metadata_resolve_path "$ROOT" "$prompt_dir" "$receipt_path")"
        if [[ ! -f "$receipt_path" ]]; then
          record_invalid "$prompt_dir" "integrationReceiptPath does not exist: $receipt_path"
        elif ! jq -e --arg source_path "$source_path" '
          .schema == "mizuchi.integration-receipt.v1"
          and .status == "integrated"
          and .sourceOut == $source_path
        ' "$receipt_path" >/dev/null; then
          record_invalid "$prompt_dir" "integration receipt does not match integrated source"
        else
          local candidate_source candidate_sha source_sha
          candidate_source="$(json_get "$receipt_path" '.candidateSource')"
          candidate_sha="$(json_get "$receipt_path" '.candidateSourceSha256')"
          source_sha="$(json_get "$receipt_path" '.sourceOutSha256')"
          if [[ -z "$candidate_source" || ! -f "$candidate_source" ]]; then
            record_invalid "$prompt_dir" "integration receipt candidate source missing: ${candidate_source:-<empty>}"
          elif [[ "$candidate_sha" != "$(sha256_file "$candidate_source")" ]]; then
            record_invalid "$prompt_dir" "integration receipt candidate source hash mismatch"
          fi
          if [[ -n "$source_path" && -f "$source_path" && "$source_sha" != "$(sha256_file "$source_path")" ]]; then
            record_invalid "$prompt_dir" "integration receipt source output hash mismatch"
          fi
          local integration_verifier_report
          integration_verifier_report="$(json_get "$receipt_path" '.verifierReport')"
          validate_verifier_report "$prompt_dir" "$integration_verifier_report" "matched" || true
        fi
      fi
      ;;
  esac
}

for prompt_dir in "${prompt_dirs[@]}"; do
  checked=$((checked + 1))

  if [[ ! -f "$prompt_dir/case.yaml" ]]; then
    echo "missing case.yaml: ${prompt_dir#$ROOT/}" >&2
    missing=$((missing + 1))
    continue
  fi

  settings_function="$(prompt_settings_get "$prompt_dir" functionName)"
  settings_target="$(prompt_settings_get "$prompt_dir" targetObjectPath)"
  case_function="$(case_metadata_get_default "$prompt_dir" functionName "$settings_function")"
  case_target="$(case_metadata_get_default "$prompt_dir" targetObjectPath "$settings_target")"

  if [[ "$settings_function" != "$case_function" ]]; then
    echo "case mismatch in ${prompt_dir#$ROOT/}: functionName settings=$settings_function case=$case_function" >&2
    invalid=$((invalid + 1))
  fi
  if [[ "$settings_target" != "$case_target" ]]; then
    echo "case mismatch in ${prompt_dir#$ROOT/}: targetObjectPath settings=$settings_target case=$case_target" >&2
    invalid=$((invalid + 1))
  fi

  validate_lifecycle_metadata "$prompt_dir"
  if [[ -f "$prompt_dir/build/build-and-verify.json" ]]; then
    validate_verifier_report "$prompt_dir" "$prompt_dir/build/build-and-verify.json" || true
  fi
  validate_programmatic_phase_report "$prompt_dir" "$prompt_dir/build/programmatic-phase.json"
  validate_ai_phase_report "$prompt_dir" "$prompt_dir/build/ai-phase.json"
  validate_decomp_function_receipt "$prompt_dir"
done

if [[ "$missing" -gt 0 || "$invalid" -gt 0 ]]; then
  exit 1
fi

echo "CASE_MANIFESTS_OK checked=$checked"
