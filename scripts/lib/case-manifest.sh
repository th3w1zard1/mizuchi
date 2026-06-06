#!/usr/bin/env bash
# Shared helpers to read prompt-local case manifests (case.yaml).

case_manifest_require_dir() {
  local dir="${1:-}"
  if [[ -z "$dir" || ! -d "$dir" ]]; then
    echo "case-manifest: not a directory: $dir" >&2
    return 2
  fi
  if [[ ! -f "$dir/case.yaml" ]]; then
    echo "case-manifest: missing case.yaml in $dir" >&2
    return 1
  fi
}

case_manifest_get() {
  local dir="$1" field_path="$2"
  case_manifest_require_dir "$dir" || return $?

  if command -v ruby >/dev/null 2>&1; then
    ruby -ryaml - "$dir/case.yaml" "$field_path" <<'RUBY'
path, field_path = ARGV
data = YAML.load_file(path)
unless data.is_a?(Hash)
  warn "case-manifest: case.yaml must be a mapping in #{path}"
  exit 1
end

value = field_path.split(".").reduce(data) do |acc, key|
  acc.is_a?(Hash) ? acc[key] : nil
end

case value
when String, Integer
  print value
else
  warn "case-manifest: invalid or missing #{field_path} in #{path}"
  exit 1
end
RUBY
    return $?
  fi

  python3 - "$dir/case.yaml" "$field_path" <<'PY'
import sys
try:
    import yaml
except ImportError:
    print("case-manifest: install ruby or PyYAML", file=sys.stderr)
    sys.exit(2)

path, field_path = sys.argv[1], sys.argv[2]
with open(path) as f:
    data = yaml.safe_load(f)

if not isinstance(data, dict):
    print(f"case-manifest: case.yaml must be a mapping in {path}", file=sys.stderr)
    sys.exit(1)

value = data
for part in field_path.split("."):
    if not isinstance(value, dict):
        value = None
        break
    value = value.get(part)

if not isinstance(value, (str, int)):
    print(f"case-manifest: invalid or missing {field_path} in {path}", file=sys.stderr)
    sys.exit(1)

print(value, end="")
PY
}
