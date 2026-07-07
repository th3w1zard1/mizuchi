#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PROMPTS="$TMP_DIR/prompts"
PROMPT="$PROMPTS/test_fn"
mkdir -p "$PROMPT"

cat >"$PROMPT/settings.yaml" <<'YAML'
functionName: test_fn
targetObjectPath: prompt:/build/target.o
asm: |
  test_fn:
      leal    7(%rdi,%rdi,2), %eax
      ret
YAML
cat >"$PROMPT/case.yaml" <<'YAML'
caseId: test_fn
functionName: test_fn
targetObjectPath: prompt:/build/target.o
targetFamily: elf-x86_64
binaryPath: prompt:/build/target.o
proof: objdiff-0
status: pending
YAML
cat >"$PROMPT/prompt.md" <<'MD'
# test_fn

Objective: match `test_fn`.
MD

prompt_text="$("$ROOT/scripts/lib/matcher-prompt.sh" --prompt "$PROMPT" --out "$TMP_DIR/prompt.md")"
printf '%s\n' "$prompt_text" | grep -q "You have ONE SHOT"
printf '%s\n' "$prompt_text" | grep -q "Function: test_fn"
printf '%s\n' "$prompt_text" | grep -q "leal"
[[ -f "$TMP_DIR/prompt.md" ]]

cat >"$TMP_DIR/response.txt" <<'TXT'
```c
int test_fn(int value) {
  return value * 3 + 7;
}
```
TXT

"$ROOT/scripts/lib/matcher-parse.sh" --input "$TMP_DIR/response.txt" --out "$TMP_DIR/trial.c" --json "$TMP_DIR/parse.json"
grep -q "value \\* 3 + 7" "$TMP_DIR/trial.c"
jq -e '.schema == "mizuchi.matcher-parse.v1" and .status == "parsed" and .bytes > 0' "$TMP_DIR/parse.json" >/dev/null

cat >"$TMP_DIR/raw.c" <<'C'
int test_fn(int value) {
  return value * 3 + 7;
}
C
"$ROOT/scripts/lib/matcher-parse.sh" --input "$TMP_DIR/raw.c" --out "$TMP_DIR/raw-trial.c" --json "$TMP_DIR/raw-parse.json"
grep -q "value \\* 3 + 7" "$TMP_DIR/raw-trial.c"
jq -e '.status == "parsed"' "$TMP_DIR/raw-parse.json" >/dev/null

cat >"$TMP_DIR/bad-response.txt" <<'TXT'
I cannot provide code.
TXT
set +e
"$ROOT/scripts/lib/matcher-parse.sh" --input "$TMP_DIR/bad-response.txt" --out "$TMP_DIR/bad.c" --json "$TMP_DIR/bad-parse.json" >"$TMP_DIR/bad.out" 2>"$TMP_DIR/bad.err"
bad_rc=$?
set -e
[[ "$bad_rc" -eq 1 ]]
jq -e '.status == "parse_error"' "$TMP_DIR/bad-parse.json" >/dev/null

out="$("$ROOT/scripts/matcher.sh" --prompt "$PROMPT" --response-file "$TMP_DIR/response.txt")"
printf '%s\n' "$out" | jq -e '.schema == "mizuchi.matcher.v1" and .status == "success" and .trialSourcePresent == true and .runner == "response-file"' >/dev/null
cmp -s "$TMP_DIR/trial.c" "$PROMPT/trial.c"
[[ -f "$PROMPT/build/matcher-prompt.md" ]]
[[ -f "$PROMPT/build/matcher-response.txt" ]]
[[ -f "$PROMPT/build/matcher-parse.json" ]]

cli_out="$(MIZUCHI_PROMPTS_DIR="$PROMPTS" "$ROOT/scripts/decomp-cli.sh" matcher test_fn --response-file "$TMP_DIR/response.txt")"
printf '%s\n' "$cli_out" | jq -e '.status == "success"' >/dev/null

set +e
"$ROOT/scripts/matcher.sh" --prompt "$PROMPT" >"$TMP_DIR/manual.out" 2>"$TMP_DIR/manual.err"
manual_rc=$?
set -e
[[ "$manual_rc" -eq 3 ]]
jq -e '.status == "manual-required" and .exitCode == 3' "$PROMPT/build/matcher.json" >/dev/null

echo "ok"
