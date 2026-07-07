#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BINARY="/run/media/brunner56/MyBook/SteamLibrary/steamapps/common/vvvvvv/VVVVVV"
BIOSHOCK_BINARY="/run/media/brunner56/MyBook/SteamLibrary/steamapps/common/BioShock Infinite/libsteam_api.so"
KOTOR_BINK="/run/media/brunner56/MyBook/SteamLibrary/steamapps/common/swkotor/binkw32.dll"

if [[ ! -f "$BINARY" ]]; then
  echo "skip: missing VVVVVV binary: $BINARY"
  exit 0
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

report="$("$ROOT/scripts/steam-roundtrip-run.py" \
  --app vvvvvv \
  --matcher-timeout 180 \
  --out "$TMP_DIR/roundtrip")"

printf '%s\n' "$report" | jq -e '.schema == "mizuchi.steam-roundtrip-run.v1"' >/dev/null
printf '%s\n' "$report" | jq -e '.appCount == 1' >/dev/null
printf '%s\n' "$report" | jq -e '.eligibleApps == 1' >/dev/null
printf '%s\n' "$report" | jq -e '.matcherRuns == 1' >/dev/null
printf '%s\n' "$report" | jq -e '.matcherTimeoutSeconds == 180' >/dev/null
printf '%s\n' "$report" | jq -e '.matchedFunctions >= 1' >/dev/null
printf '%s\n' "$report" | jq -e '.fullBinarySourceMode == "primary"' >/dev/null
printf '%s\n' "$report" | jq -e '.fullBinaryRoundtrips >= 1' >/dev/null
printf '%s\n' "$report" | jq -e '.primaryBinaryByteIdentical >= 1' >/dev/null
printf '%s\n' "$report" | jq -e '.fullAppByteIdentical == 0' >/dev/null

APP_DIR="$(printf '%s\n' "$report" | jq -r '.apps[0].workspace')"
[[ -f "$TMP_DIR/roundtrip/roundtrip-run.json" ]]
[[ -f "$APP_DIR/matched-functions.json" ]]
[[ -f "$APP_DIR/source-roundtrip-manifest.json" ]]
jq -e '(.matchedFunctions | length) >= 1' "$APP_DIR/matched-functions.json" >/dev/null
jq -e '.aggregateSourceRoundtrip.byteIdentical == true' "$APP_DIR/matched-functions.json" >/dev/null
jq -e '.sourceBundles[] | select(.kind == "elf-functions" and .byteIdentical == true and .matchedSymbols >= 1)' \
  "$APP_DIR/source-roundtrip-manifest.json" >/dev/null
jq -e '.fullAppByteIdentical == false' "$APP_DIR/source-roundtrip-manifest.json" >/dev/null
jq -e '.primaryBinaryByteIdentical == true' "$APP_DIR/source-roundtrip-manifest.json" >/dev/null
jq -e '.fullBinaryRoundtrips[] | select(.kind == "whole-binary-byte-source" and .byteIdentical == true)' \
  "$APP_DIR/source-roundtrip-manifest.json" >/dev/null
manifest_verify="$("$ROOT/scripts/decomp-cli.sh" steam-roundtrip-verify-manifest \
  --manifest "$APP_DIR/source-roundtrip-manifest.json" \
  --out "$TMP_DIR/verify-vvvvvv-manifest")"
printf '%s\n' "$manifest_verify" | jq -e '.schema == "mizuchi.app-source-roundtrip-verify.v1"' >/dev/null
printf '%s\n' "$manifest_verify" | jq -e '.byteIdentical == true and .matchedSymbols >= 1' >/dev/null
printf '%s\n' "$manifest_verify" | jq -e '.fullBinaryByteIdentical >= 1' >/dev/null

report_all_files="$("$ROOT/scripts/steam-roundtrip-run.py" \
  --app vvvvvv \
  --full-binary-source-mode all-files \
  --matcher-timeout 180 \
  --out "$TMP_DIR/roundtrip-vvvvvv-all-files")"
