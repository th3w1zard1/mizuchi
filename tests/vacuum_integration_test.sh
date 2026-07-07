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
FN="roundtrip_identity"
PROMPT_DIR="$PROMPTS/$FN"

mkdir -p "$PROMPT_DIR/build" "$STATE" "$LOGS"

if [[ -d "$ROOT/prompts/$FN" ]]; then
  cp -a "$ROOT/prompts/$FN/." "$PROMPT_DIR/"
  # Workspace fixture may already be matched; integration needs a pending queue entry.
  if [[ -f "$PROMPT_DIR/case.yaml" ]]; then
    sed -i 's/^status:.*/status: pending/' "$PROMPT_DIR/case.yaml"
  fi
else
  cat >"$PROMPT_DIR/settings.yaml" <<'YAML'
functionName: roundtrip_identity
targetObjectPath: prompt:/build/target.o
asm: |
  roundtrip_identity:
      movl %edi, %eax
      ret
YAML
  cat >"$PROMPT_DIR/case.yaml" <<'YAML'
caseId: roundtrip_identity
functionName: roundtrip_identity
targetObjectPath: prompt:/build/target.o
targetSourcePath: prompt:/target.c
candidateSourcePath: prompt:/candidate.c
targetFamily: elf-x86_64
compilerCommand: bash ./scripts/compile-local-fixture.sh "{{cFilePath}}" "{{objFilePath}}"
proof: byte-identical-object
status: pending
YAML
  cat >"$PROMPT_DIR/target.c" <<'C'
int roundtrip_identity(int value) {
  return value;
}
C
  cat >"$PROMPT_DIR/candidate.c" <<'C'
int roundtrip_identity(int value) {
  return value;
}
C
  printf '# roundtrip_identity\n' >"$PROMPT_DIR/prompt.md"
fi

init_report="$("$CLI" vacuum init \
  --prompts-dir "$PROMPTS" \
  --queue "$QUEUE" \
  --scores "$SCORES" \
  --log "$LOGS/progress.log" \
  --session "$SESSION")"

printf '%s\n' "$init_report" | jq -e '
  .schema == "mizuchi.vacuum-init.v1" and
  .status == "initialized" and
  .summary.pending >= 1
' >/dev/null

"$CLI" scorer --prompts-dir "$PROMPTS" --queue "$QUEUE" --out "$SCORES" --update-queue >/dev/null
[[ -f "$SCORES" ]]

"$ROOT/scripts/build-and-verify.sh" --prompt "$PROMPT_DIR" >/dev/null
[[ -f "$PROMPT_DIR/build/build-and-verify.json" ]]

vacuum_report="$("$CLI" vacuum start \
  --queue "$QUEUE" \
  --prompts-dir "$PROMPTS" \
  --scores "$SCORES" \
  --log "$LOGS/progress.log" \
  --session "$SESSION" \
  --max-functions 1 \
  --commit-after-match \
  --commit-dry-run \
  --runner-command 'test "{{name}}" = "roundtrip_identity"')"

printf '%s\n' "$vacuum_report" | jq -e '
  .schema == "mizuchi.vacuum.v1" and
  .processed == 1 and
  .summary.matched == 1 and
  .summary.pending == 0
' >/dev/null

jq -e '
  (.matched | map(.name) | index("roundtrip_identity")) != null and
  (.pending | length) == 0
' "$QUEUE" >/dev/null

[[ -f "$LOGS/progress.log" ]]
grep -q 'roundtrip_identity' "$LOGS/progress.log"

echo ok
