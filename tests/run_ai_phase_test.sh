#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/run-ai-phase.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PROMPT="$TMP_DIR/prompts/ai_fn"
mkdir -p "$PROMPT"
cat >"$PROMPT/settings.yaml" <<'YAML'
functionName: ai_fn
targetObjectPath: prompt:/build/target.o
asm: |
  ai_fn:
      ret
YAML
cat >"$PROMPT/case.yaml" <<'YAML'
caseId: ai_fn
functionName: ai_fn
targetObjectPath: prompt:/build/target.o
status: pending
YAML
cat >"$PROMPT/prompt.md" <<'MD'
# ai_fn
MD

set +e
PATH="/usr/bin:/bin" MIZUCHI_IMAGE="localhost/mizuchi-missing:never" "$SCRIPT" --prompt "$PROMPT" >"$TMP_DIR/manual.out" 2>"$TMP_DIR/manual.err"
manual_rc=$?
set -e
[[ "$manual_rc" -eq 3 ]] || {
  echo "expected manual fallback exit 3, got $manual_rc" >&2
  cat "$TMP_DIR/manual.err" >&2 || true
  exit 1
}
grep -q "No mizuchi runner found" "$TMP_DIR/manual.err"
jq -e '
  .schema == "mizuchi.ai-phase.v1" and
  .status == "manual-required" and
  .exitCode == 3 and
  .runner == "cursor-native" and
  .reason == "no mizuchi runner found" and
  .anthropicApiKeyPresent == false
' "$PROMPT/build/ai-phase.json" >/dev/null

mkdir -p "$TMP_DIR/bin"
cat >"$TMP_DIR/bin/mizuchi" <<'SH'
#!/usr/bin/env bash
[[ "$1" == "run" && "$2" == "--config" ]] || exit 2
exit 0
SH
chmod +x "$TMP_DIR/bin/mizuchi"
PATH="$TMP_DIR/bin:/usr/bin:/bin" "$SCRIPT" --prompt "$PROMPT" >"$TMP_DIR/native.out" 2>"$TMP_DIR/native.err"
grep -q "AI phase via native mizuchi run" "$TMP_DIR/native.err"
jq -e '
  .schema == "mizuchi.ai-phase.v1" and
  .status == "matched" and
  .exitCode == 0 and
  .runner == "native-mizuchi" and
  .reason == "mizuchi run completed with objdiff 0"
' "$PROMPT/build/ai-phase.json" >/dev/null

cat >"$TMP_DIR/bin/mizuchi" <<'SH'
#!/usr/bin/env bash
[[ "$1" == "run" && "$2" == "--config" ]] || exit 2
exit 7
SH
chmod +x "$TMP_DIR/bin/mizuchi"
set +e
PATH="$TMP_DIR/bin:/usr/bin:/bin" "$SCRIPT" --prompt "$PROMPT" >"$TMP_DIR/native-fail.out" 2>"$TMP_DIR/native-fail.err"
native_fail_rc=$?
set -e
[[ "$native_fail_rc" -eq 7 ]] || {
  echo "expected native mizuchi failure exit 7, got $native_fail_rc" >&2
  cat "$TMP_DIR/native-fail.err" >&2 || true
  exit 1
}
jq -e '
  .schema == "mizuchi.ai-phase.v1" and
  .status == "failed" and
  .exitCode == 7 and
  .runner == "native-mizuchi" and
  .reason == "mizuchi run failed"
' "$PROMPT/build/ai-phase.json" >/dev/null

MATCH_PROMPT="$TMP_DIR/prompts/match_fn"
mkdir -p "$MATCH_PROMPT"
cat >"$MATCH_PROMPT/settings.yaml" <<'YAML'
functionName: match_fn
targetObjectPath: prompt:/build/target.o
asm: |
  match_fn:
      leal    7(%rdi,%rdi,2), %eax
      ret
YAML
cat >"$MATCH_PROMPT/case.yaml" <<'YAML'
caseId: match_fn
functionName: match_fn
targetObjectPath: prompt:/build/target.o
targetSourcePath: prompt:/target.c
candidateSourcePath: prompt:/candidate.c
targetFamily: elf-x86_64
compilerCommand: bash ./scripts/compile-local-fixture.sh "{{cFilePath}}" "{{objFilePath}}"
proof: byte-identical-object
status: pending
YAML
cat >"$MATCH_PROMPT/prompt.md" <<'MD'
# match_fn
MD
cat >"$MATCH_PROMPT/target.c" <<'C'
int match_fn(int value) {
  return value * 3 + 7;
}
C
cat >"$MATCH_PROMPT/candidate.c" <<'C'
int match_fn(int value) {
  return value * 3 + 7;
}
C
"$ROOT/scripts/build-and-verify.sh" --prompt "$MATCH_PROMPT" --refresh-target >/dev/null
cat >"$TMP_DIR/matcher-response.txt" <<'TXT'
```c
int match_fn(int value) {
  return value * 3 + 7;
}
```
TXT
PATH="/usr/bin:/bin" \
  MIZUCHI_IMAGE="localhost/mizuchi-missing:never" \
  MIZUCHI_MATCHER_COMMAND="cp '$TMP_DIR/matcher-response.txt' '{{responseFile}}'" \
  "$SCRIPT" --prompt "$MATCH_PROMPT" >"$TMP_DIR/matcher.out" 2>"$TMP_DIR/matcher.err"
grep -q "AI phase via one-shot matcher command" "$TMP_DIR/matcher.err"
jq -e '
  .schema == "mizuchi.ai-phase.v1" and
  .status == "matched" and
  .exitCode == 0 and
  .runner == "one-shot-matcher" and
  .reason == "matcher trial.c completed with objdiff 0"
' "$MATCH_PROMPT/build/ai-phase.json" >/dev/null
jq -e '.status == "success" and .trialSourcePresent == true' "$MATCH_PROMPT/build/matcher.json" >/dev/null
grep -q "value \\* 3 + 7" "$MATCH_PROMPT/trial.c"

BLOCKED_PROMPT="$TMP_DIR/prompts/blocked"
mkdir -p "$BLOCKED_PROMPT"
cat >"$BLOCKED_PROMPT/settings.yaml" <<'YAML'
functionName: blocked
targetObjectPath: prompt:/build/target.o
asm: |
  blocked:
      ret
YAML
cat >"$BLOCKED_PROMPT/case.yaml" <<'YAML'
caseId: blocked
functionName: blocked
targetObjectPath: prompt:/build/target.o
proof: objdiff-0
status: blocked
blockedReason: fixture blocked
YAML
cat >"$BLOCKED_PROMPT/prompt.md" <<'MD'
# blocked
MD

set +e
"$SCRIPT" --prompt "$BLOCKED_PROMPT" >"$TMP_DIR/blocked.out" 2>"$TMP_DIR/blocked.err"
blocked_rc=$?
set -e
[[ "$blocked_rc" -eq 3 ]] || {
  echo "expected blocked exit 3, got $blocked_rc" >&2
  cat "$TMP_DIR/blocked.err" >&2 || true
  exit 1
}
jq -e '
  .schema == "mizuchi.ai-phase.v1" and
  .status == "blocked" and
  .exitCode == 3 and
  .reason == "fixture blocked"
' "$BLOCKED_PROMPT/build/ai-phase.json" >/dev/null

echo "ok"
