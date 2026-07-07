#!/usr/bin/env bash
# ReconstructKit-style configuration helper.
#
# Usage:
#   . "$ROOT/scripts/lib/reconkit-config.sh"
#   reconkit_config_resolve
#   reconkit_config_get "global.compilerScript"
#   reconkit_expand_templates
set -euo pipefail

RECONKIT_CONFIG_PATH="${RECONKIT_CONFIG_PATH:-${RECONKIT_CONFIG_PATH:-}}"
RECONKIT_PROMPTS_DIR="${RECONKIT_PROMPTS_DIR:-${RECONKIT_PROMPTS_DIR:-}}"
RECONKIT_CONFIG="${RECONKIT_CONFIG:-}"
RECONKIT_IMAGE="${RECONKIT_IMAGE:-${RECONKIT_IMAGE:-docker.io/bolabaden/reconkit:latest}}"
RECONKIT_MATCHER_COMMAND="${RECONKIT_MATCHER_COMMAND:-${RECONKIT_MATCHER_COMMAND:-}}"
RECONKIT_ROOT="${RECONKIT_ROOT:-${RECONKIT_ROOT:-}}"
RECONKIT_WORKSPACE="${RECONKIT_WORKSPACE:-${RECONKIT_WORKSPACE:-}}"
RECONKIT_ARCHIVE_PATH="${RECONKIT_ARCHIVE_PATH:-${RECONKIT_ARCHIVE_PATH:-}}"
RECONKIT_BUNDLE_PATH="${RECONKIT_BUNDLE_PATH:-${RECONKIT_BUNDLE_PATH:-}}"
RECONKIT_VERIFY_TIMEOUT="${RECONKIT_VERIFY_TIMEOUT:-${RECONKIT_VERIFY_TIMEOUT:-60}}"

export RECONKIT_CONFIG_PATH RECONKIT_PROMPTS_DIR RECONKIT_CONFIG RECONKIT_IMAGE \
  RECONKIT_MATCHER_COMMAND RECONKIT_ROOT RECONKIT_WORKSPACE \
  RECONKIT_ARCHIVE_PATH RECONKIT_BUNDLE_PATH RECONKIT_VERIFY_TIMEOUT

: "${RECONKIT_CONFIG_PATH:=$RECONKIT_CONFIG_PATH}"
: "${RECONKIT_CONFIG:=$RECONKIT_CONFIG}"
: "${RECONKIT_IMAGE:=$RECONKIT_IMAGE}"
: "${RECONKIT_MATCHER_COMMAND:=$RECONKIT_MATCHER_COMMAND}"
: "${RECONKIT_ROOT:=$RECONKIT_ROOT}"
: "${RECONKIT_WORKSPACE:=$RECONKIT_WORKSPACE}"
: "${RECONKIT_ARCHIVE_PATH:=$RECONKIT_ARCHIVE_PATH}"
: "${RECONKIT_BUNDLE_PATH:=$RECONKIT_BUNDLE_PATH}"
: "${RECONKIT_PROMPTS_DIR:=$RECONKIT_PROMPTS_DIR}"
: "${RECONKIT_VERIFY_TIMEOUT:=$RECONKIT_VERIFY_TIMEOUT}"
export RECONKIT_CONFIG_PATH RECONKIT_CONFIG RECONKIT_IMAGE RECONKIT_MATCHER_COMMAND \
  RECONKIT_ROOT RECONKIT_WORKSPACE RECONKIT_ARCHIVE_PATH RECONKIT_BUNDLE_PATH \
  RECONKIT_PROMPTS_DIR RECONKIT_VERIFY_TIMEOUT

reconkit_default_prompts_dir() {
  local root="${1:-${ROOT:-$(pwd)}}"
  printf '%s' "${RECONKIT_PROMPTS_DIR:-${RECONKIT_PROMPTS_DIR:-$root/prompts}}"
}

reconkit_config_value() {
  local key="${1:-}"
  local fallback="${2:-}"
  local value=""
  if [[ -n "$key" ]]; then
    value="${!key-}"
  fi
  if [[ -n "$value" ]]; then
    printf '%s' "$value"
  elif [[ -n "$fallback" ]]; then
    printf '%s' "$fallback"
  fi
}

reconkit_image() {
  printf '%s' "${RECONKIT_IMAGE:-${RECONKIT_IMAGE:-docker.io/bolabaden/reconkit:latest}}"
}

reconkit_config_file_for_root() {
  local root="${1:-${ROOT:-$(pwd)}}"
  reconkit_config_value RECONKIT_CONFIG "$root/reconkit.yaml"
}

reconkit_matcher_command() {
  printf '%s' "${RECONKIT_MATCHER_COMMAND:-${RECONKIT_MATCHER_COMMAND:-}}"
}

reconkit_root_dir() {
  printf '%s' "${RECONKIT_ROOT:-${RECONKIT_ROOT:-}}"
}

