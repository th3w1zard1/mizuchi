#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT/scripts/decomp-cli.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PACKAGE="$TMP_DIR/package"
PROMPTS="$TMP_DIR/prompts"
TASK_A="$PACKAGE/function-reconstruction-tasks/0000_matched_fn"
TASK_B="$PACKAGE/function-reconstruction-tasks/0001_missing_fn"
mkdir -p "$TASK_A/verifiers" "$TASK_B/verifiers" "$PROMPTS"

printf '\x55\xc3' >"$TASK_A/target.bin"
printf '\x90\xc3' >"$TASK_B/target.bin"
cat >"$TASK_A/verifiers/run.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
cp target.bin candidate.bin
SH
chmod +x "$TASK_A/verifiers/run.sh"
cp "$TASK_A/verifiers/run.sh" "$TASK_B/verifiers/run.sh"

cat >"$TASK_A/task.json" <<'JSON'
{
  "schema": "reconkit.one-shot-source-function-reconstruction-task.v1",
  "name": "matched_fn",
  "target": {"path": "function-reconstruction-tasks/0000_matched_fn/target.bin"},
  "acceptance": {"candidateVerifier": "function-reconstruction-tasks/0000_matched_fn/verifiers/run.sh"}
}
JSON
cat >"$TASK_B/task.json" <<'JSON'
{
  "schema": "reconkit.one-shot-source-function-reconstruction-task.v1",
  "name": "missing_fn",
  "target": {"path": "function-reconstruction-tasks/0001_missing_fn/target.bin"},
  "acceptance": {"candidateVerifier": "function-reconstruction-tasks/0001_missing_fn/verifiers/run.sh"}
}
JSON
cat >"$TASK_A/candidate.c" <<'C'
void matched_fn(void) {}
C
cat >"$PACKAGE/FUNCTION_RECONSTRUCTION_TASKS.json" <<'JSON'
{
  "schema": "reconkit.one-shot-source-function-reconstruction-tasks.v1",
  "status": "tasks-present",
  "taskCount": 2,
  "tasks": [
    {
      "name": "matched_fn",
      "path": "function-reconstruction-tasks/0000_matched_fn",
      "taskJson": "function-reconstruction-tasks/0000_matched_fn/task.json",
      "candidateVerifier": "function-reconstruction-tasks/0000_matched_fn/verifiers/run.sh",
      "targetBytes": "function-reconstruction-tasks/0000_matched_fn/target.bin"
    },
    {
      "name": "missing_fn",
      "path": "function-reconstruction-tasks/0001_missing_fn",
      "taskJson": "function-reconstruction-tasks/0001_missing_fn/task.json",
      "candidateVerifier": "function-reconstruction-tasks/0001_missing_fn/verifiers/run.sh",
      "targetBytes": "function-reconstruction-tasks/0001_missing_fn/target.bin"
    }
  ]
}
JSON
cat >"$PACKAGE/SEMANTIC_READINESS.json" <<'JSON'
{
  "schema": "reconkit.semantic-readiness.v1",
  "status": "not-ready",
  "missingEvidence": ["function-boundary map", "compiler profile"]
}
JSON

"$CLI" import-one-shot-tasks --package "$PACKAGE" --prompts-dir "$PROMPTS" --prefix oss_ --copy-candidates >/dev/null
rm -rf "$PROMPTS/oss_missing_fn"
"$ROOT/scripts/build-and-verify.sh" --prompt "$PROMPTS/oss_matched_fn" >/dev/null

coverage="$("$CLI" one-shot-task-coverage --package "$PACKAGE" --prompts-dir "$PROMPTS")"
printf '%s\n' "$coverage" | jq -e '
  .schema == "reconkit.one-shot-task-coverage.v1" and
  .status == "incomplete" and
  .semanticReady == false and
  .allTasksVerified == false and
  .summary.taskCount == 2 and
  .summary.imported == 1 and
  .summary.notImported == 1 and
  .summary.matched == 1 and
  .semanticSourceEvidence.missingEvidenceCount == 2 and
  (.claimBoundary | contains("not whole-app semantic source recovery"))
' >/dev/null
printf '%s\n' "$coverage" | jq -e '
  any(.tasks[]; .taskPath == "function-reconstruction-tasks/0000_matched_fn" and .classification == "matched") and
  any(.tasks[]; .taskPath == "function-reconstruction-tasks/0001_missing_fn" and .classification == "not_imported")
' >/dev/null

echo "ok"
