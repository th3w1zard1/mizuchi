#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/integrate-verified-match.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PROMPT="$TMP_DIR/prompts/roundtrip_identity"
mkdir -p "$PROMPT"
cat >"$PROMPT/settings.yaml" <<'YAML'
functionName: roundtrip_identity
targetObjectPath: prompt:/build/target.o
asm: |
  roundtrip_identity:
      leal    7(%rdi,%rdi,2), %eax
      ret
YAML
cat >"$PROMPT/case.yaml" <<'YAML'
caseId: roundtrip_identity
functionName: roundtrip_identity
targetObjectPath: prompt:/build/target.o
targetSourcePath: prompt:/target.c
candidateSourcePath: prompt:/candidate.c
targetFamily: elf-x86_64
compilerCommand: bash ./scripts/compile-local-fixture.sh "{{cFilePath}}" "{{objFilePath}}"
proof: byte-identical-object
status: matched
YAML
cat >"$PROMPT/prompt.md" <<'MD'
# roundtrip_identity
MD
cat >"$PROMPT/target.c" <<'C'
int roundtrip_identity(int value) {
  return value * 3 + 7;
}
C
cat >"$PROMPT/candidate.c" <<'C'
int roundtrip_identity(int value) {
  return value * 3 + 7;
}
C

source_out="$TMP_DIR/src/roundtrip_identity.c"
receipt="$PROMPT/build/integration-receipt.json"

out="$("$SCRIPT" --prompt "$PROMPT" --source-out "$source_out")"
printf '%s\n' "$out" | jq -e '.schema == "mizuchi.integration-receipt.v1" and .status == "integrated"' >/dev/null
[[ -f "$source_out" ]]
cmp -s "$PROMPT/candidate.c" "$source_out"
jq -e '.status == "integrated" and .sourceOut == "'"$source_out"'"' "$receipt" >/dev/null
jq -e '
  .schema == "mizuchi.build-and-verify.v1" and
  .status == "matched" and
  .byte_identical == true and
  .target_sha256 == .candidate_sha256
' "$PROMPT/build/build-and-verify.json" >/dev/null
ruby -ryaml - "$PROMPT/case.yaml" "$source_out" "$receipt" <<'RUBY'
path, source_out, receipt = ARGV
data = YAML.load_file(path)
abort "status not integrated" unless data["status"] == "integrated"
abort "source path not recorded" unless data["integratedSourcePath"] == source_out
abort "receipt path not recorded" unless data["integrationReceiptPath"] == receipt
RUBY

cat >"$PROMPT/candidate.c" <<'C'
int roundtrip_identity(int value) {
  return value * 3 + 8;
}
C
before_sha="$(sha256sum "$source_out" | awk '{print $1}')"
set +e
"$SCRIPT" --prompt "$PROMPT" --source-out "$source_out" >"$TMP_DIR/mismatch.out" 2>"$TMP_DIR/mismatch.err"
mismatch_rc=$?
set -e
[[ "$mismatch_rc" -eq 1 ]] || {
  echo "expected mismatched integration to exit 1, got $mismatch_rc" >&2
  cat "$TMP_DIR/mismatch.err" >&2 || true
  exit 1
}
grep -q "verification did not match; refusing integration" "$TMP_DIR/mismatch.err"
after_sha="$(sha256sum "$source_out" | awk '{print $1}')"
[[ "$before_sha" == "$after_sha" ]] || {
  echo "mismatched integration mutated destination" >&2
  exit 1
}

BLOCKED_PROMPT="$TMP_DIR/prompts/blocked"
mkdir -p "$BLOCKED_PROMPT"
cp "$PROMPT/settings.yaml" "$BLOCKED_PROMPT/settings.yaml"
cp "$PROMPT/prompt.md" "$BLOCKED_PROMPT/prompt.md"
cp "$PROMPT/target.c" "$BLOCKED_PROMPT/target.c"
cp "$PROMPT/candidate.c" "$BLOCKED_PROMPT/candidate.c"
ruby -ryaml - "$PROMPT/case.yaml" "$BLOCKED_PROMPT/case.yaml" <<'RUBY'
source, dest = ARGV
data = YAML.load_file(source)
data["status"] = "blocked"
data["blockedReason"] = "fixture blocked"
File.write(dest, data.to_yaml)
RUBY

set +e
"$SCRIPT" --prompt "$BLOCKED_PROMPT" --source-out "$TMP_DIR/blocked.c" >"$TMP_DIR/blocked.out" 2>"$TMP_DIR/blocked.err"
blocked_rc=$?
set -e
[[ "$blocked_rc" -eq 3 ]] || {
  echo "expected blocked integration to exit 3, got $blocked_rc" >&2
  cat "$TMP_DIR/blocked.err" >&2 || true
  exit 1
}
grep -q "integrate-verified-match: prompt is blocked: fixture blocked" "$TMP_DIR/blocked.err"

echo "ok"
