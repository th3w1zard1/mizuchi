#!/usr/bin/env bash
# Guide phase 3 — AI-powered matching loop (Claude Runner).
#
# The Macabeus/Mizuchi article runs, after the programmatic phase fails:
#   Claude Runner -> compile -> objdiff  (decomp-permuter in background),
# iterating until objdiff reports 0 differences.
#
# This script wires that phase into the CLI. The real Claude loop lives in the
# upstream `mizuchi run` runner (bundled in docker.io/bolabaden/mizuchi). We
# prefer it; if unavailable we fall back to the Cursor-native sandbox tool
# (`compile-and-view-assembly.sh`) so a human/agent can drive the same loop.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/case-metadata.sh
. "$ROOT/scripts/lib/case-metadata.sh"
PROMPT_DIR=""
CONFIG="${MIZUCHI_CONFIG:-$ROOT/mizuchi.yaml}"
IMAGE="${MIZUCHI_IMAGE:-docker.io/bolabaden/mizuchi:latest}"
REPORT_JSON=""
PROMPT_NAME=""

usage() {
  cat <<'EOF'
Usage: run-ai-phase.sh --prompt <prompt-dir> [--config <mizuchi.yaml>]

Runs the guide's AI matching loop (Claude -> compile -> objdiff) for one prompt
folder. Requires ANTHROPIC_API_KEY. Exit 0 only on objdiff 0.

Runner resolution (first available):
  1. `mizuchi` on PATH           -> mizuchi run --config <cfg>
  2. docker/podman + $MIZUCHI_IMAGE -> containerized mizuchi run
  3. MIZUCHI_MATCHER_COMMAND     -> one-shot matcher.sh + build-and-verify
  4. fallback: print Cursor-native loop instructions (compile-and-view-assembly.sh)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) PROMPT_DIR="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -z "$PROMPT_DIR" ]] && { echo "Missing --prompt" >&2; usage; exit 2; }
PROMPT_DIR="$(cd "$PROMPT_DIR" && pwd)"
PROMPT_NAME="$(basename "$PROMPT_DIR")"
REPORT_JSON="$PROMPT_DIR/build/ai-phase.json"
mkdir -p "$PROMPT_DIR/build"

write_report() {
  local status="$1" exit_code="$2" runner="${3:-}" reason="${4:-}"
  jq -n \
    --arg schema "mizuchi.ai-phase.v1" \
    --arg status "$status" \
    --arg prompt "$PROMPT_NAME" \
    --arg prompt_dir "$PROMPT_DIR" \
    --arg runner "$runner" \
    --arg reason "$reason" \
    --arg config "$CONFIG" \
    --arg image "$IMAGE" \
    --arg anthropic_api_key_present "${ANTHROPIC_API_KEY:+true}" \
    --arg exit_code "$exit_code" \
    '{
      schema: $schema,
      status: $status,
      prompt: $prompt,
      promptDir: $prompt_dir,
      runner: (if $runner == "" then null else $runner end),
      reason: (if $reason == "" then null else $reason end),
      config: $config,
      image: $image,
      anthropicApiKeyPresent: ($anthropic_api_key_present == "true"),
      exitCode: (if $exit_code == "" then null else ($exit_code | tonumber) end)
    }' >"$REPORT_JSON"
}

case_status="$(case_metadata_get_default "$PROMPT_DIR" status "")"
if [[ "$case_status" == "blocked" ]]; then
  blocked_reason="$(case_metadata_get_default "$PROMPT_DIR" blockedReason "case.yaml status is blocked")"
  write_report "blocked" 3 "" "$blocked_reason"
  echo "run-ai-phase: prompt is blocked: $blocked_reason" >&2
  exit 3
fi

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "WARN: ANTHROPIC_API_KEY not set — the AI phase needs it." >&2
fi

# 1. native mizuchi
if command -v mizuchi >/dev/null 2>&1; then
  echo "==> AI phase via native mizuchi run" >&2
  write_report "started" "" "native-mizuchi" "mizuchi run started"
  set +e
  mizuchi run --config "$CONFIG"
  rc=$?
  set -e
  if [[ "$rc" -eq 0 ]]; then
    write_report "matched" 0 "native-mizuchi" "mizuchi run completed with objdiff 0"
  else
    write_report "failed" "$rc" "native-mizuchi" "mizuchi run failed"
  fi
  exit "$rc"
fi

# 2. containerized mizuchi (the image we build/publish)
for engine in docker podman; do
  if command -v "$engine" >/dev/null 2>&1 && "$engine" image exists "$IMAGE" 2>/dev/null || \
     { command -v "$engine" >/dev/null 2>&1 && "$engine" image inspect "$IMAGE" >/dev/null 2>&1; }; then
    echo "==> AI phase via $engine image $IMAGE" >&2
    write_report "started" "" "$engine" "containerized mizuchi run started"
    set +e
    "$engine" run --rm \
      -e ANTHROPIC_API_KEY \
      -v "$ROOT:/work" -w /work \
      "$IMAGE" run --config "${CONFIG/#$ROOT/\/work}"
    rc=$?
    set -e
    if [[ "$rc" -eq 0 ]]; then
      write_report "matched" 0 "$engine" "containerized mizuchi run completed with objdiff 0"
    else
      write_report "failed" "$rc" "$engine" "containerized mizuchi run failed"
    fi
    exit "$rc"
  fi
done

# 3. one-shot matcher command fallback
if [[ -n "${MIZUCHI_MATCHER_COMMAND:-}" ]]; then
  echo "==> AI phase via one-shot matcher command" >&2
  write_report "started" "" "one-shot-matcher" "matcher command started"
  set +e
  "$ROOT/scripts/matcher.sh" --prompt "$PROMPT_DIR"
  matcher_rc=$?
  set -e
  if [[ "$matcher_rc" -ne 0 ]]; then
    write_report "failed" "$matcher_rc" "one-shot-matcher" "matcher command failed"
    exit "$matcher_rc"
  fi

  set +e
  "$ROOT/scripts/build-and-verify.sh" --prompt "$PROMPT_DIR" --candidate "$PROMPT_DIR/trial.c"
  verify_rc=$?
  set -e
  if [[ "$verify_rc" -eq 0 ]]; then
    write_report "matched" 0 "one-shot-matcher" "matcher trial.c completed with objdiff 0"
  else
    write_report "manual-required" 3 "one-shot-matcher" "matcher trial.c did not match"
    exit 3
  fi
  exit 0
fi

# 4. fallback: Cursor-native loop
cat >&2 <<EOF
==> No mizuchi runner found; use the Cursor-native AI loop:

  ./scripts/compile-and-view-assembly.sh --prompt "$PROMPT_DIR" --code-file trial.c

Iterate: write trial.c, run the tool, read the objdiff delta, repeat until
0 differences, then: ./scripts/decomp-cli.sh decomp-integrate <name> <target.o>

To enable the automated loop, set ANTHROPIC_API_KEY and either install mizuchi
or build the image (docker build -t $IMAGE .).
EOF
write_report "manual-required" 3 "cursor-native" "no mizuchi runner found"
exit 3
