#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: audit-plugin-readiness.sh [--plugin-root PATH] [--quiet]

Audits a Cursor plugin directory for marketplace submission readiness.
Prints PLUGIN_READINESS_OK on success. Verbose logging is the default.
EOF
  exit 2
}

plugin_root=""
quiet=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --plugin-root)
      shift
      [[ $# -gt 0 ]] || usage
      plugin_root="$1"
      shift
      ;;
    --quiet)
      quiet=1
      shift
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "unexpected argument: $1" >&2
      usage
      ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/check-log.sh
source "$ROOT/scripts/lib/check-log.sh"

CHECK_LOG_QUIET=$quiet
check_log_init "audit-plugin-readiness"

if [[ -z "$plugin_root" ]]; then
  if [[ -n "${PLUGIN_ROOT:-}" ]]; then
    plugin_root="$PLUGIN_ROOT"
    check_log_trace "plugin-root from PLUGIN_ROOT=${plugin_root}"
  elif [[ -d "${HOME}/.cursor/plugins/local/matching-decompilation-re" ]]; then
    plugin_root="${HOME}/.cursor/plugins/local/matching-decompilation-re"
    check_log_trace "plugin-root default local install=${plugin_root}"
  else
    check_log_fail "provide --plugin-root or set PLUGIN_ROOT"
    check_log_summary "PLUGIN_READINESS_FAIL"
    exit 2
  fi
else
  check_log_trace "plugin-root explicit=${plugin_root}"
fi

if [[ ! -d "$plugin_root" ]]; then
  check_log_fail "plugin root not found: $plugin_root"
  check_log_summary "PLUGIN_READINESS_FAIL"
  exit 1
fi

plugin_root="$(cd "$plugin_root" && pwd)"
check_log_trace "plugin-root resolved=${plugin_root}"

failures=0
record_fail() { failures=1; }

require_file() {
  local rel="$1"
  if [[ -f "$plugin_root/$rel" ]]; then
    check_log_read_file "$plugin_root/$rel" "$rel" "required doc" || record_fail
  else
    check_log_fail "missing file: $rel"
    record_fail
  fi
}

require_frontmatter_pair() {
  local file="$1"
  local label="$2"
  local rel="${file#$plugin_root/}"
  if [[ ! -f "$file" ]]; then
    check_log_fail "missing $label: $rel"
    record_fail
    return
  fi
  check_log_read_file "$file" "$rel" "$label"
  if ! grep -q '^name:' "$file"; then
    check_log_fail "${rel}: missing frontmatter name"
    record_fail
  else
    check_log_pass "${rel} frontmatter name"
  fi
  if ! grep -q '^description:' "$file"; then
    check_log_fail "${rel}: missing frontmatter description"
    record_fail
  else
    check_log_pass "${rel} frontmatter description"
  fi
}

require_rule_frontmatter() {
  local file="$1"
  local rel="${file#$plugin_root/}"
  if [[ ! -f "$file" ]]; then
    check_log_fail "missing rule: $rel"
    record_fail
    return
  fi
  check_log_read_file "$file" "$rel" "rule"
  if ! grep -q '^description:' "$file"; then
    check_log_fail "${rel}: missing frontmatter description"
    record_fail
  else
    check_log_pass "${rel} rule description"
  fi
}

manifest="$plugin_root/.cursor-plugin/plugin.json"
if [[ ! -f "$manifest" ]]; then
  check_log_fail "missing .cursor-plugin/plugin.json"
  record_fail
else
  check_log_read_file "$manifest" ".cursor-plugin/plugin.json" "manifest"
  if ! python3 - "$manifest" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)
required = ("name", "version", "description")
missing = [k for k in required if not data.get(k)]
if missing:
    print("manifest missing keys: " + ", ".join(missing))
    sys.exit(1)
name = data["name"]
if not isinstance(name, str) or not name.replace("-", "").isalnum() or name != name.lower():
    print(f"manifest name must be lowercase kebab-case: {name!r}")
    sys.exit(1)
PY
  then
    check_log_fail "invalid manifest JSON or metadata in .cursor-plugin/plugin.json"
    record_fail
  else
    check_log_pass "manifest JSON and metadata"
  fi
fi

for doc in README.md LICENSE CHANGELOG.md; do
  require_file "$doc"
done

if [[ -d "$plugin_root/skills" ]]; then
  shopt -s nullglob
  for skill_dir in "$plugin_root/skills"/*/; do
    skill_file="${skill_dir}SKILL.md"
    require_frontmatter_pair "$skill_file" "skill"
  done
  shopt -u nullglob
fi

if [[ -d "$plugin_root/commands" ]]; then
  shopt -s nullglob
  for cmd in "$plugin_root/commands"/*.{md,txt}; do
    require_frontmatter_pair "$cmd" "command"
  done
  shopt -u nullglob
fi

if [[ -d "$plugin_root/agents" ]]; then
  shopt -s nullglob
  for agent in "$plugin_root/agents"/*.md; do
    require_frontmatter_pair "$agent" "agent"
  done
  shopt -u nullglob
fi

if [[ -d "$plugin_root/rules" ]]; then
  shopt -s nullglob
  for rule in "$plugin_root/rules"/*.{mdc,md}; do
    require_rule_frontmatter "$rule"
  done
  shopt -u nullglob
fi

hooks_json="$plugin_root/hooks/hooks.json"
if [[ ! -f "$hooks_json" ]]; then
  check_log_fail "missing hooks/hooks.json"
  record_fail
else
  check_log_read_file "$hooks_json" "hooks/hooks.json" "hooks manifest"
  if ! python3 - "$hooks_json" "$plugin_root" <<'PY'
import json, sys
from pathlib import Path
hooks_path = Path(sys.argv[1])
root = Path(sys.argv[2])
data = json.loads(hooks_path.read_text(encoding="utf-8"))
if "hooks" not in data:
    raise SystemExit("hooks.json missing hooks key")
for phase, entries in data["hooks"].items():
    if not isinstance(entries, list):
        raise SystemExit(f"hooks.{phase} must be a list")
    for entry in entries:
        cmd = entry.get("command")
        if not cmd:
            raise SystemExit("hook entry missing command")
        hook_script = root / cmd
        if not hook_script.is_file():
            raise SystemExit(f"hook script not found: {cmd}")
PY
  then
    check_log_fail "invalid hooks/hooks.json or missing hook scripts"
    record_fail
  else
    check_log_pass "hooks/hooks.json and hook scripts"
  fi
fi

if [[ "$failures" -ne 0 ]]; then
  check_log_summary "PLUGIN_READINESS_FAIL"
  echo "PLUGIN_READINESS_FAIL count=$CHECK_LOG_FAILED" >&2
  exit 1
fi

check_log_summary "PLUGIN_READINESS_OK"
printf 'PLUGIN_READINESS_OK\n'