printf '%s\n' "$report_all_files" | jq -e '.appCount == 1' >/dev/null
printf '%s\n' "$report_all_files" | jq -e '.fullBinarySourceMode == "all-files"' >/dev/null
printf '%s\n' "$report_all_files" | jq -e '.appFileRoundtripTotal >= 1' >/dev/null
printf '%s\n' "$report_all_files" | jq -e '.appFileRoundtripExpected == .appFileRoundtripTotal' >/dev/null
printf '%s\n' "$report_all_files" | jq -e '.appFileRoundtripMatched == .appFileRoundtripTotal' >/dev/null
printf '%s\n' "$report_all_files" | jq -e '.fullAppByteIdentical == 1' >/dev/null
printf '%s\n' "$report_all_files" | jq -e '.primaryBinaryByteIdentical == 1' >/dev/null
APP_ALL_FILES_DIR="$(printf '%s\n' "$report_all_files" | jq -r '.apps[0].workspace')"
jq -e '.fullAppByteIdentical == true' "$APP_ALL_FILES_DIR/source-roundtrip-manifest.json" >/dev/null
jq -e '.appFilesByteIdentical == true' "$APP_ALL_FILES_DIR/source-roundtrip-manifest.json" >/dev/null
jq -e '.appFileRoundtripExpected == .appFileRoundtripTotal' "$APP_ALL_FILES_DIR/source-roundtrip-manifest.json" >/dev/null
jq -e '.appFileRoundtripMatched == .appFileRoundtripTotal' "$APP_ALL_FILES_DIR/source-roundtrip-manifest.json" >/dev/null
manifest_verify_all_files="$("$ROOT/scripts/decomp-cli.sh" steam-roundtrip-verify-manifest \
  --manifest "$APP_ALL_FILES_DIR/source-roundtrip-manifest.json" \
  --out "$TMP_DIR/verify-vvvvvv-all-files-manifest" \
  --timeout 180)"
printf '%s\n' "$manifest_verify_all_files" | jq -e '.byteIdentical == true and .fullAppByteIdentical == true' >/dev/null
printf '%s\n' "$manifest_verify_all_files" | jq -e '.appFileRoundtripMatched == .appFileRoundtripTotal' >/dev/null
report_all_files_resume="$("$ROOT/scripts/steam-roundtrip-run.py" \
  --app vvvvvv \
  --full-binary-source-mode all-files \
  --skip-existing-full-app \
  --matcher-timeout 180 \
  --out "$TMP_DIR/roundtrip-vvvvvv-all-files")"
printf '%s\n' "$report_all_files_resume" | jq -e '.skippedExistingFullApps == 1' >/dev/null
printf '%s\n' "$report_all_files_resume" | jq -e '.apps[0].status == "skipped-existing-full-app"' >/dev/null
printf '%s\n' "$report_all_files_resume" | jq -e '.fullAppByteIdentical == 1' >/dev/null

report_lean="$("$ROOT/scripts/steam-roundtrip-run.py" \
  --app vvvvvv \
  --full-binary-source-mode all-files \
  --full-binary-artifact-mode lean \
  --full-binary-max-files 3 \
  --matcher-timeout 180 \
  --out "$TMP_DIR/roundtrip-vvvvvv-lean")"
printf '%s\n' "$report_lean" | jq -e '.fullBinaryArtifactMode == "lean"' >/dev/null
printf '%s\n' "$report_lean" | jq -e '.appFileRoundtripExpected == 3 and .appFileRoundtripMatched == 3' >/dev/null
printf '%s\n' "$report_lean" | jq -e '.fullAppByteIdentical == 0' >/dev/null
APP_LEAN_DIR="$(printf '%s\n' "$report_lean" | jq -r '.apps[0].workspace')"
jq -e '.fullBinaryArtifactMode == "lean"' "$APP_LEAN_DIR/source-roundtrip-manifest.json" >/dev/null
jq -e '.fullBinaryRoundtrips[] | select(.artifactMode == "lean" and .blob == null and .object == null and .rebuiltBinary == null and .byteIdentical == true)' \
  "$APP_LEAN_DIR/source-roundtrip-manifest.json" >/dev/null
