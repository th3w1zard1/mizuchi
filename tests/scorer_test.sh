#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCORER="$ROOT/scripts/scorer.sh"
CLI="$ROOT/scripts/decomp-cli.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PROMPTS="$TMP_DIR/prompts"
STATE="$TMP_DIR/state"
mkdir -p "$PROMPTS/easy_fn" "$PROMPTS/hard_fn" "$PROMPTS/blocked_fn" "$PROMPTS/_template" "$STATE"

cat >"$PROMPTS/easy_fn/settings.yaml" <<'YAML'
functionName: easy_fn
targetObjectPath: prompt:/build/target.o
asm: |
  easy_fn:
      movl %edi, %eax
      ret
YAML
cat >"$PROMPTS/easy_fn/case.yaml" <<'YAML'
status: pending
YAML

cat >"$PROMPTS/hard_fn/settings.yaml" <<'YAML'
functionName: hard_fn
targetObjectPath: prompt:/build/target.o
asm: |
  hard_fn:
      movl %edi, %eax
      testl %eax, %eax
      je .Lzero
      cmpl $7, %eax
      jne .Lret
      call helper
  .Lzero:
      xorl %eax, %eax
      jmp .Ldone
  .Lret:
      addl $1, %eax
  .Ldone:
      ret
YAML
cat >"$PROMPTS/hard_fn/case.yaml" <<'YAML'
status: pending
YAML

cat >"$PROMPTS/blocked_fn/settings.yaml" <<'YAML'
functionName: blocked_fn
targetObjectPath: prompt:/build/target.o
asm: |
  blocked_fn:
      ret
YAML
cat >"$PROMPTS/blocked_fn/case.yaml" <<'YAML'
status: blocked
YAML

cat >"$PROMPTS/_template/settings.yaml" <<'YAML'
functionName: _template
targetObjectPath: prompt:/build/target.o
asm: |
  _template:
      ret
YAML

single="$("$SCORER" --prompt easy_fn --prompts-dir "$PROMPTS")"
printf '%s\n' "$single" | jq -e '
  .name == "easy_fn" and
  .schema == "reconkit.scorer-entry.v1" and
  .scorer == "heuristic" and
  .metrics.instructions == 2 and
  .score > 90
' >/dev/null

scores="$STATE/scores.json"
report="$("$SCORER" --prompts-dir "$PROMPTS" --out "$scores")"
[[ -f "$scores" ]]
printf '%s\n' "$report" | jq -e '
  .schema == "reconkit.scorer.v1" and
  .scorer == "heuristic" and
  .ml.enabled == false and
  .count == 2 and
  .entries[0].name == "easy_fn" and
  .entries[1].name == "hard_fn" and
  ([.entries[].name] | index("blocked_fn") | not) and
  ([.entries[].name] | index("_template") | not)
' >/dev/null

jq -e '.entries[0].score > .entries[1].score' "$scores" >/dev/null

QUEUE="$STATE/queue.json"
"$CLI" queue init --queue "$QUEUE" --prompts-dir "$PROMPTS" >/dev/null
"$CLI" scorer --prompts-dir "$PROMPTS" --queue "$QUEUE" --update-queue --out "$STATE/queued-scores.json" >/dev/null
jq -e '
  .pending[0].name == "easy_fn" and
  .pending[0].score > .pending[1].score and
  (.pending[0].metrics.instructions == 2) and
  (.pending[0].scoredAt | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T"))
' "$QUEUE" >/dev/null

EMPTY="$STATE/empty.json"
cat >"$EMPTY" <<'JSON'
{
  "schema": "reconkit.vacuum-queue.v1",
  "pending": [],
  "matched": [],
  "integrated": [],
  "failed": [],
  "difficult": [],
  "attempts": {}
}
JSON
"$SCORER" --prompts-dir "$PROMPTS" --queue "$EMPTY" | jq -e '.count == 0 and .entries == []' >/dev/null

SCORER_ML_ENABLED=true "$SCORER" --prompt hard_fn --prompts-dir "$PROMPTS" | jq -e '
  .scorer == "heuristic" and .score < 90
' >/dev/null

echo "ok"
