#!/usr/bin/env bash
# Validate ReconstructKit prompt folder: settings.yaml has exactly functionName, targetObjectPath, asm.
set -euo pipefail

dir="${1:-}"
if [[ -z "$dir" || ! -d "$dir" ]]; then
  echo "usage: $0 <prompts/<name>/>" >&2
  exit 2
fi

settings="$dir/settings.yaml"
prompt="$dir/prompt.md"

for f in "$settings" "$prompt"; do
  if [[ ! -f "$f" ]]; then
    echo "missing: $f" >&2
    exit 1
  fi
done

validate_with_ruby() {
  ruby -ryaml - "$settings" <<'RUBY'
path = ARGV[0]
data = YAML.load_file(path)
unless data.is_a?(Hash)
  warn "settings.yaml must be a mapping"
  exit 1
end
allowed = %w[functionName targetObjectPath asm]
keys = data.keys.map(&:to_s)
extra = keys - allowed
missing = allowed - keys
unless extra.empty?
  warn "unexpected keys (ReconstructKit allows only 3): #{extra.sort.join(', ')}"
  exit 1
end
unless missing.empty?
  warn "missing keys: #{missing.sort.join(', ')}"
  exit 1
end
allowed.each do |k|
  v = data[k]
  unless v.is_a?(String) && !v.strip.empty?
    warn "#{k} must be a non-empty string"
    exit 1
  end
end
if data['asm'].length < 10
  warn "asm looks too short — paste full target assembly"
  exit 1
end
puts "OK: #{path}"
RUBY
}

validate_with_python() {
  python3 - "$settings" <<'PY'
import sys
try:
    import yaml
except ImportError:
    sys.exit(2)

path = sys.argv[1]
with open(path) as f:
    data = yaml.safe_load(f)

if not isinstance(data, dict):
    print("settings.yaml must be a mapping", file=sys.stderr)
    sys.exit(1)

allowed = {"functionName", "targetObjectPath", "asm"}
keys = set(data.keys())
extra = keys - allowed
missing = allowed - keys

if extra:
    print(f"unexpected keys (ReconstructKit allows only 3): {sorted(extra)}", file=sys.stderr)
    sys.exit(1)
if missing:
    print(f"missing keys: {sorted(missing)}", file=sys.stderr)
    sys.exit(1)

for k in allowed:
    v = data[k]
    if not isinstance(v, str) or not v.strip():
        print(f"{k} must be a non-empty string", file=sys.stderr)
        sys.exit(1)

if len(data.get("asm", "")) < 10:
    print("asm looks too short — paste full target assembly", file=sys.stderr)
    sys.exit(1)

print("OK:", path)
PY
}

if command -v ruby >/dev/null 2>&1; then
  validate_with_ruby
elif validate_with_python; then
  :
else
  code=$?
  if [[ "$code" -eq 2 ]]; then
    echo "install PyYAML: pip install pyyaml (or install ruby for built-in YAML)" >&2
  fi
  exit "$code"
fi
