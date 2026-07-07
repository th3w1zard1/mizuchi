#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/steam-roundtrip-run.py"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

STEAMAPPS="$TMP_DIR/steamapps"
APP_DIR="$STEAMAPPS/common/Resume Game"
OUT="$TMP_DIR/roundtrip"
WORKSPACE="$OUT/apps/456-resume-game"
mkdir -p "$APP_DIR" "$STEAMAPPS" "$WORKSPACE/existing"

cat >"$STEAMAPPS/appmanifest_456.acf" <<'ACF'
"AppState"
{
  "appid" "456"
  "name" "Resume Game"
  "installdir" "Resume Game"
}
ACF

cp /bin/true "$APP_DIR/ResumeGame"
printf 'asset bytes\n' >"$APP_DIR/asset.dat"

SOURCE_ONE="$WORKSPACE/existing/resume-game.S"
SOURCE_TWO="$WORKSPACE/existing/asset.S"
printf '/* existing verified byte source */\n' >"$SOURCE_ONE"
printf '/* existing verified byte source */\n' >"$SOURCE_TWO"

GAME_SHA="$(sha256sum "$APP_DIR/ResumeGame" | awk '{print $1}')"
ASSET_SHA="$(sha256sum "$APP_DIR/asset.dat" | awk '{print $1}')"
GAME_SIZE="$(stat -c '%s' "$APP_DIR/ResumeGame")"
ASSET_SIZE="$(stat -c '%s' "$APP_DIR/asset.dat")"

cat >"$WORKSPACE/source-roundtrip-manifest.json" <<JSON
{
  "schema": "mizuchi.app-source-roundtrip-manifest.v1",
  "app": "Resume Game",
  "appid": "456",
  "workspace": "$WORKSPACE",
  "sourceBundles": [],
  "rebuiltBinaries": [],
  "fullBinaryRoundtrips": [
    {
      "kind": "whole-binary-byte-source",
      "binary": "$APP_DIR/ResumeGame",
      "relativePath": "ResumeGame",
      "source": "$SOURCE_ONE",
      "blob": null,
      "object": null,
      "rebuiltBinary": null,
      "artifactMode": "lean",
      "byteIdentical": true,
      "originalSha256": "$GAME_SHA",
      "originalSize": $GAME_SIZE,
      "strategy": "byte-source-incbin",
      "sourceType": "byte-source",
      "sourceAuthority": "original-bytes",
      "semanticDecompilation": false
    },
    {
      "kind": "whole-binary-byte-source",
      "binary": "$APP_DIR/asset.dat",
      "relativePath": "asset.dat",
      "source": "$SOURCE_TWO",
      "blob": null,
      "object": null,
      "rebuiltBinary": null,
      "artifactMode": "lean",
      "byteIdentical": true,
      "originalSha256": "$ASSET_SHA",
      "originalSize": $ASSET_SIZE,
      "strategy": "byte-source-incbin",
      "sourceType": "byte-source",
      "sourceAuthority": "original-bytes",
      "semanticDecompilation": false
    }
  ],
  "fullBinarySourceMode": "all-files",
  "fullBinaryArtifactMode": "lean",
  "appFileRoundtripExpected": 2,
  "appFileRoundtripTotal": 2,
  "appFileRoundtripBytes": $((GAME_SIZE + ASSET_SIZE)),
  "appFileRoundtripMatched": 2,
  "appFileRoundtripSkipped": [],
  "appFilesByteIdentical": true,
  "matchedElfFunctions": 0,
  "matchedPeExportFunctions": 0,
  "matchedFunctions": 0,
  "primaryBinaryByteIdentical": true,
  "fullAppByteIdentical": true
}
JSON

report_skip="$("$SCRIPT" \
  --steamapps "$STEAMAPPS" \
  --app "resume game" \
  --out "$OUT" \
  --full-binary-source-mode all-files \
  --full-binary-artifact-mode lean \
  --semantic-match-mode never \
  --skip-existing-full-app)"
printf '%s\n' "$report_skip" | jq -e '.skippedExistingFullApps == 1' >/dev/null
printf '%s\n' "$report_skip" | jq -e '.apps[0].status == "skipped-existing-full-app"' >/dev/null
printf '%s\n' "$report_skip" | jq -e '.fullAppByteIdentical == 1' >/dev/null

rm "$SOURCE_ONE"

report_rerun="$("$SCRIPT" \
  --steamapps "$STEAMAPPS" \
  --app "resume game" \
  --out "$OUT" \
  --full-binary-source-mode all-files \
  --full-binary-artifact-mode lean \
  --semantic-match-mode never \
  --skip-existing-full-app)"
printf '%s\n' "$report_rerun" | jq -e '.skippedExistingFullApps == 0' >/dev/null
printf '%s\n' "$report_rerun" | jq -e '.apps[0].status != "skipped-existing-full-app"' >/dev/null
printf '%s\n' "$report_rerun" | jq -e '.appFileRoundtripExpected == 2 and .appFileRoundtripMatched == 2' >/dev/null
printf '%s\n' "$report_rerun" | jq -e '.fullAppByteIdentical == 1' >/dev/null

echo "ok"
