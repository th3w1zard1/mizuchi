#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

mkdir -p "$TMP_DIR/src" "$TMP_DIR/build"
printf 'int copied_fn(void) { return 7; }\n' >"$TMP_DIR/src/copied_fn.c"
printf 'preverified-object-bytes' >"$TMP_DIR/build/copied_fn.obj"

cat >"$TMP_DIR/build_manifest.json" <<JSON
{
  "schema": "reconkit.recovered-source-build-manifest.v1",
  "status": "complete",
  "units": [
    {
      "name": "copied_fn",
      "entry": "00401000",
      "verifiedObject": "$TMP_DIR/build/copied_fn.obj",
      "compilerProfileArgs": ["/unreachable-if-copy-works"]
    }
  ]
}
JSON

cat >"$TMP_DIR/simple_matches.manifest.json" <<JSON
{
  "schema": "reconkit.swkotor-recovered-source-shard.v1",
  "status": "complete",
  "buildManifest": "$TMP_DIR/build_manifest.json",
  "functionCount": 1,
  "functions": [
    {
      "name": "copied_fn",
      "entry": "00401000",
      "kind": "function",
      "exportedSource": "$TMP_DIR/src/copied_fn.c"
    }
  ]
}
JSON

"$ROOT/scripts/swkotor-compile-recovered-shard.py" \
  --manifest "$TMP_DIR/simple_matches.manifest.json" \
  --out-dir "$TMP_DIR/objects" \
  --summary "$TMP_DIR/compile-summary.json" \
  --vc-root "$TMP_DIR/missing-vc" \
  --wineprefix "$TMP_DIR/missing-wine" >/dev/null

cmp "$TMP_DIR/build/copied_fn.obj" "$TMP_DIR/objects/00401000_copied_fn.obj"

jq -e '
  .attempted == 1 and
  .compiled == 1 and
  .failed == 0 and
  .preverifiedCopyCount == 1 and
  .results[0].method == "preverified-copy"
' "$TMP_DIR/compile-summary.json" >/dev/null

echo "ok"
