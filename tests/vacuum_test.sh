#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT/scripts/decomp-cli.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PROMPTS="$TMP_DIR/prompts"
STATE="$TMP_DIR/state"
LOG="$TMP_DIR/logs/progress.log"
SESSION="$STATE/session.json"
mkdir -p "$PROMPTS/easy_fn" "$PROMPTS/hard_fn" "$STATE"

cat >"$PROMPTS/easy_fn/settings.yaml" <<'YAML'
functionName: easy_fn
targetObjectPath: prompt:/build/target.o
asm: |
  easy_fn:
      movl %edi, %eax
      ret
YAML
cat >"$PROMPTS/easy_fn/case.yaml" <<'YAML'
caseId: easy_fn
functionName: easy_fn
targetObjectPath: prompt:/build/target.o
targetSourcePath: prompt:/target.c
candidateSourcePath: prompt:/candidate.c
targetFamily: elf-x86_64
compilerCommand: bash ./scripts/compile-local-fixture.sh "{{cFilePath}}" "{{objFilePath}}"
proof: byte-identical-object
status: pending
YAML
cat >"$PROMPTS/easy_fn/target.c" <<'C'
int easy_fn(int value) {
  return value * 3 + 7;
}
C
cat >"$PROMPTS/easy_fn/candidate.c" <<'C'
int easy_fn(int value) {
  return value * 3 + 7;
}
C

cat >"$PROMPTS/hard_fn/settings.yaml" <<'YAML'
functionName: hard_fn
targetObjectPath: prompt:/build/target.o
asm: |
  hard_fn:
      testl %edi, %edi
      je .Lzero
      call helper
  .Lzero:
      ret
YAML
cat >"$PROMPTS/hard_fn/case.yaml" <<'YAML'
status: pending
YAML

QUEUE="$STATE/queue.json"
SCORES="$STATE/scores.json"

"$CLI" vacuum start \
  --queue "$QUEUE" \
  --prompts-dir "$PROMPTS" \
  --scores "$SCORES" \
  --log "$LOG" \
  --session "$SESSION" \
  --max-functions 1 \
  --commit-after-match \
  --commit-dry-run \
  --runner-command 'test "{{name}}" = "easy_fn"' \
  | jq -e '
    .schema == "reconkit.vacuum.v1" and
    .processed == 1 and
    .summary.matched == 1 and
    .summary.pending == 1
  ' >/dev/null

jq -e '
  (.matched | length) == 1 and
  .matched[0].name == "easy_fn" and
  .attempts.easy_fn.count == 1 and
  .attempts.easy_fn.lastStatus == "matched"
' "$QUEUE" >/dev/null
jq -e '.schema == "reconkit.vacuum-session.v1" and .status == "matched" and .currentFunction == "easy_fn"' "$SESSION" >/dev/null
grep -q 'easy_fn MATCHED' "$LOG"
grep -q 'easy_fn COMMIT_VERIFIED' "$LOG"
[[ -f "$SCORES" ]]

"$CLI" vacuum start \
  --queue "$QUEUE" \
  --prompts-dir "$PROMPTS" \
  --scores "$SCORES" \
  --log "$LOG" \
  --session "$SESSION" \
  --max-functions 1 \
  --max-attempts 1 \
  --runner-command 'false' \
  | jq -e '
    .processed == 1 and
    .summary.difficult == 1 and
    .summary.pending == 0
  ' >/dev/null

jq -e '
  (.difficult | length) == 1 and
  .difficult[0].name == "hard_fn" and
  .attempts.hard_fn.count == 1 and
  .attempts.hard_fn.lastStatus == "failed"
' "$QUEUE" >/dev/null
grep -q 'hard_fn DIFFICULT' "$LOG"

"$CLI" vacuum reset-queue --queue "$QUEUE" --name hard_fn >/dev/null
jq -e '
  (.pending | length) == 1 and
  .pending[0].name == "hard_fn" and
  (.difficult | length) == 0
' "$QUEUE" >/dev/null

"$CLI" vacuum start \
  --queue "$QUEUE" \
  --prompts-dir "$PROMPTS" \
  --scores "$SCORES" \
  --log "$LOG" \
  --session "$SESSION" \
  --max-functions 1 \
  --max-attempts 5 \
  --backoff-base 2 \
  --backoff-max 8 \
  --no-sleep \
  --runner-command 'echo "429 rate limit quota exceeded"; exit 1' \
  | jq -e '
    .processed == 1 and
    .summary.pending == 1 and
    .summary.difficult == 0
  ' >/dev/null

jq -e '
  .pending[0].name == "hard_fn" and
  .attempts.hard_fn.count == 2 and
  .attempts.hard_fn.lastStatus == "quota"
' "$QUEUE" >/dev/null
jq -e '
  .status == "backoff" and
  .currentFunction == "hard_fn" and
  .backoffSeconds == 4
' "$SESSION" >/dev/null
grep -q 'hard_fn BACKOFF quota' "$LOG"

"$CLI" vacuum status --queue "$QUEUE" --log "$LOG" --session "$SESSION" | jq -e '
  .schema == "reconkit.vacuum-status.v1" and
  .summary.pending == 1 and
  .lastSession.status == "backoff"
' >/dev/null

"$CLI" vacuum start \
  --queue "$QUEUE" \
  --prompts-dir "$PROMPTS" \
  --scores "$SCORES" \
  --log "$LOG" \
  --session "$SESSION" \
  --max-functions 1 \
  --timeout 0s \
  --runner-command 'echo should-not-run; exit 1' \
  | jq -e '
    .processed == 0 and
    .timeout == "0s" and
    .summary.pending == 1
  ' >/dev/null

jq -e '.status == "timeout" and .message == "deadline reached"' "$SESSION" >/dev/null
grep -q 'VACUUM_TIMEOUT deadline reached' "$LOG"
if grep -q 'should-not-run' "$LOG"; then
  echo "timeout runner unexpectedly executed" >&2
  exit 1
fi

"$CLI" vacuum inspect-queue --queue "$QUEUE" | jq -e '
  .schema == "reconkit.vacuum-queue.v1" and (.matched | length) == 1 and (.pending | length) == 1
' >/dev/null

echo "ok"
