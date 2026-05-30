#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${MIZUCHI_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"

# shellcheck source=scripts/lib/check-log.sh
source "$SCRIPT_DIR/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$SCRIPT_DIR/lib/guide-manifest.sh"

quiet=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet) quiet=1; shift ;;
    -h|--help)
      cat <<EOF
usage: validate-guide-coverage.sh [--quiet]

Verifies AGENTS.md, knowledgebase layers, MCP servers, hooks, and CLI parity.
Verbose logging is the default; use --quiet for machine-only output.
EOF
      exit 0
      ;;
    *) echo "unexpected argument: $1" >&2; exit 2 ;;
  esac
done

CHECK_LOG_QUIET=$quiet
check_log_init "validate-guide-coverage"
check_log_trace "root  ${ROOT#${HOME}/}"

guide_manifest_load "$ROOT"

failures=0
record_fail() {
  failures=1
}

for file in "${GUIDE_REQUIRED_FILES[@]}"; do
  rel="${file#$ROOT/}"
  check_log_read_file "$file" "$rel" "guide artifact" || record_fail
done

for dir in "${GUIDE_KB_LAYERS[@]}"; do
  rel="${dir#$ROOT/}"
  check_log_read_dir "$dir" "$rel" "knowledgebase layer" || record_fail
done

check_log_grep_file "${ROOT}/.cursor/hooks.json" "$GUIDE_HOOK_PATTERN" "match-claim guard hook" || record_fail

mcp_file="${ROOT}/.cursor/mcp.json"
for server in "${GUIDE_MCP_SERVERS[@]}"; do
  check_log_mcp_server "$mcp_file" "$server" || record_fail
done

agents_file="${ROOT}/AGENTS.md"
for cmd in "${GUIDE_SLASH_COMMANDS[@]}"; do
  check_log_grep_file "$agents_file" "$cmd" "slash command $cmd" || record_fail
done

for link in "${GUIDE_AGENTS_LINKS[@]}"; do
  check_log_grep_file "$agents_file" "$link" "research source link" || record_fail
done

for invariant in "${GUIDE_INVARIANTS[@]}"; do
  check_log_grep_file "$agents_file" "$invariant" "invariant" || record_fail
done

cli_file="${ROOT}/scripts/decomp-cli.sh"
for cli_token in "${GUIDE_CLI_TOKENS[@]}"; do
  pattern="^[[:space:]]+${cli_token}([[:space:]]|$)"
  check_log_trace "grep  ${cli_file#$ROOT/} pattern=${cli_token} (decomp-cli usage)"
  if grep -qE "$pattern" "$cli_file"; then
    check_log_pass "decomp-cli token ${cli_token}"
  else
    check_log_fail "decomp-cli missing token: ${cli_token}"
    record_fail
  fi
done

if [[ "$failures" -ne 0 ]]; then
  check_log_summary "GUIDE_COVERAGE_FAIL"
  exit 1
fi

check_log_summary "GUIDE_COVERAGE_OK"
echo "GUIDE_COVERAGE_OK"
