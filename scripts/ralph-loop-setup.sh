#!/usr/bin/env bash
# Activate Mizuchi source-parity Ralph loop state for Cursor stop hook.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_FILE="${ROOT}/.cursor/ralph-loop.local.md"
mkdir -p "${ROOT}/.cursor"

MAX_ITERATIONS=0
COMPLETION_PROMISE="SOURCE_PARITY_LOOP_COMPLETE"
PROMPT_PARTS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-iterations)
      MAX_ITERATIONS="${2:-0}"
      shift 2
      ;;
    --completion-promise)
      COMPLETION_PROMISE="${2:-SOURCE_PARITY_LOOP_COMPLETE}"
      shift 2
      ;;
    *)
      PROMPT_PARTS+=("$1")
      shift
      ;;
  esac
done

PROMPT="${PROMPT_PARTS[*]:-Continue source-parity loop: SWKOTOR >=90% one-shot, JKA fully one-shottable, upstream Mizuchi core without gaps.}"

cat > "$STATE_FILE" <<EOF
---
active: true
iteration: 1
max_iterations: ${MAX_ITERATIONS}
completion_promise: "${COMPLETION_PROMISE}"
started_at: "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
---

${PROMPT}
EOF

chmod +x "${ROOT}/scripts/ralph-loop-verify.sh" "${ROOT}/scripts/ralph-loop-stop-hook.sh"
echo "Ralph loop armed at ${STATE_FILE}"
echo "Verify: ${ROOT}/scripts/ralph-loop-verify.sh"
echo "Promise (only when verify complete): <promise>${COMPLETION_PROMISE}</promise>"
