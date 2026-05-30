#!/usr/bin/env bash
# Validate Mizuchi prompt folder: settings.yaml has exactly functionName, targetObjectPath, asm.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib/check-log.sh
source "$ROOT/scripts/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$ROOT/scripts/lib/guide-manifest.sh"
# shellcheck source=scripts/lib/cli-agent.sh
source "$ROOT/scripts/lib/cli-agent.sh"

usage() {
  cat <<EOF
Usage: validate-prompt-settings.sh <prompts/<name>/> [--quiet]

Checks settings.yaml has exactly functionName, targetObjectPath, asm (non-empty).

Examples:
  ./scripts/validate-prompt-settings.sh prompts/fun_00148020/
  ./scripts/validate-prompt-settings.sh prompts/fun_00148020/ --quiet
EOF
}

dir=""
quiet=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet) quiet=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      if [[ -z "$dir" ]]; then dir="$1"; shift
      else cli_agent_missing_arg "validate-prompt-settings.sh" "unexpected argument: $1" "./scripts/validate-prompt-settings.sh prompts/fun_00148020/"
      fi
      ;;
  esac
done

CHECK_LOG_QUIET=$quiet
check_log_init "validate-prompt-settings"
guide_manifest_load "$ROOT"

if [[ -z "$dir" || ! -d "$dir" ]]; then
  check_log_fail "missing or invalid prompt directory"
  check_log_summary "VALIDATE_PROMPT_SETTINGS_FAIL"
  cli_agent_missing_arg "validate-prompt-settings.sh" "missing prompt directory" "./scripts/validate-prompt-settings.sh prompts/fun_00148020/"
fi

dir="$(cd "$dir" && pwd)"
check_log_trace "prompt $(guide_manifest_rel "$ROOT" "$dir")"

settings="$dir/settings.yaml"
prompt="$dir/prompt.md"

for f in "$settings" "$prompt"; do
  check_log_read_file "$f" "$(guide_manifest_rel "$ROOT" "$f")" "prompt artifact" || {
    check_log_summary "VALIDATE_PROMPT_SETTINGS_FAIL"
    exit 1
  }
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
  warn "unexpected keys (Mizuchi allows only 3): #{extra.sort.join(', ')}"
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
  warn "asm looks too short — paste full GAS from Ghidra"
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
    print(f"unexpected keys (Mizuchi allows only 3): {sorted(extra)}", file=sys.stderr)
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
    print("asm looks too short — paste full GAS from Ghidra", file=sys.stderr)
    sys.exit(1)

print("OK:", path)
PY
}

if command -v ruby >/dev/null 2>&1; then
  if validate_with_ruby; then
    check_log_pass "settings.yaml schema"
    check_log_summary "VALIDATE_PROMPT_SETTINGS_OK"
    echo "VALIDATE_PROMPT_SETTINGS_OK prompt=$(guide_manifest_rel "$ROOT" "$dir")"
    exit 0
  fi
  check_log_fail "settings.yaml validation failed"
  check_log_summary "VALIDATE_PROMPT_SETTINGS_FAIL"
  exit 1
elif validate_with_python; then
  check_log_pass "settings.yaml schema"
  check_log_summary "VALIDATE_PROMPT_SETTINGS_OK"
  echo "VALIDATE_PROMPT_SETTINGS_OK prompt=$(guide_manifest_rel "$ROOT" "$dir")"
  exit 0
else
  code=$?
  if [[ "$code" -eq 2 ]]; then
    check_log_fail "install PyYAML or ruby for YAML validation"
  else
    check_log_fail "settings.yaml validation failed"
  fi
  check_log_summary "VALIDATE_PROMPT_SETTINGS_FAIL"
  exit "$code"
fi
