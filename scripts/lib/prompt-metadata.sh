#!/usr/bin/env bash
# Shared readers for prompt-local metadata used by discovery and orchestration surfaces.

prompt_metadata_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/case-manifest.sh
source "$prompt_metadata_lib_dir/case-manifest.sh"
# shellcheck source=scripts/lib/prompt-settings.sh
source "$prompt_metadata_lib_dir/prompt-settings.sh"
# shellcheck source=scripts/lib/target-adapters.sh
source "$prompt_metadata_lib_dir/target-adapters.sh"

prompt_metadata_name() {
  local prompt_dir="$1"
  basename "$prompt_dir"
}

prompt_metadata_case_id() {
  local prompt_dir="$1"
  local fallback
  fallback="$(prompt_metadata_name "$prompt_dir")"
  case_manifest_get "$prompt_dir" caseId 2>/dev/null || printf '%s\n' "$fallback"
}

prompt_metadata_status() {
  local prompt_dir="$1"
  local notes_file="$prompt_dir/notes.md"
  local status="pending"

  if [[ -f "$notes_file" ]]; then
    if grep -q "status.*integrated" "$notes_file" 2>/dev/null; then
      status="integrated"
    elif grep -q "status.*matched" "$notes_file" 2>/dev/null; then
      status="matched"
    elif grep -q "status.*in_progress\|status.*in-progress" "$notes_file" 2>/dev/null; then
      status="in_progress"
    elif grep -q "status.*blocked" "$notes_file" 2>/dev/null; then
      status="blocked"
    fi
  fi

  printf '%s\n' "$status"
}

prompt_metadata_function_name() {
  local prompt_dir="$1"
  local prompt_name
  local function_name=""

  prompt_name="$(prompt_metadata_name "$prompt_dir")"
  function_name="$(case_manifest_get "$prompt_dir" symbol.name 2>/dev/null || true)"
  if [[ -n "$function_name" ]]; then
    printf '%s\n' "$function_name"
    return 0
  fi

  function_name="$(prompt_settings_get "$prompt_dir" functionName 2>/dev/null || true)"
  if [[ -n "$function_name" ]]; then
    printf '%s\n' "$function_name"
    return 0
  fi

  if [[ -f "$prompt_dir/prompt.md" ]]; then
    function_name="$(grep -o 'Decompile `[^`]\+' "$prompt_dir/prompt.md" 2>/dev/null | head -1 | sed 's/Decompile `//;s/`$//' || true)"
    if [[ -n "$function_name" ]]; then
      printf '%s\n' "$function_name"
      return 0
    fi
  fi

  printf '%s\n' "$(echo "$prompt_name" | sed 's/fun_/FUN_/' | tr '[:lower:]' '[:upper:]')"
}

prompt_metadata_last_updated_mtime() {
  local prompt_dir="$1"
  stat -c %Y "$prompt_dir" 2>/dev/null || printf '0\n'
}

prompt_metadata_last_updated_date() {
  local prompt_dir="$1"
  local mtime

  mtime="$(prompt_metadata_last_updated_mtime "$prompt_dir")"
  if [[ "$mtime" != "0" ]]; then
    date -u -d "@$mtime" +"%Y-%m-%d" 2>/dev/null || true
  fi
}

prompt_metadata_adapter_id() {
  local prompt_dir="$1"
  case_manifest_get "$prompt_dir" adapter.id 2>/dev/null || printf 'unknown\n'
}

prompt_metadata_adapter_supported() {
  local prompt_dir="$1"
  local adapter_id

  adapter_id="$(prompt_metadata_adapter_id "$prompt_dir")"
  if target_adapter_is_supported "$adapter_id"; then
    printf 'true\n'
  else
    printf 'false\n'
  fi
}

prompt_metadata_target_family() {
  local prompt_dir="$1"
  local adapter_id="${2:-}"
  local target_family=""

  target_family="$(case_manifest_get "$prompt_dir" target.family 2>/dev/null || true)"
  if [[ -n "$target_family" ]]; then
    printf '%s\n' "$target_family"
    return 0
  fi

  [[ -n "$adapter_id" ]] || adapter_id="$(prompt_metadata_adapter_id "$prompt_dir")"
  if target_adapter_is_supported "$adapter_id"; then
    target_adapter_expected_family "$adapter_id"
    return 0
  fi

  printf 'unknown\n'
}

prompt_metadata_load_tool() {
  local prompt_dir="$1"
  local adapter_id="${2:-}"

  [[ -n "$adapter_id" ]] || adapter_id="$(prompt_metadata_adapter_id "$prompt_dir")"
  target_adapter_case_load_tool "$prompt_dir" "$adapter_id" 2>/dev/null || printf 'unknown\n'
}

prompt_metadata_context_path() {
  local prompt_dir="$1"
  local adapter_id="${2:-}"

  [[ -n "$adapter_id" ]] || adapter_id="$(prompt_metadata_adapter_id "$prompt_dir")"
  target_adapter_case_context_path "$prompt_dir" "$adapter_id" 2>/dev/null || printf '\n'
}

