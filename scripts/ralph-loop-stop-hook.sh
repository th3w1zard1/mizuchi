#!/usr/bin/env bash
# Cursor stop hook: continue Ralph loop until verify passes and promise is emitted.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_FILE="${ROOT}/.cursor/ralph-loop.local.md"

input=$(cat)
response=$(echo "$input" | jq -r '.text // .response // .message // empty' 2>/dev/null || true)

if [[ ! -f "$STATE_FILE" ]]; then
  exit 0
fi

FRONTMATTER=$(sed -n '/^---$/,/^---$/{ /^---$/d; p; }' "$STATE_FILE")
ITERATION=$(echo "$FRONTMATTER" | grep '^iteration:' | sed 's/iteration: *//')
MAX_ITERATIONS=$(echo "$FRONTMATTER" | grep '^max_iterations:' | sed 's/max_iterations: *//')
COMPLETION_PROMISE=$(echo "$FRONTMATTER" | grep '^completion_promise:' | sed 's/completion_promise: *//' | sed 's/^"\(.*\)"$/\1/')

if [[ ! "$ITERATION" =~ ^[0-9]+$ ]] || [[ ! "$MAX_ITERATIONS" =~ ^[0-9]+$ ]]; then
  rm -f "$STATE_FILE"
  exit 0
fi

if [[ $MAX_ITERATIONS -gt 0 ]] && [[ $ITERATION -ge $MAX_ITERATIONS ]]; then
  rm -f "$STATE_FILE"
  exit 0
fi

VERIFY_JSON=$("$ROOT/scripts/ralph-loop-verify.sh" 2>/dev/null || true)
VERIFY_COMPLETE=$(echo "$VERIFY_JSON" | jq -r '.complete // false' 2>/dev/null || echo false)

if [[ "$COMPLETION_PROMISE" != "null" ]] && [[ -n "$COMPLETION_PROMISE" ]] && [[ -n "$response" ]]; then
  PROMISE_TEXT=$(echo "$response" | perl -0777 -pe 's/.*?<promise>(.*?)<\/promise>.*/$1/s; s/^\s+|\s+$//g; s/\s+/ /g' 2>/dev/null || echo "")
  if [[ "$VERIFY_COMPLETE" == "true" ]] && [[ -n "$PROMISE_TEXT" ]] && [[ "$PROMISE_TEXT" == "$COMPLETION_PROMISE" ]]; then
    rm -f "$STATE_FILE"
    exit 0
  fi
fi

NEXT_ITERATION=$((ITERATION + 1))
PROMPT_TEXT=$(awk '/^---$/{i++; next} i>=2' "$STATE_FILE")
if [[ -z "$PROMPT_TEXT" ]]; then
  rm -f "$STATE_FILE"
  exit 0
fi

TEMP_FILE="${STATE_FILE}.tmp.$$"
sed "s/^iteration: .*/iteration: $NEXT_ITERATION/" "$STATE_FILE" > "$TEMP_FILE"
mv "$TEMP_FILE" "$STATE_FILE"

SUMMARY=$(echo "$VERIFY_JSON" | jq -c '.checks | map({name, ok, verifiedRatio: .verifiedRatio, target: .target, missing: .missing})' 2>/dev/null || echo '[]')

FOLLOWUP=$(cat <<EOF
Ralph iteration ${NEXT_ITERATION}. Continue source-parity work until ralph-loop-verify reports complete=true for all checks.

Current verify snapshot: ${SUMMARY}

Priorities each iteration:
1. Advance SWKOTOR one-shot coverage toward >=90% verified matched functions (objdiff 0 is the bar for claims).
2. Keep upstream ReconstructKit bridges intact (vacuum, decomp-cli, source-parity-one-shot orchestrator, synthesis).
3. Bootstrap Jedi Academy pipeline (discover→inventory→matching) until >=90% verified coverage.
4. Run highest-leverage stage next: ./scripts/decomp-cli.sh source-parity-one-shot <binary> --resume --stop-after synthesize-candidates OR vacuum start for prompt queue.

When AND ONLY WHEN ./scripts/ralph-loop-verify.sh exits 0, output exactly: <promise>${COMPLETION_PROMISE}</promise>

Original task:
${PROMPT_TEXT}
EOF
)

jq -n --arg msg "$FOLLOWUP" '{"followup_message": $msg}'
exit 0
