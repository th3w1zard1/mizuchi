#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT/scripts/decomp-cli.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PACKAGE="$TMP_DIR/package"
TASK="$PACKAGE/function-reconstruction-tasks/0000_test_fn"
PROMPTS="$TMP_DIR/prompts"
mkdir -p "$TASK" "$PROMPTS"

printf '\x55\xc3' >"$TASK/target.bin"
mkdir -p "$TASK/verifiers"
cat >"$TASK/verifiers/run.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ -f candidate.c ]]
cp target.bin candidate.bin
echo FUNCTION_RECONSTRUCTION_CANDIDATE_OK
SH
chmod +x "$TASK/verifiers/run.sh"
cat >"$TASK/ONE_SHOT_SOURCE_PROMPT.md" <<'MD'
# One-shot semantic source prompt: test_fn

Produce candidate.c.
MD
cat >"$TASK/task.json" <<'JSON'
{
  "schema": "reconkit.one-shot-source-function-reconstruction-task.v1",
  "name": "test_fn",
  "status": "ready-for-semantic-source-attempt",
  "semanticDecompilation": false,
  "verifiedAgainstSource": false,
  "target": {
    "path": "function-reconstruction-tasks/0000_test_fn/target.bin",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "size": 2,
    "fileOffset": 0,
    "section": ".text",
    "sectionName": ".text",
    "address": "0x1000"
  },
	  "acceptance": {
	    "candidateVerifier": "function-reconstruction-tasks/0000_test_fn/verifiers/run.sh",
	    "oneShotPrompt": "function-reconstruction-tasks/0000_test_fn/ONE_SHOT_SOURCE_PROMPT.md"
	  }
}
JSON
cat >"$TASK/candidate.c" <<'C'
void test_fn(void) {}
C
cat >"$PACKAGE/FUNCTION_RECONSTRUCTION_TASKS.json" <<'JSON'
{
  "schema": "reconkit.one-shot-source-function-reconstruction-tasks.v1",
  "status": "tasks-present",
  "taskCount": 1,
  "semanticDecompilation": false,
  "verifiedAgainstSource": false,
  "tasks": [
    {
      "name": "test_fn",
      "path": "function-reconstruction-tasks/0000_test_fn",
      "taskJson": "function-reconstruction-tasks/0000_test_fn/task.json",
      "candidateVerifier": "function-reconstruction-tasks/0000_test_fn/verifiers/run.sh",
      "oneShotPrompt": "function-reconstruction-tasks/0000_test_fn/ONE_SHOT_SOURCE_PROMPT.md",
      "targetBytes": "function-reconstruction-tasks/0000_test_fn/target.bin",
      "targetSize": 2,
      "semanticDecompilation": false,
      "verifiedAgainstSource": false
    }
  ]
}
JSON

report="$("$CLI" import-one-shot-tasks --package "$PACKAGE" --prompts-dir "$PROMPTS" --prefix oss_ --copy-candidates)"
printf '%s\n' "$report" | jq -e '
  .schema == "reconkit.import-one-shot-tasks.v1" and
  .status == "imported" and
  .importedCount == 1 and
  .prompts[0].name == "oss_test_fn" and
  .prompts[0].copiedCandidate == true
' >/dev/null

PROMPT="$PROMPTS/oss_test_fn"
[[ -f "$PROMPT/settings.yaml" && -f "$PROMPT/case.yaml" && -f "$PROMPT/prompt.md" && -f "$PROMPT/candidate.c" ]]
"$ROOT/scripts/validate-prompt-settings.sh" "$PROMPT" >/dev/null
"$ROOT/scripts/validate-case-manifests.sh" "$PROMPT" >/dev/null
grep -q -- "--verifier 'verifiers/run.sh'" "$PROMPT/case.yaml"

"$ROOT/scripts/build-and-verify.sh" --prompt "$PROMPT" | jq -e '
  .schema == "reconkit.build-and-verify.v1" and
  .status == "matched" and
  .method == "custom" and
  .byte_identical == true
' >/dev/null

STATE="$TMP_DIR/state"
"$CLI" vacuum init --prompts-dir "$PROMPTS" --queue "$STATE/queue.json" --scores "$STATE/scores.json" --session "$STATE/session.json" --log "$TMP_DIR/logs/progress.log" | jq -e '
  .schema == "reconkit.vacuum-init.v1" and .summary.pending == 1
' >/dev/null

BAD_PACKAGE="$TMP_DIR/bad-package"
BAD_TASK="$BAD_PACKAGE/function-reconstruction-tasks/0000_bad_fn"
mkdir -p "$BAD_TASK"
cp "$TASK/target.bin" "$BAD_TASK/target.bin"
mkdir -p "$BAD_TASK/verifiers"
cp "$TASK/verifiers/run.sh" "$BAD_TASK/verifiers/run.sh"
cat >"$BAD_TASK/task.json" <<'JSON'
{
  "schema": "reconkit.one-shot-source-function-reconstruction-task.v1",
  "name": "bad_fn",
  "status": "ready-for-semantic-source-attempt",
  "semanticDecompilation": false,
  "verifiedAgainstSource": false,
  "target": {
    "path": "function-reconstruction-tasks/0000_bad_fn/target.bin",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "size": 2
  },
  "acceptance": {
    "candidateVerifier": "function-reconstruction-tasks/0000_bad_fn/verifiers/run.sh"
  }
}
JSON
cat >"$BAD_PACKAGE/FUNCTION_RECONSTRUCTION_TASKS.json" <<'JSON'
{
  "schema": "reconkit.one-shot-source-function-reconstruction-tasks.v1",
  "status": "tasks-present",
  "taskCount": 1,
  "tasks": [
    {
      "name": "bad_fn",
      "path": "function-reconstruction-tasks/0000_bad_fn",
      "taskJson": "function-reconstruction-tasks/0000_bad_fn/task.json",
      "candidateVerifier": "../outside-verifier.sh",
      "targetBytes": "function-reconstruction-tasks/0000_bad_fn/target.bin"
    }
  ]
}
JSON
set +e
"$CLI" import-one-shot-tasks --package "$BAD_PACKAGE" --prompts-dir "$TMP_DIR/bad-prompts" >"$TMP_DIR/bad.out" 2>"$TMP_DIR/bad.err"
bad_rc=$?
set -e
[[ "$bad_rc" -ne 0 ]]
grep -q "unsafe verifier path" "$TMP_DIR/bad.err"

echo "ok"
