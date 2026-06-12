#!/usr/bin/env bash
# Shared registry for supported target adapters and their runtime defaults.

target_adapters_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/case-manifest.sh
source "$target_adapters_lib_dir/case-manifest.sh"

target_adapter_ids() {
  printf '%s\n' \
    "odyssey" \
    "elf-ps2"
}

target_adapter_is_supported() {
  local adapter_id="${1:-}"
  case "$adapter_id" in
    odyssey|elf-ps2) return 0 ;;
    *) return 1 ;;
  esac
}

target_adapter_expected_family() {
  local adapter_id="${1:-}"
  case "$adapter_id" in
    odyssey) printf 'odyssey\n' ;;
    elf-ps2) printf 'elf-ps2\n' ;;
    *) return 1 ;;
  esac
}

target_adapter_default_load_tool() {
  local adapter_id="${1:-}"
  case "$adapter_id" in
    odyssey) printf 'agdec-http\n' ;;
    elf-ps2) printf 'ghidra\n' ;;
    *) return 1 ;;
  esac
}

target_adapter_default_context_path() {
  local adapter_id="${1:-}"
  case "$adapter_id" in
    odyssey|elf-ps2) printf 'context/ctx.h\n' ;;
    *) return 1 ;;
  esac
}

target_adapter_capabilities_profile() {
  local adapter_id="${1:-}"
  case "$adapter_id" in
    odyssey) printf 'ghidra-mizuchi-v1\n' ;;
    elf-ps2) printf 'ghidra-ps2-v1\n' ;;
    *) return 1 ;;
  esac
}

target_adapter_resolve_path() {
  local root="$1"
  local raw_path="${2:-}"

  if [[ -z "$raw_path" ]]; then
    return 1
  fi

  if [[ "$raw_path" == /* ]]; then
    printf '%s\n' "$raw_path"
    return 0
  fi

  printf '%s/%s\n' "$root" "$raw_path"
}

target_adapter_case_context_path() {
  local prompt_dir="$1"
  local adapter_id="${2:-}"
  local context_path=""

  context_path="$(case_manifest_get "$prompt_dir" load.contextPath 2>/dev/null || true)"
  if [[ -n "$context_path" ]]; then
    printf '%s\n' "$context_path"
    return 0
  fi

  [[ -n "$adapter_id" ]] || return 1
  target_adapter_default_context_path "$adapter_id"
}

target_adapter_case_context_path_abs() {
  local root="$1"
  local prompt_dir="$2"
  local adapter_id="${3:-}"
  local context_path=""

  context_path="$(target_adapter_case_context_path "$prompt_dir" "$adapter_id" 2>/dev/null || true)"
  [[ -n "$context_path" ]] || return 1
  target_adapter_resolve_path "$root" "$context_path"
}

target_adapter_case_load_tool() {
  local prompt_dir="$1"
  local adapter_id="${2:-}"
  local load_tool=""

  load_tool="$(case_manifest_get "$prompt_dir" load.tool 2>/dev/null || true)"
  if [[ -n "$load_tool" ]]; then
    printf '%s\n' "$load_tool"
    return 0
  fi

  [[ -n "$adapter_id" ]] || return 1
  target_adapter_default_load_tool "$adapter_id"
}