manifest_verify_lean="$("$ROOT/scripts/decomp-cli.sh" steam-roundtrip-verify-manifest \
  --manifest "$APP_LEAN_DIR/source-roundtrip-manifest.json" \
  --out "$TMP_DIR/verify-vvvvvv-lean-manifest" \
  --timeout 180)"
printf '%s\n' "$manifest_verify_lean" | jq -e '.fullBinaryByteIdentical == 3 and .fullAppByteIdentical == false' >/dev/null
printf '%s\n' "$manifest_verify_lean" | jq -e '([.fullBinaries[].artifactMode] | unique) == ["lean"]' >/dev/null

report_batch="$("$ROOT/scripts/steam-roundtrip-run.py" \
  --app vvvvvv \
  --full-binary-source-mode all-files \
  --full-binary-artifact-mode lean \
  --full-binary-runner app-batch \
  --full-binary-max-files 3 \
  --semantic-match-mode never \
  --matcher-timeout 180 \
  --out "$TMP_DIR/roundtrip-vvvvvv-batch")"
printf '%s\n' "$report_batch" | jq -e '.fullBinaryRunner == "app-batch" and .semanticMatchMode == "never"' >/dev/null
printf '%s\n' "$report_batch" | jq -e '.appFileRoundtripExpected == 3 and .appFileRoundtripMatched == 3' >/dev/null
APP_BATCH_DIR="$(printf '%s\n' "$report_batch" | jq -r '.apps[0].workspace')"
jq -e '([.fullBinaryRoundtrips[].sectionName] | length) == 3' "$APP_BATCH_DIR/source-roundtrip-manifest.json" >/dev/null
manifest_verify_batch="$("$ROOT/scripts/decomp-cli.sh" steam-roundtrip-verify-manifest \
  --manifest "$APP_BATCH_DIR/source-roundtrip-manifest.json" \
  --out "$TMP_DIR/verify-vvvvvv-batch-manifest" \
  --timeout 180)"
printf '%s\n' "$manifest_verify_batch" | jq -e '.fullBinaryByteIdentical == 3' >/dev/null
printf '%s\n' "$manifest_verify_batch" | jq -e '([.fullBinaries[] | select(.sectionName != null)] | length) == 3' >/dev/null

if [[ -f "$BIOSHOCK_BINARY" ]]; then
  report_bioshock="$("$ROOT/scripts/steam-roundtrip-run.py" --app bioshock --out "$TMP_DIR/roundtrip-bioshock")"
  printf '%s\n' "$report_bioshock" | jq -e '.eligibleApps >= 1' >/dev/null
  printf '%s\n' "$report_bioshock" | jq -e '.matchedElfFunctions >= 3' >/dev/null
  printf '%s\n' "$report_bioshock" | jq -e '.apps[] | select(.name == "BioShock Infinite" and .matchedElfFunctions >= 3)' >/dev/null
  BIOSHOCK_APP_DIR="$(printf '%s\n' "$report_bioshock" | jq -r '.apps[] | select(.name == "BioShock Infinite") | .workspace')"
  jq -e '.aggregateSourceRoundtrip.byteIdentical == true and .aggregateSourceRoundtrip.matchedSymbols >= 3' \
    "$BIOSHOCK_APP_DIR/matched-functions.json" >/dev/null
fi

