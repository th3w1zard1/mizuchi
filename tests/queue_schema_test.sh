#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/lib/queue-state.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

QUEUE="$TMP_DIR/state/queue.json"

missing="$("$SCRIPT" summary --queue "$QUEUE")"
printf '%s\n' "$missing" | jq -e '
  .schema == "reconkit.vacuum-queue-summary.v1" and
  .pending == 0 and .matched == 0 and .attempts == 0 and .next == null
' >/dev/null

"$SCRIPT" init --queue "$QUEUE" >/dev/null
[[ -f "$QUEUE" ]]
jq -e '
  .schema == "reconkit.vacuum-queue.v1" and
  (.pending | length) == 0 and
  (.attempts | type) == "object"
' "$QUEUE" >/dev/null

cat >"$QUEUE" <<'JSON'
{
  "schema": "reconkit.vacuum-queue.v1",
  "pending": [
    {"name": "hard_fn", "score": 10, "reason": "many branches"},
    {"name": "easy_fn", "score": 90, "reason": "straight-line"},
    {"name": "medium_fn", "score": 45, "reason": "some branches"}
  ],
  "matched": [],
  "integrated": [],
  "failed": [],
  "difficult": [],
  "attempts": {}
}
JSON

next="$("$SCRIPT" next --queue "$QUEUE")"
printf '%s\n' "$next" | jq -e '.name == "easy_fn" and .score == 90' >/dev/null

"$SCRIPT" move --queue "$QUEUE" --name easy_fn --to matched --reason "objdiff 0" >/dev/null
jq -e '
  (.pending | map(.name) | index("easy_fn") | not) and
  (.matched | length == 1) and
  .matched[0].name == "easy_fn" and
  .matched[0].reason == "objdiff 0" and
  (.matched[0].updatedAt | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T"))
' "$QUEUE" >/dev/null

"$SCRIPT" attempt --queue "$QUEUE" --name hard_fn --status mismatch --message "objdiff mismatch" >/dev/null
"$SCRIPT" attempt --queue "$QUEUE" --name hard_fn --status parse_error --message "bad response" >/dev/null
jq -e '
  .attempts.hard_fn.count == 2 and
  .attempts.hard_fn.lastStatus == "parse_error" and
  (.attempts.hard_fn.history | length) == 2
' "$QUEUE" >/dev/null

summary="$("$SCRIPT" summary --queue "$QUEUE")"
printf '%s\n' "$summary" | jq -e '
  .pending == 2 and .matched == 1 and .attempts == 1 and .next.name == "medium_fn"
' >/dev/null

PROMPTS="$TMP_DIR/prompts"
mkdir -p "$PROMPTS/pending_a" "$PROMPTS/matched_b" "$PROMPTS/blocked_c" "$PROMPTS/_template"
cat >"$PROMPTS/pending_a/settings.yaml" <<'YAML'
functionName: pending_a
targetObjectPath: prompt:/build/target.o
asm: |
  pending_a:
      ret
YAML
cat >"$PROMPTS/pending_a/prompt.md" <<'MD'
# pending_a
MD
cat >"$PROMPTS/pending_a/case.yaml" <<'YAML'
status: pending
YAML
cat >"$PROMPTS/matched_b/case.yaml" <<'YAML'
status: matched
YAML
cat >"$PROMPTS/blocked_c/case.yaml" <<'YAML'
status: blocked
YAML
cat >"$PROMPTS/_template/case.yaml" <<'YAML'
status: pending
YAML

SEEDED="$TMP_DIR/state/seeded.json"
"$SCRIPT" init --queue "$SEEDED" --prompts-dir "$PROMPTS" >/dev/null
jq -e '
  (.pending | length) == 1 and
  .pending[0].name == "pending_a" and
  (.pending | map(.name) | index("_template") | not) and
  (.matched | length) == 0
' "$SEEDED" >/dev/null

BAD="$TMP_DIR/bad.json"
printf '{bad json\n' >"$BAD"
set +e
"$SCRIPT" summary --queue "$BAD" >"$TMP_DIR/bad.out" 2>"$TMP_DIR/bad.err"
bad_rc=$?
set -e
[[ "$bad_rc" -ne 0 ]]
grep -q "invalid queue JSON" "$TMP_DIR/bad.err"

echo "ok"
