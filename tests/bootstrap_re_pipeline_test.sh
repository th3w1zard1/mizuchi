#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/bootstrap-re-pipeline.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PROMPT="$TMP_DIR/prompts/fun_test"
out="$("$SCRIPT" --prompt "$PROMPT")"
last_line="$(printf '%s\n' "$out" | tail -n 1)"
[[ "$last_line" == "RE_BOOTSTRAP_OK prompt=$PROMPT" ]] || {
  echo "unexpected output: $out" >&2
  exit 1
}

[[ -f "$PROMPT/prompt.md" ]] || {
  echo "missing prompt.md" >&2
  exit 1
}
[[ -f "$PROMPT/case.yaml" ]] || {
  echo "missing case.yaml" >&2
  exit 1
}
[[ -f "$PROMPT/settings.yaml" ]] || {
  echo "missing settings.yaml" >&2
  exit 1
}
grep -q '^  targetObjectPath: path/to/{{functionName}}\.o$' "$PROMPT/case.yaml" || {
  echo "case.yaml targetObjectPath placeholder mismatch" >&2
  exit 1
}

orig_prompt_contents="$(cat "$PROMPT/prompt.md")"
orig_case_contents="$(cat "$PROMPT/case.yaml")"
out2="$("$SCRIPT" --prompt "$PROMPT")"
last_line2="$(printf '%s\n' "$out2" | tail -n 1)"
[[ "$last_line2" == "RE_BOOTSTRAP_OK prompt=$PROMPT" ]] || {
  echo "unexpected output on rerun: $out2" >&2
  exit 1
}
[[ "$(cat "$PROMPT/prompt.md")" == "$orig_prompt_contents" ]] || {
  echo "prompt.md was unexpectedly overwritten" >&2
  exit 1
}
[[ "$(cat "$PROMPT/case.yaml")" == "$orig_case_contents" ]] || {
  echo "case.yaml was unexpectedly overwritten" >&2
  exit 1
}

set +e
"$SCRIPT" >"$TMP_DIR/usage.txt" 2>&1
status=$?
set -e
[[ "$status" -eq 2 ]] || {
  echo "expected exit 2, got $status" >&2
  exit 1
}
grep -q "usage:" "$TMP_DIR/usage.txt" || {
  echo "missing usage output" >&2
  exit 1
}

echo "ok"