if [[ -f "$KOTOR_BINK" ]]; then
  report_kotor="$("$ROOT/scripts/steam-roundtrip-run.py" --app "knights of the old republic" --max-pe-binaries 12 --out "$TMP_DIR/roundtrip-kotor")"
  printf '%s\n' "$report_kotor" | jq -e '.peExportMatcherRuns >= 1' >/dev/null
  printf '%s\n' "$report_kotor" | jq -e '.matchedPeExportFunctions >= 3' >/dev/null
  printf '%s\n' "$report_kotor" | jq -e '.apps[] | select(.name == "STAR WARS™ Knights of the Old Republic™" and .matchedPeExportFunctions >= 3)' >/dev/null
  KOTOR_APP_DIR="$(printf '%s\n' "$report_kotor" | jq -r '.apps[] | select(.name == "STAR WARS™ Knights of the Old Republic™") | .workspace')"
  [[ -f "$KOTOR_APP_DIR/source-roundtrip-manifest.json" ]]
  jq -e '.binaries[].matches[] | select(.candidateObjectFormat == "coff" and .byteIdentical == true)' \
    "$KOTOR_APP_DIR/matched-pe-export-functions.json" >/dev/null
  jq -e '.binaries[] | select(.aggregateSourceRoundtrip.byteIdentical == true and .aggregateSourceRoundtrip.matchedSymbols >= 3)' \
    "$KOTOR_APP_DIR/matched-pe-export-functions.json" >/dev/null
  jq -e '.binaries[] | select(.aggregateSourceRoundtrip.rebuiltDllRoundtrip.byteIdenticalExports == true and .aggregateSourceRoundtrip.rebuiltDllRoundtrip.matchedSymbols >= 3)' \
    "$KOTOR_APP_DIR/matched-pe-export-functions.json" >/dev/null
  jq -e '.sourceBundles[] | select(.kind == "pe-exports" and .byteIdentical == true and .matchedSymbols >= 3)' \
    "$KOTOR_APP_DIR/source-roundtrip-manifest.json" >/dev/null
  jq -e '.rebuiltBinaries[] | select(.kind == "pe-export-dll" and .byteIdenticalExports == true and .matchedSymbols >= 3)' \
    "$KOTOR_APP_DIR/source-roundtrip-manifest.json" >/dev/null
  jq -e '.primaryBinaryByteIdentical == true' "$KOTOR_APP_DIR/source-roundtrip-manifest.json" >/dev/null
  kotor_manifest_verify="$("$ROOT/scripts/decomp-cli.sh" steam-roundtrip-verify-manifest \
    --manifest "$KOTOR_APP_DIR/source-roundtrip-manifest.json" \
    --out "$TMP_DIR/verify-kotor-manifest")"
  printf '%s\n' "$kotor_manifest_verify" | jq -e '.byteIdentical == true and .matchedSymbols >= 3' >/dev/null
  printf '%s\n' "$kotor_manifest_verify" | jq -e '.fullBinaryByteIdentical >= 1' >/dev/null

  report_kotor_fast="$("$ROOT/scripts/steam-roundtrip-run.py" --app "knights of the old republic" --max-pe-binaries 12 --pe-rebuild-mode never --out "$TMP_DIR/roundtrip-kotor-fast")"
  printf '%s\n' "$report_kotor_fast" | jq -e '.peRebuildMode == "never"' >/dev/null
  printf '%s\n' "$report_kotor_fast" | jq -e '.matchedPeExportFunctions >= 3' >/dev/null
  KOTOR_FAST_APP_DIR="$(printf '%s\n' "$report_kotor_fast" | jq -r '.apps[] | select(.name == "STAR WARS™ Knights of the Old Republic™") | .workspace')"
  jq -e '.binaries[] | select(.aggregateSourceRoundtrip.rebuiltDllRoundtrip.status == "skipped" and .matchedFunctions >= 3)' \
    "$KOTOR_FAST_APP_DIR/matched-pe-export-functions.json" >/dev/null
  jq -e '.rebuiltBinaries[] | select(.status == "skipped" and .matchedSymbols >= 3)' \
    "$KOTOR_FAST_APP_DIR/source-roundtrip-manifest.json" >/dev/null
fi

echo "ok"
