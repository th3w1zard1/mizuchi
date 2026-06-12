#!/usr/bin/env bash
# Validate prompt-local case.yaml files against the workspace contract.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
quiet=0

# shellcheck source=scripts/lib/prompt-settings.sh
source "$ROOT/scripts/lib/prompt-settings.sh"
# shellcheck source=scripts/lib/case-manifest.sh
source "$ROOT/scripts/lib/case-manifest.sh"
# shellcheck source=scripts/lib/target-adapters.sh
source "$ROOT/scripts/lib/target-adapters.sh"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet) quiet=1; shift ;;
    -h|--help)
      echo "usage: $0 [--quiet]" >&2
      exit 2
      ;;
    *) echo "unexpected argument: $1" >&2; exit 2 ;;
  esac
done

log() {
  [[ "$quiet" -eq 0 ]] && echo "$1" >&2
}

errors=0

for prompt_dir in "$ROOT"/prompts/*/; do
  [[ -d "$prompt_dir" ]] || continue
  prompt_name="$(basename "$prompt_dir")"
  [[ "$prompt_name" == "_template" ]] && continue

  case_file="$prompt_dir/case.yaml"
  if [[ ! -f "$case_file" ]]; then
    log "invalid: prompts/$prompt_name missing case.yaml"
    errors=1
    continue
  fi

  schema_version="$(case_manifest_get "$prompt_dir" schemaVersion 2>/dev/null || true)"
  case_id="$(case_manifest_get "$prompt_dir" caseId 2>/dev/null || true)"
  adapter_id="$(case_manifest_get "$prompt_dir" adapter.id 2>/dev/null || true)"
  adapter_profile="$(case_manifest_get "$prompt_dir" adapter.capabilitiesProfile 2>/dev/null || true)"
  ingest_source_type="$(case_manifest_get "$prompt_dir" ingest.sourceType 2>/dev/null || true)"
  ingest_source_path="$(case_manifest_get "$prompt_dir" ingest.sourcePath 2>/dev/null || true)"
  ingest_provenance="$(case_manifest_get "$prompt_dir" ingest.provenance 2>/dev/null || true)"
  target_family="$(case_manifest_get "$prompt_dir" target.family 2>/dev/null || true)"
  target_binary="$(case_manifest_get "$prompt_dir" target.binary 2>/dev/null || true)"
  target_platform="$(case_manifest_get "$prompt_dir" target.platform 2>/dev/null || true)"
  load_tool="$(case_manifest_get "$prompt_dir" load.tool 2>/dev/null || true)"
  load_program_path="$(case_manifest_get "$prompt_dir" load.programPath 2>/dev/null || true)"
  load_context_path="$(case_manifest_get "$prompt_dir" load.contextPath 2>/dev/null || true)"
  symbol_name="$(case_manifest_get "$prompt_dir" symbol.name 2>/dev/null || true)"
  symbol_locator="$(case_manifest_get "$prompt_dir" symbol.locator 2>/dev/null || true)"
  proof_target="$(case_manifest_get "$prompt_dir" proof.targetObjectPath 2>/dev/null || true)"
  proof_source="$(case_manifest_get "$prompt_dir" proof.source 2>/dev/null || true)"
  proof_comparator="$(case_manifest_get "$prompt_dir" proof.comparator 2>/dev/null || true)"
  workspace_prompt="$(case_manifest_get "$prompt_dir" workspace.promptPath 2>/dev/null || true)"
  workspace_build="$(case_manifest_get "$prompt_dir" workspace.buildDir 2>/dev/null || true)"

  [[ "$schema_version" == "1" ]] || { log "invalid: prompts/$prompt_name case.yaml schemaVersion must be 1"; errors=1; }
  [[ "$case_id" == "$prompt_name" ]] || { log "invalid: prompts/$prompt_name caseId must match prompt directory"; errors=1; }
  [[ -n "$adapter_id" ]] || { log "invalid: prompts/$prompt_name case.yaml missing adapter.id"; errors=1; }
  [[ -n "$adapter_profile" ]] || { log "invalid: prompts/$prompt_name case.yaml missing adapter.capabilitiesProfile"; errors=1; }
  if [[ -n "$adapter_id" ]] && ! target_adapter_is_supported "$adapter_id"; then
    log "invalid: prompts/$prompt_name case.yaml adapter.id is unsupported: $adapter_id"
    errors=1
  fi
  if [[ -n "$adapter_id" && -n "$adapter_profile" ]] && target_adapter_is_supported "$adapter_id"; then
    expected_profile="$(target_adapter_capabilities_profile "$adapter_id" 2>/dev/null || true)"
    [[ "$adapter_profile" == "$expected_profile" ]] || {
      log "invalid: prompts/$prompt_name case.yaml adapter.capabilitiesProfile must match adapter registry"
      errors=1
    }
  fi
  [[ -n "$ingest_source_type" ]] || { log "invalid: prompts/$prompt_name case.yaml missing ingest.sourceType"; errors=1; }
  [[ -n "$ingest_source_path" ]] || { log "invalid: prompts/$prompt_name case.yaml missing ingest.sourcePath"; errors=1; }
  [[ -n "$ingest_provenance" ]] || { log "invalid: prompts/$prompt_name case.yaml missing ingest.provenance"; errors=1; }
  [[ -n "$target_family" ]] || { log "invalid: prompts/$prompt_name case.yaml missing target.family"; errors=1; }
  if [[ -n "$adapter_id" && -n "$target_family" ]] && target_adapter_is_supported "$adapter_id"; then
    expected_family="$(target_adapter_expected_family "$adapter_id" 2>/dev/null || true)"
    [[ "$target_family" == "$expected_family" ]] || {
      log "invalid: prompts/$prompt_name case.yaml target.family must match adapter registry"
      errors=1
    }
  fi
  [[ -n "$target_binary" ]] || { log "invalid: prompts/$prompt_name case.yaml missing target.binary"; errors=1; }
  [[ -n "$target_platform" ]] || { log "invalid: prompts/$prompt_name case.yaml missing target.platform"; errors=1; }
  [[ -n "$load_tool" ]] || { log "invalid: prompts/$prompt_name case.yaml missing load.tool"; errors=1; }
  if [[ -n "$adapter_id" && -n "$load_tool" ]] && target_adapter_is_supported "$adapter_id"; then
    expected_load_tool="$(target_adapter_default_load_tool "$adapter_id" 2>/dev/null || true)"
    [[ "$load_tool" == "$expected_load_tool" ]] || {
      log "invalid: prompts/$prompt_name case.yaml load.tool must match adapter registry"
      errors=1
    }
  fi
  [[ -n "$load_program_path" ]] || { log "invalid: prompts/$prompt_name case.yaml missing load.programPath"; errors=1; }
  [[ -n "$load_context_path" ]] || { log "invalid: prompts/$prompt_name case.yaml missing load.contextPath"; errors=1; }
  [[ -n "$symbol_name" ]] || { log "invalid: prompts/$prompt_name case.yaml missing symbol.name"; errors=1; }
  [[ -n "$symbol_locator" ]] || { log "invalid: prompts/$prompt_name case.yaml missing symbol.locator"; errors=1; }
  [[ -n "$proof_target" ]] || { log "invalid: prompts/$prompt_name case.yaml missing proof.targetObjectPath"; errors=1; }
  [[ -n "$proof_source" ]] || { log "invalid: prompts/$prompt_name case.yaml missing proof.source"; errors=1; }
  [[ -n "$proof_comparator" ]] || { log "invalid: prompts/$prompt_name case.yaml missing proof.comparator"; errors=1; }
  [[ "$workspace_prompt" == "prompts/$prompt_name" ]] || { log "invalid: prompts/$prompt_name workspace.promptPath must be prompts/$prompt_name"; errors=1; }
  [[ "$workspace_build" == "build" ]] || { log "invalid: prompts/$prompt_name workspace.buildDir must be build"; errors=1; }

  if [[ -f "$prompt_dir/settings.yaml" ]]; then
    settings_symbol="$(prompt_settings_get "$prompt_dir" functionName 2>/dev/null || true)"
    settings_target="$(prompt_settings_get "$prompt_dir" targetObjectPath 2>/dev/null || true)"

    [[ "$symbol_name" == "$settings_symbol" ]] || {
      log "invalid: prompts/$prompt_name case.yaml symbol.name must match settings.yaml functionName"
      errors=1
    }
    [[ "$proof_target" == "$settings_target" ]] || {
      log "invalid: prompts/$prompt_name case.yaml proof.targetObjectPath must match settings.yaml targetObjectPath"
      errors=1
    }
  fi
done

if [[ "$errors" -ne 0 ]]; then
  exit 1
fi

echo "CASE_MANIFESTS_OK"
