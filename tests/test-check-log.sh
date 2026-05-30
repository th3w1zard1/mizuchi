#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/check-log.sh
source "$ROOT/scripts/lib/check-log.sh"

CHECK_LOG_QUIET=0
check_log_init "test-check-log"
check_log_read_file "$ROOT/AGENTS.md" "AGENTS.md" "fixture"
check_log_mcp_server "$ROOT/.cursor/mcp.json" "agdec-http"
check_log_file_op "prompts/example/prompt.md" "created"
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
