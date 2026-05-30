#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/check-log.sh
source "$ROOT/scripts/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$ROOT/scripts/lib/guide-manifest.sh"

CHECK_LOG_QUIET=0
check_log_init "test-check-log"
check_log_read_file "$ROOT/AGENTS.md" "AGENTS.md" "fixture"
check_log_mcp_server "$ROOT/.cursor/mcp.json" "agdec-http"
check_log_file_op "prompts/example/prompt.md" "created"
check_log_file_appended "$ROOT/AGENTS.md" "$ROOT" "test append"
check_log_file_removed "$ROOT/tmp-removed-example" "$ROOT" "test remove"

summary_out="$(
  check_log_summary "TEST_OK" 2>&1
)"
[[ "$summary_out" == *"changes:"* ]] || {
  echo "expected changes block in summary, got: $summary_out" >&2
  exit 1
}
[[ "$summary_out" == *"created prompts/example/prompt.md"* ]] || {
  echo "expected created entry in changes, got: $summary_out" >&2
  exit 1
}
[[ "$summary_out" == *"appended AGENTS.md"* ]] || {
  echo "expected appended entry in changes, got: $summary_out" >&2
  exit 1
}
[[ "$summary_out" == *"removed tmp-removed-example"* ]] || {
  echo "expected removed entry in changes, got: $summary_out" >&2
  exit 1
}

guide_manifest_load "$ROOT"
defaults_trace="$(
  {
    CHECK_LOG_QUIET=0
    check_log_init "defaults-test"
    guide_manifest_trace_defaults "$ROOT"
  } 2>&1
)"
[[ "$defaults_trace" == *"mcp   server=agdec-http"* ]] || {
  echo "expected MCP server in defaults trace, got: $defaults_trace" >&2
  exit 1
}
[[ "$defaults_trace" == *"mcp   server=mizuchi"* ]] || {
  echo "expected mizuchi server in defaults trace, got: $defaults_trace" >&2
  exit 1
}
[[ "$defaults_trace" == *"dirs  prompts=prompts"* ]] || {
  echo "expected prompts dir in defaults trace, got: $defaults_trace" >&2
  exit 1
}

verbose_trace="$(
  {
    CHECK_LOG_QUIET=0
    check_log_init "verbose-test"
    check_log_trace "visible-line"
  } 2>&1
)"
[[ "$verbose_trace" == *"visible-line"* ]] || {
  echo "expected verbose trace, got: $verbose_trace" >&2
  exit 1
}

quiet_trace="$(
  {
    CHECK_LOG_QUIET=1
    check_log_init "quiet-test"
    check_log_trace "hidden-line"
  } 2>&1
)"
[[ "$quiet_trace" != *"hidden-line"* ]] || {
  echo "quiet mode should suppress trace, got: $quiet_trace" >&2
  exit 1
}

echo "test-check-log: PASS"
