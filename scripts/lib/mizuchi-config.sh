#!/usr/bin/env bash
# Read mizuchi.yaml / mizuchi.example.yaml and expand {{template}} variables.
# Source after setting ROOT to the Mizuchi workspace root.
#
#   . "$ROOT/scripts/lib/mizuchi-config.sh"
#   mizuchi_config_resolve
#   mizuchi_config_get "global.compilerScript"

mizuchi_config_resolve() {
  if [[ -f "${ROOT}/mizuchi.yaml" ]]; then
    MIZUCHI_CONFIG_PATH="${ROOT}/mizuchi.yaml"
  elif [[ -f "${ROOT}/mizuchi.example.yaml" ]]; then
    MIZUCHI_CONFIG_PATH="${ROOT}/mizuchi.example.yaml"
  else
    echo "mizuchi-config: no mizuchi.yaml or mizuchi.example.yaml under $ROOT" >&2
    return 1
  fi
}

# Dot-path lookup; prints value or empty; exit 1 if missing (unless optional).
# Usage: mizuchi_config_get global.compilerScript
#        mizuchi_config_get plugins.m2c.enable optional
mizuchi_config_get() {
  local path="${1:-}" optional="${2:-}"
  mizuchi_config_resolve || return $?

  local out status=0
  if command -v ruby >/dev/null 2>&1; then
    out="$(ruby -ryaml - "$MIZUCHI_CONFIG_PATH" "$path" "$optional" <<'RUBY'
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
    out="$(python3 - "$MIZUCHI_CONFIG_PATH" "$path" "$optional" <<'PY'
import sys
try:
    import yaml
except ImportError:
    print("mizuchi-config: install ruby or PyYAML", file=sys.stderr)
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
    echo "mizuchi-config: missing key $path in $MIZUCHI_CONFIG_PATH" >&2
    return 1
  fi
  printf '%s' "$out"
}

# Replace {{name}} placeholders in a string (stdin or arg).
mizuchi_expand_templates() {
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
mizuchi_m2c_target_for_platform() {
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
