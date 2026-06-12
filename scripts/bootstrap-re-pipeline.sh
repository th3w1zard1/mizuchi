#!/usr/bin/env bash
# Bootstrap a Mizuchi prompt folder with required files for RE workflows.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=scripts/lib/check-log.sh
source "$ROOT/scripts/lib/check-log.sh"

usage() {
  cat <<'EOF'
usage: bootstrap-re-pipeline.sh [--quiet] --prompt <prompts/<name>/>

Creates case.yaml, prompt.md, and settings.yaml when missing, then validates the
prompt contract. Existing files are preserved. Verbose logging is the default.
EOF
}

PROMPT_DIR=""
quiet=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) PROMPT_DIR="$2"; shift 2 ;;
    --quiet) quiet=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

CHECK_LOG_QUIET=$quiet
check_log_init "bootstrap-re-pipeline"

if [[ -z "$PROMPT_DIR" ]]; then
  check_log_fail "missing --prompt"
  check_log_summary "RE_BOOTSTRAP_FAIL"
  usage
  exit 2
fi

check_log_trace "prompt-dir target=${PROMPT_DIR}"
mkdir -p "$PROMPT_DIR"
check_log_file_op "$PROMPT_DIR" "ensure-dir"

prompt_md="$PROMPT_DIR/prompt.md"
settings_yaml="$PROMPT_DIR/settings.yaml"
case_yaml="$PROMPT_DIR/case.yaml"
prompt_name="$(basename "$PROMPT_DIR")"

if [[ ! -f "$case_yaml" ]]; then
  cat >"$case_yaml" <<EOF
schemaVersion: 1
caseId: $prompt_name
adapter:
  id: replace_me
  capabilitiesProfile: replace_me
ingest:
  sourceType: replace_me
  sourcePath: replace_me
  provenance: replace_me
target:
  family: replace_me
  binary: replace_me
  platform: replace_me
load:
  tool: replace_me
  programPath: replace_me
  contextPath: replace_me
symbol:
  name: replace_me
  locator: replace_me
proof:
  targetObjectPath: path/to/replace_me.o
  source: replace_me
  comparator: replace_me
workspace:
  promptPath: prompts/$prompt_name
  buildDir: build
EOF
  check_log_file_op "${case_yaml#$ROOT/}" "created"
else
  check_log_file_op "${case_yaml#$ROOT/}" "preserved"
fi

if [[ ! -f "$prompt_md" ]]; then
  cat >"$prompt_md" <<'EOF'
# Decompiled Function Prompt

## Goal
Produce C code for the target function and iterate until objdiff reaches 0.

## Notes
- Run the programmatic phase first.
- Preserve behavior and ABI.
EOF
  check_log_file_op "${prompt_md#$ROOT/}" "created"
else
  check_log_file_op "${prompt_md#$ROOT/}" "preserved"
fi

if [[ ! -f "$settings_yaml" ]]; then
  cat >"$settings_yaml" <<'EOF'
functionName: replace_me
targetObjectPath: path/to/{{functionName}}.o
asm: |
  # Paste Ghidra/objdump assembly for the target function.
  nop
  nop
  nop
EOF
  check_log_file_op "${settings_yaml#$ROOT/}" "created"
else
  check_log_file_op "${settings_yaml#$ROOT/}" "preserved"
fi

check_log_trace "run   scripts/validate-prompt-settings.sh ${PROMPT_DIR}"
"$ROOT/scripts/validate-prompt-settings.sh" "$PROMPT_DIR"
check_log_pass "validate-prompt-settings.sh"

check_log_trace "run   scripts/validate-case-manifests.sh --quiet"
"$ROOT/scripts/validate-case-manifests.sh" --quiet >/dev/null
check_log_pass "validate-case-manifests.sh"

check_log_summary "RE_BOOTSTRAP_OK"
echo "RE_BOOTSTRAP_OK prompt=$PROMPT_DIR"
