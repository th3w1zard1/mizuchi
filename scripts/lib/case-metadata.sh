#!/usr/bin/env bash
# Helpers for optional prompt case.yaml metadata.

case_metadata_file() {
  local prompt_dir="$1"
  printf '%s/case.yaml' "$prompt_dir"
}

case_metadata_has_file() {
  local prompt_dir="$1"
  [[ -f "$(case_metadata_file "$prompt_dir")" ]]
}

case_metadata_get() {
  local prompt_dir="$1" field="$2"
  local case_file
  case_file="$(case_metadata_file "$prompt_dir")"

  if [[ ! -f "$case_file" ]]; then
    return 1
  fi

  ruby -ryaml -rtime - "$case_file" "$field" <<'RUBY'
path, field = ARGV
data = YAML.load_file(path, permitted_classes: [Time])
unless data.is_a?(Hash)
  warn "case.yaml must be a mapping"
  exit 1
end
value = data[field]
exit 1 if value.nil?
if value.is_a?(Time)
  print value.utc.iso8601
  exit 0
end
unless value.is_a?(String) || value.is_a?(Integer) || value.is_a?(Float) || value == true || value == false
  warn "case.yaml field #{field} must be scalar"
  exit 1
end
print value.to_s
RUBY
}

case_metadata_get_default() {
  local prompt_dir="$1" field="$2" default_value="$3"
  case_metadata_get "$prompt_dir" "$field" 2>/dev/null || printf '%s' "$default_value"
}

case_metadata_expand() {
  local value="$1" function_name="$2" prompt_name="$3"
  value="${value//\{\{functionName\}\}/$function_name}"
  value="${value//\{\{promptName\}\}/$prompt_name}"
  printf '%s' "$value"
}

case_metadata_resolve_path() {
  local root="$1" prompt_dir="$2" raw_path="$3"
  raw_path="${raw_path/#\~/$HOME}"
  if [[ "$raw_path" = /* ]]; then
    printf '%s' "$raw_path"
  elif [[ "$raw_path" == prompt:/* ]]; then
    printf '%s/%s' "$prompt_dir" "${raw_path#prompt:/}"
  else
    printf '%s/%s' "$root" "$raw_path"
  fi
}
