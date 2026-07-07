#!/usr/bin/env bash
# Shared helpers to read ReconstructKit prompt folders (settings.yaml).
# Source from other scripts: . "$(dirname "$0")/lib/prompt-settings.sh"

prompt_settings_require_dir() {
  local dir="${1:-}"
  if [[ -z "$dir" || ! -d "$dir" ]]; then
    echo "prompt-settings: not a directory: $dir" >&2
    return 2
  fi
  if [[ ! -f "$dir/settings.yaml" ]]; then
    echo "prompt-settings: missing settings.yaml in $dir" >&2
    return 1
  fi
}

# Usage: prompt_settings_get <prompt_dir> <field>
# Prints field value to stdout; exit 1 on missing/invalid.
prompt_settings_get() {
  local dir="$1" field="$2"
  prompt_settings_require_dir "$dir" || return $?

  if command -v ruby >/dev/null 2>&1; then
    ruby -ryaml - "$dir/settings.yaml" "$field" <<'RUBY'
path, field = ARGV
data = YAML.load_file(path)
unless data.is_a?(Hash) && data[field].is_a?(String) && !data[field].strip.empty?
  warn "prompt-settings: invalid or missing #{field} in #{path}"
  exit 1
end
print data[field]
RUBY
    return $?
  fi

  python3 - "$dir/settings.yaml" "$field" <<'PY'
import sys
try:
    import yaml
except ImportError:
    print("prompt-settings: install ruby or PyYAML", file=sys.stderr)
    sys.exit(2)

path, field = sys.argv[1], sys.argv[2]
with open(path) as f:
    data = yaml.safe_load(f)
if not isinstance(data, dict):
    print("prompt-settings: settings.yaml must be a mapping", file=sys.stderr)
    sys.exit(1)
val = data.get(field)
if not isinstance(val, str) or not val.strip():
    print(f"prompt-settings: invalid or missing {field}", file=sys.stderr)
    sys.exit(1)
print(val, end="")
PY
}
