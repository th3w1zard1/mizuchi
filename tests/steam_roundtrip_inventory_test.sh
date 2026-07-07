#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/steam-roundtrip-inventory.py"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

STEAMAPPS="$TMP_DIR/steamapps"
APP_DIR="$STEAMAPPS/common/Fake Game"
mkdir -p "$APP_DIR/redist" "$STEAMAPPS"

cat >"$STEAMAPPS/appmanifest_123.acf" <<'ACF'
"AppState"
{
  "appid" "123"
  "name" "Fake Game"
  "installdir" "Fake Game"
}
ACF

ln -s "$TMP_DIR/vanished.acf" "$STEAMAPPS/appmanifest_999.acf"

cp /bin/true "$APP_DIR/FakeGame.exe"
cp /bin/true "$APP_DIR/redist/a_support.dll"
printf 'int add(int a, int b) { return a + b; }\n' >"$APP_DIR/example.c"

REPORT="$TMP_DIR/report.json"
WORKSPACES="$TMP_DIR/workspaces"
out="$("$SCRIPT" --steamapps "$STEAMAPPS" --out "$REPORT" --emit-workspaces "$WORKSPACES" --json)"

printf '%s\n' "$out" | jq -e '.schema == "mizuchi.steam-roundtrip-inventory.v1"' >/dev/null
printf '%s\n' "$out" | jq -e '.app_count == 1' >/dev/null
printf '%s\n' "$out" | jq -e '.matched_count == 0' >/dev/null
printf '%s\n' "$out" | jq -e '.all_byte_identical == false' >/dev/null
printf '%s\n' "$out" | jq -e '.apps[0].roundtrip_evidence.primaryTarget.path == "FakeGame.exe"' >/dev/null
printf '%s\n' "$out" | jq -e '.apps[0].roundtrip_evidence.byteIdentical == false' >/dev/null
printf '%s\n' "$out" | jq -e '.apps[0].roundtrip_evidence.missingInputs | index("matching compiler and linker versions")' >/dev/null
cmp -s "$REPORT" <(printf '%s\n' "$out")
[[ -f "$WORKSPACES/123-fake-game/manifest.json" ]]
[[ -f "$WORKSPACES/123-fake-game/README.md" ]]
[[ -f "$WORKSPACES/123-fake-game/original.sha256" ]]
grep -q 'FakeGame.exe' "$WORKSPACES/123-fake-game/README.md"
jq -e '.app.roundtrip_evidence.primaryTarget.path == "FakeGame.exe"' "$WORKSPACES/123-fake-game/manifest.json" >/dev/null

echo "ok"