prompt_metadata_proof_target() {
  local prompt_dir="$1"
  local proof_target=""

  proof_target="$(case_manifest_get "$prompt_dir" proof.targetObjectPath 2>/dev/null || true)"
  if [[ -n "$proof_target" ]]; then
    printf '%s\n' "$proof_target"
    return 0
  fi

  prompt_settings_get "$prompt_dir" targetObjectPath 2>/dev/null || true
}

prompt_metadata_summary_json() {
  local prompt_dir="$1"
  local prompt_name status mtime adapter_supported

  prompt_name="$(prompt_metadata_name "$prompt_dir")"
  status="$(prompt_metadata_status "$prompt_dir")"
  mtime="$(prompt_metadata_last_updated_mtime "$prompt_dir")"

  local metadata_json
  if [[ -f "$prompt_dir/case.yaml" ]] && command -v ruby >/dev/null 2>&1; then
    metadata_json="$(ruby -ryaml -rjson - "$prompt_dir" "$prompt_name" <<'RUBY'
dir, prompt_name = ARGV

def dig_path(data, path)
  path.split(".").reduce(data) do |acc, key|
    acc.is_a?(Hash) ? acc[key] : nil
  end
end

case_path = File.join(dir, "case.yaml")
settings_path = File.join(dir, "settings.yaml")
case_data = File.exist?(case_path) ? (YAML.load_file(case_path) || {}) : {}
settings_data = File.exist?(settings_path) ? (YAML.load_file(settings_path) || {}) : {}
case_data = {} unless case_data.is_a?(Hash)
settings_data = {} unless settings_data.is_a?(Hash)

adapter_id = dig_path(case_data, "adapter.id").to_s
adapter_id = "unknown" if adapter_id.empty?

function_name = dig_path(case_data, "symbol.name").to_s
function_name = settings_data["functionName"].to_s if function_name.empty?
function_name = prompt_name.sub(/^fun_/, "FUN_").upcase if function_name.empty?

proof_target = dig_path(case_data, "proof.targetObjectPath").to_s
proof_target = settings_data["targetObjectPath"].to_s if proof_target.empty?

payload = {
  case_id: dig_path(case_data, "caseId").to_s,
  function_name: function_name,
  adapter: adapter_id,
  target_family: dig_path(case_data, "target.family").to_s,
  proof_target: proof_target,
  load_tool: dig_path(case_data, "load.tool").to_s,
  context_path: dig_path(case_data, "load.contextPath").to_s
}
payload[:case_id] = prompt_name if payload[:case_id].empty?
puts JSON.generate(payload)
RUBY
)"
  else
    metadata_json="$(jq -n \
      --arg case_id "$(prompt_metadata_case_id "$prompt_dir")" \
      --arg function_name "$(prompt_metadata_function_name "$prompt_dir")" \
      --arg adapter "$(prompt_metadata_adapter_id "$prompt_dir")" \
      --arg target_family "$(prompt_metadata_target_family "$prompt_dir")" \
      --arg proof_target "$(prompt_metadata_proof_target "$prompt_dir")" \
      --arg load_tool "$(prompt_metadata_load_tool "$prompt_dir")" \
      --arg context_path "$(prompt_metadata_context_path "$prompt_dir")" \
      '{case_id: $case_id, function_name: $function_name, adapter: $adapter, target_family: $target_family, proof_target: $proof_target, load_tool: $load_tool, context_path: $context_path}')"
  fi

  local adapter_id target_family load_tool context_path
  adapter_id="$(jq -r '.adapter // "unknown"' <<<"$metadata_json")"
  if target_adapter_is_supported "$adapter_id"; then
    adapter_supported=true
  else
    adapter_supported=false
  fi

  target_family="$(jq -r '.target_family // ""' <<<"$metadata_json")"
  if [[ -z "$target_family" || "$target_family" == "null" ]]; then
    target_family="$(target_adapter_expected_family "$adapter_id" 2>/dev/null || printf 'unknown\n')"
  fi

  load_tool="$(jq -r '.load_tool // ""' <<<"$metadata_json")"
  if [[ -z "$load_tool" || "$load_tool" == "null" ]]; then
    load_tool="$(target_adapter_default_load_tool "$adapter_id" 2>/dev/null || printf 'unknown\n')"
  fi

  context_path="$(jq -r '.context_path // ""' <<<"$metadata_json")"
  if [[ -z "$context_path" || "$context_path" == "null" ]]; then
    context_path="$(target_adapter_default_context_path "$adapter_id" 2>/dev/null || printf '\n')"
  fi

  jq -n \
    --arg case_id "$(jq -r '.case_id // ""' <<<"$metadata_json")" \
    --arg status "$status" \
    --arg function_name "$(jq -r '.function_name // ""' <<<"$metadata_json")" \
    --arg mtime "$mtime" \
    --arg adapter "$adapter_id" \
    --arg target_family "$target_family" \
    --arg proof_target "$(jq -r '.proof_target // ""' <<<"$metadata_json")" \
    --arg load_tool "$load_tool" \
    --arg context_path "$context_path" \
    --argjson adapter_supported "$adapter_supported" \
    '{case_id: $case_id, status: $status, function_name: $function_name, last_updated_mtime: ($mtime | tonumber), adapter: $adapter, adapter_supported: $adapter_supported, target_family: $target_family, proof_target: $proof_target, load_tool: $load_tool, context_path: $context_path}'
}
