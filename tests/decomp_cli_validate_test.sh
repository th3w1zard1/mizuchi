#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/decomp-cli.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

prompts_dir="$TMP_DIR/prompts"
prompt_dir="$prompts_dir/sample_fn"
mkdir -p "$prompt_dir"
cat >"$prompt_dir/prompt.md" <<'MD'
# sample_fn
MD
cat >"$prompt_dir/settings.yaml" <<'YAML'
functionName: sample_fn
targetObjectPath: prompt:/build/target.o
asm: |
  sample_fn:
      ret
YAML
cat >"$prompt_dir/case.yaml" <<'YAML'
caseId: sample_fn
functionName: sample_fn
targetObjectPath: prompt:/build/target.o
targetFamily: elf-x86_64
proof: objdiff-0
status: pending
YAML

output="$(MIZUCHI_PROMPTS_DIR="$prompts_dir" "$SCRIPT" decomp-validate sample_fn)"
grep -q "OK: $prompt_dir/settings.yaml" <<<"$output"
grep -q "CASE_MANIFESTS_OK checked=1" <<<"$output"

all_output="$(MIZUCHI_PROMPTS_DIR="$prompts_dir" "$SCRIPT" decomp-validate --all)"
grep -q "CASE_MANIFESTS_OK checked=1" <<<"$all_output"

cat >"$prompt_dir/case.yaml" <<'YAML'
caseId: sample_fn
functionName: sample_fn
targetObjectPath: prompt:/build/target.o
targetFamily: elf-x86_64
proof: objdiff-0
status: made_up
YAML

if MIZUCHI_PROMPTS_DIR="$prompts_dir" "$SCRIPT" decomp-validate sample_fn >/tmp/mizuchi-decomp-validate.out 2>/tmp/mizuchi-decomp-validate.err; then
  echo "expected decomp-validate to reject invalid case.yaml" >&2
  exit 1
fi
grep -q "unknown status: made_up" /tmp/mizuchi-decomp-validate.err

echo "ok"
