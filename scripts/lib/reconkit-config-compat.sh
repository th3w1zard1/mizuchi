#!/usr/bin/env bash
# Read reconkit.yaml / reconkit.example.yaml and expand {{template}} variables.
# Source after setting ROOT to the ReconstructKit workspace root.
#
#   . "$ROOT/scripts/lib/reconkit-config.sh"
#   reconkit_config_resolve
#   reconkit_config_get "global.compilerScript"

reconkit_config_resolve() {
  if [[ -f "${ROOT}/reconkit.yaml" ]]; then
    RECONKIT_CONFIG_PATH="${ROOT}/reconkit.yaml"
  elif [[ -f "${ROOT}/reconkit.example.yaml" ]]; then
    RECONKIT_CONFIG_PATH="${ROOT}/reconkit.example.yaml"
  else
    echo "reconkit-config: no reconkit.yaml or reconkit.example.yaml under $ROOT" >&2
    return 1
  fi
}

# Dot-path lookup; prints value or empty; exit 1 if missing (unless optional).
# Usage: reconkit_config_get global.compilerScript
#        reconkit_config_get plugins.m2c.enable optional
reconkit_config_get() {
  local path="${1:-}" optional="${2:-}"
  reconkit_config_resolve || return $?

  local out status=0
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

# Replace {{name}} placeholders in a string (stdin or arg).
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

# Map global.target to m2c --target arch (empty = unsupported).
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

# Compatibility alias layer kept for migration paths.
recovery_config_resolve() { reconkit_config_resolve "$@"; }
recovery_config_get() { reconkit_config_get "$@"; }
recovery_expand_templates() { reconkit_expand_templates "$@"; }
recovery_m2c_target_for_platform() { reconkit_m2c_target_for_platform "$@"; }
