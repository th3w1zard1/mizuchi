#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT/scripts/decomp-cli.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PROMPTS="$TMP_DIR/prompts"
STATE="$TMP_DIR/state"
LOGS="$TMP_DIR/logs"
QUEUE="$STATE/queue.json"
SCORES="$STATE/scores.json"
SESSION="$STATE/session.json"
mkdir -p "$PROMPTS/pending_fn" "$PROMPTS/matched_fn" "$PROMPTS/integrated_fn" "$PROMPTS/blocked_fn" "$PROMPTS/_template"

write_settings() {
  local dir="$1" fn="$2" asm="$3"
  cat >"$dir/settings.yaml" <<YAML
functionName: $fn
targetObjectPath: prompt:/build/target.o
asm: |
$asm
YAML
  printf '# %s\n' "$fn" >"$dir/prompt.md"
}

write_settings "$PROMPTS/pending_fn" pending_fn "  pending_fn:
      movl %edi, %eax
      ret"
cat >"$PROMPTS/pending_fn/case.yaml" <<'YAML'
status: pending
YAML

write_settings "$PROMPTS/matched_fn" matched_fn "  matched_fn:
      ret"
cat >"$PROMPTS/matched_fn/case.yaml" <<'YAML'
status: matched
YAML

write_settings "$PROMPTS/integrated_fn" integrated_fn "  integrated_fn:
      ret"
cat >"$PROMPTS/integrated_fn/case.yaml" <<'YAML'
status: integrated
YAML

write_settings "$PROMPTS/blocked_fn" blocked_fn "  blocked_fn:
      ret"
cat >"$PROMPTS/blocked_fn/case.yaml" <<'YAML'
status: blocked
blockedReason: fixture blocked
YAML

write_settings "$PROMPTS/_template" template_fn "  template_fn:
      ret"
cat >"$PROMPTS/_template/case.yaml" <<'YAML'
status: pending
YAML

init_report="$("$CLI" vacuum init \
  --prompts-dir "$PROMPTS" \
  --queue "$QUEUE" \
  --scores "$SCORES" \
  --log "$LOGS/progress.log" \
  --session "$SESSION")"

printf '%s\n' "$init_report" | jq -e '
  .schema == "reconkit.vacuum-init.v1" and
  .status == "initialized" and
  .promptTotal == 4 and
  .blockedPrompts == 1 and
  .summary.pending == 1 and
  .summary.matched == 1 and
  .summary.integrated == 1
' >/dev/null

[[ -f "$QUEUE" && -f "$SCORES" && -f "$SESSION" ]]
jq -e '
  (.pending | length) == 1 and .pending[0].name == "pending_fn" and
  (.matched | length) == 1 and .matched[0].name == "matched_fn" and
  (.integrated | length) == 1 and .integrated[0].name == "integrated_fn" and
  ([.pending[].name, .matched[].name, .integrated[].name] | index("_template") | not)
' "$QUEUE" >/dev/null
jq -e '.status == "initialized" and .blockedPrompts == 1' "$SESSION" >/dev/null
jq -e '.schema == "reconkit.scorer.v1" and .count == 1 and .entries[0].name == "pending_fn"' "$SCORES" >/dev/null

"$CLI" vacuum start \
  --prompts-dir "$PROMPTS" \
  --queue "$QUEUE" \
  --scores "$SCORES" \
  --log "$LOGS/progress.log" \
  --session "$SESSION" \
  --max-functions 1 \
  --runner-command 'test "{{name}}" = "pending_fn"' \
  | jq -e '.processed == 1 and .summary.pending == 0 and .summary.matched == 2' >/dev/null

"$CLI" vacuum resume \
  --prompts-dir "$PROMPTS" \
  --queue "$QUEUE" \
  --scores "$SCORES" \
  --log "$LOGS/progress.log" \
  --session "$SESSION" \
  --max-functions 1 \
  --runner-command 'exit 99' \
  | jq -e '.processed == 0 and .summary.pending == 0' >/dev/null

"$CLI" vacuum reset-queue --queue "$QUEUE" --name pending_fn >/dev/null
jq -e '.pending[0].name == "pending_fn" and (.matched | length) == 1' "$QUEUE" >/dev/null

"$CLI" vacuum status --queue "$QUEUE" --session "$SESSION" --log "$LOGS/progress.log" | jq -e '
  .schema == "reconkit.vacuum-status.v1" and .summary.pending == 1
' >/dev/null

"$CLI" vacuum inspect-queue --queue "$QUEUE" | jq -e '
  .schema == "reconkit.vacuum-queue.v1" and .pending[0].name == "pending_fn"
' >/dev/null

echo "ok"
