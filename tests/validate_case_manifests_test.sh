#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

mkdir -p "$work_dir/scripts/lib" "$work_dir/prompts/honest"
cp "$ROOT/scripts/validate-case-manifests.sh" "$work_dir/scripts/"
cp "$ROOT/scripts/lib/prompt-settings.sh" "$work_dir/scripts/lib/"
cp "$ROOT/scripts/lib/case-manifest.sh" "$work_dir/scripts/lib/"
chmod +x "$work_dir/scripts/validate-case-manifests.sh"

cat >"$work_dir/prompts/honest/settings.yaml" <<'EOF'
functionName: FUN_12345678
targetObjectPath: build/obj/FUN_12345678.o
asm: |
  /* glabel FUN_12345678 */
EOF

cat >"$work_dir/prompts/honest/case.yaml" <<'EOF'
schemaVersion: 1
caseId: honest
target:
  family: odyssey
  binary: /TSL/example.xbe
  platform: xbox
symbol:
  name: FUN_12345678
  locator: "0x12345678"
proof:
  targetObjectPath: build/obj/FUN_12345678.o
workspace:
  promptPath: prompts/honest
  buildDir: build
EOF

out="$( (cd "$work_dir" && ./scripts/validate-case-manifests.sh --quiet) )"
[[ "$out" == "CASE_MANIFESTS_OK" ]]

mkdir -p "$work_dir/prompts/missing"
cat >"$work_dir/prompts/missing/settings.yaml" <<'EOF'
functionName: FUN_MISSING
targetObjectPath: build/obj/FUN_MISSING.o
asm: |
  /* glabel FUN_MISSING */
EOF

set +e
(cd "$work_dir" && ./scripts/validate-case-manifests.sh --quiet >/dev/null 2>&1)
missing_status=$?
set -e
[[ "$missing_status" -ne 0 ]]

rm -rf "$work_dir/prompts/missing"
cat >"$work_dir/prompts/honest/case.yaml" <<'EOF'
schemaVersion: 1
caseId: honest
target:
  family: odyssey
  binary: /TSL/example.xbe
  platform: xbox
symbol:
  name: FUN_WRONG
  locator: "0x12345678"
proof:
  targetObjectPath: build/obj/FUN_12345678.o
workspace:
  promptPath: prompts/honest
  buildDir: build
EOF

set +e
(cd "$work_dir" && ./scripts/validate-case-manifests.sh --quiet >/dev/null 2>&1)
mismatch_status=$?
set -e
[[ "$mismatch_status" -ne 0 ]]

echo "validate_case_manifests_test: ok"