reconkit_workspace_dir() {
  printf '%s' "${RECONKIT_WORKSPACE:-${RECONKIT_WORKSPACE:-}}"
}

reconkit_archive_path() {
  local default_path="${1:-}"
  printf '%s' "${RECONKIT_ARCHIVE_PATH:-${RECONKIT_ARCHIVE_PATH:-$default_path}}"
}

reconkit_bundle_path() {
  local default_path="${1:-}"
  printf '%s' "${RECONKIT_BUNDLE_PATH:-${RECONKIT_BUNDLE_PATH:-$default_path}}"
}

reconkit_verify_timeout() {
  printf '%s' "${RECONKIT_VERIFY_TIMEOUT:-${RECONKIT_VERIFY_TIMEOUT:-60}}"
}

reconkit_config_resolve() {
  local root="${1:-${ROOT:-$(pwd)}}"
  local candidate=""
  if [[ -n "${RECONKIT_CONFIG_PATH:-}" && -f "$RECONKIT_CONFIG_PATH" ]]; then
    candidate="$RECONKIT_CONFIG_PATH"
  elif [[ -f "$root/reconkit.yaml" ]]; then
    candidate="$root/reconkit.yaml"
  elif [[ -f "$root/reconkit.example.yaml" ]]; then
    candidate="$root/reconkit.example.yaml"
  else
    return 1
  fi

  RECONKIT_CONFIG_PATH="$candidate"
  RECONKIT_CONFIG_PATH="$candidate"
  export RECONKIT_CONFIG_PATH RECONKIT_CONFIG_PATH
}

reconkit_config_get() {
  local path="${1:-}"
  local optional="${2:-}"
  reconkit_config_resolve || return $?

  local out=""
  local status=0
  if command -v ruby >/dev/null 2>&1; then
    out="$(ruby -ryaml - "$RECONKIT_CONFIG_PATH" "$path" "$optional" <<'RUBY'
cfg_path, dot_path, optional = ARGV
cfg = YAML.load_file(cfg_path)
val = dot_path.split('.').reduce(cfg) { |m, k| m.is_a?(Hash) ? m[k] : nil }
if val.nil?
  exit(optional == 'optional' ? 0 : 1)
end
if val.is_a?(TrueClass)
  print 'true'
elsif val.is_a?(FalseClass)
  print 'false'
else
  print val.to_s
end
RUBY
)" || status=$?
  else
    out="$(python3 - "$RECONKIT_CONFIG_PATH" "$path" "$optional" <<'PY'
import sys
try:
    import yaml
except ImportError:
    print("reconkit-config: install ruby or PyYAML", file=sys.stderr)
    sys.exit(2)

cfg_path, dot_path, optional = sys.argv[1], sys.argv[2], sys.argv[3]
with open(cfg_path) as f:
    cfg = yaml.safe_load(f) or {}
node = cfg
for part in dot_path.split("."):
    if not isinstance(node, dict) or part not in node:
        sys.exit(0 if optional == "optional" else 1)
    node = node[part]
if isinstance(node, bool):
    print("true" if node else "false", end="")
else:
    print(node, end="")
PY
)" || status=$?
  fi

  if [[ $status -ne 0 && "$optional" != "optional" ]]; then
    echo "reconkit-config: missing key $path in $RECONKIT_CONFIG_PATH" >&2
    return 1
  fi
  printf '%s' "$out"
}

reconkit_expand_templates() {
  local text="${1:-}"
  if [[ -z "$text" && ! -t 0 ]]; then
    text="$(cat)"
  fi
  while [[ "$text" =~ \{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\} ]]; do
    local key="${BASH_REMATCH[1]}"
    local val="${!key-}"
    text="${text//\{\{$key\}\}/$val}"
  done
  printf '%s' "$text"
}

reconkit_m2c_target_for_platform() {
  local platform="${1:-}"
  case "$platform" in
    gba|nds|n3ds) echo "arm" ;;
    n64|ps1) echo "mips" ;;
    ps2|psp) echo "mipsel" ;;
    gc|wii) echo "ppc" ;;
    win32|x86|i386) echo "" ;;
    *) echo "" ;;
  esac
}

reconkit_config_resolve() { reconkit_config_resolve "$@"; }
reconkit_config_get() { reconkit_config_get "$@"; }
reconkit_expand_templates() { reconkit_expand_templates "$@"; }
reconkit_m2c_target_for_platform() { reconkit_m2c_target_for_platform "$@"; }

# Neutral API aliases for new edits and integrations.
recovery_config_resolve() { reconkit_config_resolve "$@"; }
recovery_config_get() { reconkit_config_get "$@"; }
recovery_expand_templates() { reconkit_expand_templates "$@"; }
recovery_m2c_target_for_platform() { reconkit_m2c_target_for_platform "$@"; }
