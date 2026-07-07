#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${MIZUCHI_KOTOR_BINK_DLL:-/run/media/brunner56/MyBook/SteamLibrary/steamapps/common/swkotor/binkw32.dll}"
VC_ROOT="${VC_ROOT:-/run/media/brunner56/MyBook/MizuchiSource/toolchains/msvc8.0-main}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

if [[ ! -f "$TARGET" ]]; then
  echo "skip: KOTOR binkw32.dll not found at $TARGET"
  exit 0
fi
if [[ ! -f "$VC_ROOT/bin/cl.exe" ]]; then
  echo "skip: MSVC cl.exe not found at $VC_ROOT/bin/cl.exe"
  exit 0
fi
if ! command -v wine >/dev/null 2>&1; then
  echo "skip: wine not found"
  exit 0
fi

VC_ROOT="$VC_ROOT" "$ROOT/scripts/decomp-cli.sh" recover "$TARGET" \
  --work-dir "$TMP_DIR/recover" \
  --source-task-limit 560 \
  --source-synthesis none \
  --stop-after plan-strategy \
  --context-max-files 20 \
  --context-max-depth 1 \
  --context-strings-limit 20 \
  --function-analysis none >/dev/null

jq -e '.generatedSourceCandidates == 462 and .semanticSourceCandidates == 410 and .nonSemanticBootstrapCandidates == 52 and .byStatus["generated-unverified"] == 462 and .byStatus["not-generated-fragment"] == 4' "$TMP_DIR/recover/source-generation/summary.json" >/dev/null
jq -e '.highLevelSourceCandidates == 22 and .inlineAsmSourceCandidates == 61 and .byteEmissionSourceCandidates == 327 and .generatedByLanguage == {"asm": 52, "c": 84, "masm": 326} and .semanticByLanguage == {"c": 84, "masm": 326} and .generatedBySourceQuality == {"byte-emission-asm": 327, "high-level-c": 22, "inline-asm-c": 61, "nonsemantic-bootstrap": 52}' "$TMP_DIR/recover/source-generation/summary.json" >/dev/null
jq -e '.uniqueCandidateAddresses == 471 and .duplicateAddressAliasTasks == 41 and .addressAliasArtifacts.aliasGroups == 41 and .addressAliasArtifacts.duplicateAddressTasks == 41 and (.addressAliasArtifacts.groups | test("address-alias-groups.json$"))' "$TMP_DIR/recover/source-generation/summary.json" >/dev/null
jq -e '.aliasGroups == 41 and .duplicateAddressTasks == 41 and (.groups | length) == 41 and (.groups[] | select(.canonicalAddress == "0x30001020" and .canonicalName == "_radmalloc@4" and .aliasCount == 2 and ([.aliases[].name] | sort) == ["_radmalloc@4", "sub_1020"]))' "$TMP_DIR/recover/source-generation/artifacts/address-alias-groups.json" >/dev/null
jq -e '.sourceCoverageArtifacts.semanticGeneratedRatio == 0.887446 and .generatedByRule["target-slice-asm-bootstrap"] == 52 and .semanticByRule["extended-terminal-body-masm"] == 23 and .semanticByRule["bounded-leading-return-slice-masm"] == 124 and .semanticByRule["bounded-import-call-terminal-masm"] == 6 and .semanticByRule["bounded-direct-call-terminal-masm"] == 34 and .semanticByRule["bounded-terminal-leaf-masm"] == 21 and .semanticByRule["packed-leading-function-masm"] == 37 and .semanticByRule["bink-buffer-check-win-pos-forwarder"] == 1 and .semanticByRule["bink-buffer-clear-forwarder"] == 1 and .semanticByRule["bink-buffer-close-forwarder"] == 1 and .semanticByRule["bink-buffer-get-description-forwarder"] == 1 and .semanticByRule["bink-buffer-lock-forwarder"] == 2 and .semanticByRule["bink-buffer-set-direct-draw-forwarder"] == 1 and .semanticByRule["bink-buffer-set-offset-forwarder"] == 2 and .semanticByRule["bink-buffer-set-scale-forwarder"] == 2 and .semanticByRule["bink-buffer-unlock-forwarder"] == 2 and .semanticByRule["bink-check-cursor-forwarder"] == 2 and .semanticByRule["bink-close-track-forwarder"] == 1 and .semanticByRule["bink-copy-to-buffer-forwarder"] == 1 and .semanticByRule["bink-get-key-frame-forwarder"] == 2 and .semanticByRule["bink-get-realtime-forwarder"] == 1 and .semanticByRule["bink-goto-forwarder"] == 1 and .semanticByRule["bink-get-summary-forwarder"] == 1 and .semanticByRule["bink-close-forwarder"] == 1 and .semanticByRule["bink-wait-forwarder"] == 1 and .semanticByRule["short-direct-call-ret-masm"] == 7 and .semanticByRule["compact-terminal-ret-masm"] == 9 and .semanticByRule["compact-import-call-ret-masm"] == 17 and .semanticByRule["bink-next-frame-forwarder"] == 2 and .semanticByRule["bink-open-track-forwarder"] == 1 and .semanticByRule["bink-surface-type-forwarder"] == 3 and .semanticByRule["rad-aligned-malloc-forwarder"] == 2 and .semanticByRule["rad-aligned-free-forwarder"] == 1 and .semanticByRule["rad-direct-free-wrapper"] == 1 and .semanticByRule["rad-timer-read-forwarder"] == 2 and .semanticByRule["bink-pause-forwarder"] == 2 and .semanticByRule["ebx-bitfield-mode-remap"] == 2 and .semanticByRule["ecx-global-cmp-return-else-tailjmp"] == 1 and .semanticByRule["global-callback-nonzero-return-one"] == 1 and .semanticByRule["global-guard-call-set-return-zero"] == 1 and .semanticByRule["global-setter-u32-stdcall"] == 3 and .semanticByRule["global-two-cmp-return-1-or-3"] == 1 and .semanticByRule["import-tail-jump"] == 1 and .semanticByRule["live-eax-nullable-import-tailjmp-stdcall4"] == 1 and .semanticByRule["nullable-field-setter-u32-stdcall8"] == 1 and .semanticByRule["nullable-indexed-field-array-getter-stdcall8"] == 3 and .semanticByRule["push-const-call-wrapper"] == 2 and .semanticByRule["push-global-call-wrapper"] == 1 and .semanticByRule["push-imm32-pair-call-wrapper"] == 1 and .semanticByRule["push-stack-stack-const-call-wrapper"] == 2 and .semanticByRule["rep-stos-global-clear"] == 1 and .semanticByRule["return-immediate-cdecl"] == 3 and .semanticByRule["return-immediate-stdcall"] == 1 and .semanticByRule["return-zero-stdcall"] == 1 and .semanticByRule["small-copy-loop"] == 1 and .semanticByRule["small-zero-scan-bool"] == 1 and .semanticByRule["stack-arg-range-global-mode-setter"] == 1 and .semanticByRule["stdcall-clamped-count-copy-to-global"] == 1 and .semanticByRule["stdcall-global-callback-install"] == 1 and .semanticByRule["stdcall-indirect-global-callback-loop"] == 2 and .semanticByRule["stdcall-nullable-field-tailjmp"] == 1 and .semanticByRule["stdcall-store-three-stack-args-to-globals"] == 1 and .semanticByRule["stdcall-store-two-stack-args-to-globals"] == 2 and .semanticByRule["stdcall-track-method-forwarder"] == 4 and .semanticByRule["stdcall-yuv-blit-alpha-wrapper"] == 8 and .semanticByRule["stdcall-yuv-blit-format-wrapper"] == 10 and .semanticByRule["stdcall-yuv-blit-mask-alpha-prefix"] == 8 and .semanticByRule["stdcall-yuv-blit-mask-format-prefix"] == 14 and .semanticByRule["stdcall-yuv-blit-packed-wrapper"] == 4 and .semanticByRule["u32-add-store-wrap-flag"] == 1 and .semanticByRule["u96-bit-tail-clear-check"] == 1 and .semanticByRule["u96-left-shift-one"] == 1 and .semanticByRule["x87-control-word-masked-setter"] == 1 and .semanticByRule["x87-double-exponent-adjust-return"] == 1 and .semanticByRule["x87-round-stack-double-return"] == 1 and .semanticByRule["x87-temp-i16-return"] == 2' "$TMP_DIR/recover/source-generation/summary.json" >/dev/null
jq -e '.semanticGeneratedRatio == 0.887446 and .highLevelGeneratedRatio == 0.047619 and .highLevelSemanticRatio == 0.053659 and .highLevelSourceCandidates == 22 and .inlineAsmSourceCandidates == 61 and .byteEmissionSourceCandidates == 327 and .generatedBySourceQuality == {"byte-emission-asm": 327, "high-level-c": 22, "inline-asm-c": 61, "nonsemantic-bootstrap": 52} and (.topNonsemanticSlices | length) >= 1 and .topNonsemanticSlices[0].generatorRule == "boundary-fragment" and (.generatorOpportunities["bink-buffer-check-win-pos-forwarder"] | not) and (.generatorOpportunities["bink-buffer-clear-forwarder"] | not) and (.generatorOpportunities["bink-buffer-close-forwarder"] | not) and (.generatorOpportunities["bink-buffer-get-description-forwarder"] | not) and (.generatorOpportunities["bink-buffer-lock-forwarder"] | not) and (.generatorOpportunities["bink-buffer-set-direct-draw-forwarder"] | not) and (.generatorOpportunities["bink-buffer-set-offset-forwarder"] | not) and (.generatorOpportunities["bink-buffer-set-scale-forwarder"] | not) and (.generatorOpportunities["bink-buffer-unlock-forwarder"] | not) and (.generatorOpportunities["bink-check-cursor-forwarder"] | not) and (.generatorOpportunities["bink-close-track-forwarder"] | not) and (.generatorOpportunities["bink-copy-to-buffer-forwarder"] | not) and (.generatorOpportunities["bink-get-key-frame-forwarder"] | not) and (.generatorOpportunities["bink-get-realtime-forwarder"] | not) and (.generatorOpportunities["bink-goto-forwarder"] | not) and (.generatorOpportunities["bink-get-summary-forwarder"] | not) and (.generatorOpportunities["bink-close-forwarder"] | not) and (.generatorOpportunities["bink-wait-forwarder"] | not) and (.generatorOpportunities["short-direct-call-ret-masm"] | not) and (.generatorOpportunities["compact-terminal-ret-masm"] | not) and (.generatorOpportunities["compact-import-call-ret-masm"] | not) and (.generatorOpportunities["bink-next-frame-forwarder"] | not) and (.generatorOpportunities["bink-open-track-forwarder"] | not) and (.generatorOpportunities["bink-surface-type-forwarder"] | not) and (.generatorOpportunities["rad-aligned-malloc-forwarder"] | not) and (.generatorOpportunities["rad-aligned-free-forwarder"] | not) and (.generatorOpportunities["rad-direct-free-wrapper"] | not) and (.generatorOpportunities["rad-timer-read-forwarder"] | not) and (.generatorOpportunities["bink-pause-forwarder"] | not) and (.generatorOpportunities["ebx-bitfield-mode-remap"] | not) and (.generatorOpportunities["ecx-global-cmp-return-else-tailjmp"] | not) and (.generatorOpportunities["global-guard-return-zero"] | not) and (.generatorOpportunities["import-tail-jump"] | not) and (.generatorOpportunities["live-eax-nullable-import-tailjmp-stdcall4"] | not) and (.generatorOpportunities["push-const-call-wrapper"] | not) and (.generatorOpportunities["push-global-call-wrapper"] | not) and (.generatorOpportunities["push-imm32-pair-call-wrapper"] | not) and (.generatorOpportunities["push-stack-stack-const-call-wrapper"] | not) and (.generatorOpportunities["rep-stos-global-clear"] | not) and (.generatorOpportunities["small-zero-scan-bool"] | not) and (.generatorOpportunities["small-copy-loop"] | not) and (.generatorOpportunities["stack-arg-range-global-mode-setter"] | not) and (.generatorOpportunities["u32-add-store-wrap-flag"] | not) and (.generatorOpportunities["u96-bit-tail-clear-check"] | not) and (.generatorOpportunities["u96-left-shift-one"] | not) and (.generatorOpportunities["x87-control-word-masked-setter"] | not) and (.generatorOpportunities["x87-double-exponent-adjust-return"] | not) and (.generatorOpportunities["x87-pop-return-zero"] | not) and (.generatorOpportunities["x87-round-stack-double-return"] | not) and (.generatorOpportunities["x87-temp-i16-return"] | not) and (.generatorOpportunities["nullable-field-setter-u32-stdcall8"] | not) and (.generatorOpportunities["stdcall-store-three-stack-args-to-globals"] | not) and (.generatorOpportunities["stdcall-copy-cstr-to-global"] | not) and (.generatorOpportunities["stdcall-indirect-global-callback-loop"] | not) and (.generatorOpportunities["stdcall-nullable-field-tailjmp"] | not) and (.generatorOpportunities["stdcall-clamped-count-copy-to-global"] | not) and (.generatorOpportunities["stdcall-global-callback-install"] | not) and (.generatorOpportunities["stdcall-track-method-forwarder"] | not) and (.generatorOpportunities["stdcall-yuv-blit-alpha-wrapper"] | not) and (.generatorOpportunities["stdcall-yuv-blit-format-wrapper"] | not) and (.generatorOpportunities["stdcall-yuv-blit-mask-alpha-prefix"] | not) and (.generatorOpportunities["stdcall-yuv-blit-mask-format-prefix"] | not) and (.generatorOpportunities["stdcall-yuv-blit-packed-wrapper"] | not) and (.generatorOpportunities["bounded-leading-return-slice-masm"] | not) and (.generatorOpportunities["extended-terminal-body-masm"] | not) and (.generatorOpportunities["tail-fragment"] | not) and .generatorOpportunities["boundary-fragment"].count == 4 and .generatorOpportunities["unknown"].count == 13 and .generatorOpportunities["call-bearing"].count == 39 and (.generatorOpportunities["multi-function-packed-slice"] | not)' "$TMP_DIR/recover/source-generation/artifacts/semantic-coverage.json" >/dev/null
jq -e '.boundaryRepair.fragmentCount == 4 and .boundaryRepair.appliedRepairCount == 45 and .boundaryRepair.countsByFragmentClass["boundary-fragment"] == 4 and (.boundaryRepair.countsByFragmentClass["tail-fragment"] | not) and .boundaryRepair.countsByRecommendedRepair["merge-with-adjacent-boundary-fragment"] == 4 and (.boundaryRepair.countsByRecommendedRepair["prepend-to-previous-function-tail"] | not) and (.boundaryRepair.appliedRepairs | test("applied-boundary-repairs.jsonl$")) and (.boundaryRepair.manifest | test("boundary-repair-manifest.jsonl$")) and (.boundaryRepair.summary | test("boundary-repair-summary.json$"))' "$TMP_DIR/recover/source-generation/artifacts/semantic-coverage.json" >/dev/null
jq -e '.sourceCoverageArtifacts.boundaryRepair.fragmentCount == 4 and .sourceCoverageArtifacts.boundaryRepair.appliedRepairCount == 45 and .sourceCoverageArtifacts.boundaryRepair.countsByFragmentClass["boundary-fragment"] == 4 and (.sourceCoverageArtifacts.boundaryRepair.countsByFragmentClass["tail-fragment"] | not) and (.sourceCoverageArtifacts.boundaryRepair.manifest | test("boundary-repair-manifest.jsonl$"))' "$TMP_DIR/recover/source-generation/summary.json" >/dev/null

if [[ -n "${MIZUCHI_MSVCSMOKE_STRATEGIES:-}" ]]; then
  FOCUSED_OUT="$TMP_DIR/source-synthesis-focused"
  FOCUSED_SOURCE_QUALITY_ARGS=()
  if [[ -n "${MIZUCHI_MSVCSMOKE_SOURCE_QUALITY:-}" ]]; then
    FOCUSED_SOURCE_QUALITY_ARGS=(--source-quality "$MIZUCHI_MSVCSMOKE_SOURCE_QUALITY")
  fi
  FOCUSED_PACKAGED_SOURCE_ARGS=()
  if [[ "${MIZUCHI_MSVCSMOKE_VERIFY_PACKAGED_SOURCE:-}" == "1" ]]; then
    FOCUSED_PACKAGED_SOURCE_ARGS=(--verify-packaged-source)
  fi
  PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
    --source-tasks "$TMP_DIR/recover/source-generation/tasks.jsonl" \
    --source-tasks-only \
    "${FOCUSED_PACKAGED_SOURCE_ARGS[@]}" \
    --out-dir "$FOCUSED_OUT" \
    --compiler msvc \
    --strategies "$MIZUCHI_MSVCSMOKE_STRATEGIES" \
    "${FOCUSED_SOURCE_QUALITY_ARGS[@]}" \
    --limit "${MIZUCHI_MSVCSMOKE_LIMIT:-200}" \
    --max-variants-per-function "${MIZUCHI_MSVCSMOKE_MAX_VARIANTS:-1}" \
    --timeout "${MIZUCHI_MSVCSMOKE_TIMEOUT:-45}" \
    --semantic-only >/dev/null

  jq -e '.compiler == "msvc" and .semanticOnly == true and .generatedCandidates > 0 and .attemptedCandidates == .generatedCandidates and .semanticCodeSliceMatchedCandidates == .generatedCandidates and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .errorCandidates == 0 and (.generatedBySourceQuality | values | add) == .generatedCandidates and (.attemptedBySourceQuality | values | add) == .attemptedCandidates and (.semanticCodeSliceMatchedBySourceQuality | values | add) == .semanticCodeSliceMatchedCandidates and (.semanticMismatchedBySourceQuality | values | add // 0) == 0 and (.compileFailedBySourceQuality | values | add // 0) == 0 and (.errorBySourceQuality | values | add // 0) == 0' "$FOCUSED_OUT/summary.json" >/dev/null
  if [[ -n "${MIZUCHI_MSVCSMOKE_EXPECT_MATCHES:-}" ]]; then
    jq -e --argjson expected "$MIZUCHI_MSVCSMOKE_EXPECT_MATCHES" '.generatedCandidates == $expected and .attemptedCandidates == $expected and .semanticCodeSliceMatchedCandidates == $expected' "$FOCUSED_OUT/summary.json" >/dev/null
  fi
  jq -e 'select(.status != "code-slice-matched" or .differences != 0)' "$FOCUSED_OUT/attempts.jsonl" >/dev/null && exit 1
  echo "ok"
  exit 0
fi

jq -s -e '
  length == 4 and
  ([.[] | select(.fragmentClass == "boundary-fragment" and .recommendedRepair == "merge-with-adjacent-boundary-fragment")] | length) == 4 and
  ([.[] | select(.fragmentClass == "tail-fragment")] | length) == 0 and
  (map(.name) | sort) == ["sub_19107","sub_1d310","sub_b616","sub_c273"] and
  ([.[] | select(.name == "sub_b616" and .bodyBytes == 2 and .bytePrefix == "ddd8" and .boundaryQuality.hasTerminalReturn == false and (.claimBoundary | test("not generated source")))] | length) == 1 and
  ([.[] | select(.name == "sub_c273" and .bodyBytes == 7 and .bytePrefix == "c745e088390530" and .boundaryQuality.hasTerminalReturn == false and (.claimBoundary | test("not generated source")))] | length) == 1 and
  ([.[] | select(.name == "sub_19107" and .bodyBytes == 6 and .bytePrefix == "8b431c8b4b24" and .boundaryQuality.hasTerminalReturn == false and (.claimBoundary | test("not generated source")))] | length) == 1 and
  ([.[] | select(.name == "sub_1d310" and .bodyBytes == 1 and .bytePrefix == "56" and .boundaryQuality.hasTerminalReturn == false and (.claimBoundary | test("not generated source")))] | length) == 1
' "$TMP_DIR/recover/source-generation/artifacts/boundary-repair-manifest.jsonl" >/dev/null
jq -s -e '
  length == 45 and
  ([.[] | select(.repair == "append-tail-fragment-to-previous-function")] | length) == 4 and
  ([.[] | select(.repair == "append-terminal-continuation-to-prefix-fragment")] | length) == 41 and
  ([.[] | select(.ownerName == "sub_71da" and .fragmentName == "sub_72db" and .ownerOriginalSize == 257 and .ownerRepairedSize == 262 and .fragmentBytePrefix == "5f5e5bc9c3")] | length) == 1 and
  ([.[] | select(.ownerName == "sub_75b7" and .fragmentName == "sub_75ba" and .ownerOriginalSize == 3 and .ownerRepairedSize == 8 and .fragmentBytePrefix == "5f5e5bc9c3")] | length) == 1 and
  ([.[] | select(.ownerName == "sub_75bf" and .fragmentName == "sub_78b6" and .ownerOriginalSize == 759 and .ownerRepairedSize == 764 and .fragmentBytePrefix == "5f5e5bc9c3")] | length) == 1 and
  ([.[] | select(.ownerName == "sub_996e" and .fragmentName == "sub_9971" and .ownerOriginalSize == 3 and .ownerRepairedSize == 7 and .fragmentBytePrefix == "5f5d5bc3")] | length) == 1
' "$TMP_DIR/recover/source-generation/artifacts/applied-boundary-repairs.jsonl" >/dev/null
jq -e '(.classes["bink-buffer-check-win-pos-forwarder"] | not) and (.classes["bink-buffer-clear-forwarder"] | not) and (.classes["bink-buffer-close-forwarder"] | not) and (.classes["bink-buffer-get-description-forwarder"] | not) and (.classes["bink-buffer-lock-forwarder"] | not) and (.classes["bink-buffer-set-direct-draw-forwarder"] | not) and (.classes["bink-buffer-set-offset-forwarder"] | not) and (.classes["bink-buffer-set-scale-forwarder"] | not) and (.classes["bink-buffer-unlock-forwarder"] | not) and (.classes["bink-check-cursor-forwarder"] | not) and (.classes["bink-close-track-forwarder"] | not) and (.classes["bink-copy-to-buffer-forwarder"] | not) and (.classes["bink-get-key-frame-forwarder"] | not) and (.classes["bink-get-realtime-forwarder"] | not) and (.classes["bink-goto-forwarder"] | not) and (.classes["bink-get-summary-forwarder"] | not) and (.classes["bink-close-forwarder"] | not) and (.classes["bink-wait-forwarder"] | not) and (.classes["short-direct-call-ret-masm"] | not) and (.classes["compact-terminal-ret-masm"] | not) and (.classes["compact-import-call-ret-masm"] | not) and (.classes["bink-next-frame-forwarder"] | not) and (.classes["bink-open-track-forwarder"] | not) and (.classes["bink-surface-type-forwarder"] | not) and (.classes["rad-aligned-malloc-forwarder"] | not) and (.classes["rad-aligned-free-forwarder"] | not) and (.classes["rad-direct-free-wrapper"] | not) and (.classes["rad-timer-read-forwarder"] | not) and (.classes["bink-pause-forwarder"] | not) and (.classes["ebx-bitfield-mode-remap"] | not) and (.classes["ecx-global-cmp-return-else-tailjmp"] | not) and (.classes["import-tail-jump"] | not) and (.classes["live-eax-nullable-import-tailjmp-stdcall4"] | not) and (.classes["push-const-call-wrapper"] | not) and (.classes["push-global-call-wrapper"] | not) and (.classes["push-imm32-pair-call-wrapper"] | not) and (.classes["push-stack-stack-const-call-wrapper"] | not) and (.classes["small-zero-scan-bool"] | not) and (.classes["small-copy-loop"] | not) and (.classes["stack-arg-range-global-mode-setter"] | not) and (.classes["u32-add-store-wrap-flag"] | not) and (.classes["u96-bit-tail-clear-check"] | not) and (.classes["u96-left-shift-one"] | not) and (.classes["x87-control-word-masked-setter"] | not) and (.classes["x87-double-exponent-adjust-return"] | not) and (.classes["x87-pop-return-zero"] | not) and (.classes["x87-round-stack-double-return"] | not) and (.classes["x87-temp-i16-return"] | not) and (.classes["nullable-field-setter-u32-stdcall8"] | not) and (.classes["stdcall-store-three-stack-args-to-globals"] | not) and (.classes["stdcall-copy-cstr-to-global"] | not) and (.classes["stdcall-indirect-global-callback-loop"] | not) and (.classes["stdcall-nullable-field-tailjmp"] | not) and (.classes["stdcall-clamped-count-copy-to-global"] | not) and (.classes["stdcall-global-callback-install"] | not) and (.classes["stdcall-track-method-forwarder"] | not) and (.classes["stdcall-yuv-blit-alpha-wrapper"] | not) and (.classes["stdcall-yuv-blit-format-wrapper"] | not) and (.classes["stdcall-yuv-blit-mask-alpha-prefix"] | not) and (.classes["stdcall-yuv-blit-mask-format-prefix"] | not) and (.classes["stdcall-yuv-blit-packed-wrapper"] | not) and (.classes["bounded-leading-return-slice-masm"] | not) and (.classes["extended-terminal-body-masm"] | not) and (.classes["tail-fragment"] | not) and .classes["boundary-fragment"].uniqueCodeStarts == 4 and .classes["unknown"].count == 13 and .classes["unknown"].uniqueCodeStarts == 13 and .classes["unknown"].duplicateAddressTasks == 0 and .classes["call-bearing"].count == 39 and .classes["call-bearing"].uniqueCodeStarts == 38 and .classes["call-bearing"].duplicateAddressTasks == 1 and (.classes["multi-function-packed-slice"] | not)' "$TMP_DIR/recover/source-generation/artifacts/generator-opportunities.json" >/dev/null
jq -e '.sourceGenerationSummary.sourceCoverageArtifacts.generatorOpportunities | test("generator-opportunities.json$")' "$TMP_DIR/recover/strategy.json" >/dev/null

jq -c 'select(.targetSlice.boundaryQuality.sizeSource == "boundary-repaired-tail-fragment")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/boundary-repaired-owner-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/boundary-repaired-owner-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-boundary-repaired-owners" \
  --compiler msvc \
  --limit 4 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 4 and .semanticGeneratedCandidates == 4 and .semanticCodeSliceMatchedCandidates == 4 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-boundary-repaired-owners/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '
  [.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .candidateCompile.compilerRoot == $vc_root)] as $rows |
  ($rows | length) == 4 and
  ($rows | map(.name) | sort) == ["sub_71da","sub_75b7","sub_75bf","sub_996e"] and
  ($rows | map(select(.rule == "bounded-leading-return-slice-masm")) | length) == 2 and
  ($rows | map(select(.rule == "compact-terminal-ret-masm")) | length) == 1 and
  ($rows | map(select(.rule == "bounded-terminal-leaf-masm")) | length) == 1 and
  ($rows | map(select(.name == "sub_71da" and .generationEvidence.bodyBytes == 30 and .generationEvidence.sourceSliceKind == "leading-return-prefix")) | length) == 1 and
  ($rows | map(select(.name == "sub_75b7" and .generationEvidence.bodyBytes == 8 and .generationEvidence.terminalReturn == "ret")) | length) == 1 and
  ($rows | map(select(.name == "sub_75bf" and .generationEvidence.bodyBytes == 104 and .generationEvidence.sourceSliceKind == "leading-return-prefix")) | length) == 1 and
  ($rows | map(select(.name == "sub_996e" and .generationEvidence.bodyBytes == 7 and .generationEvidence.terminalReturn == "ret")) | length) == 1 and
  all($rows[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))
' "$TMP_DIR/source-synthesis-boundary-repaired-owners/attempts.jsonl" >/dev/null

jq -c 'select(.targetSlice.boundaryQuality.sizeSource == "boundary-repaired-prefix-fragment")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/prefix-repaired-owner-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/prefix-repaired-owner-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-prefix-repaired-owners" \
  --compiler msvc \
  --limit 12 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 12 and .semanticGeneratedCandidates == 12 and .semanticCodeSliceMatchedCandidates == 12 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-prefix-repaired-owners/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '
  [.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .candidateCompile.compilerRoot == $vc_root)] as $rows |
  ($rows | length) == 12 and
  ($rows | map(.name) | sort) == ["sub_16830","sub_1910d","sub_1adf0","sub_a330","sub_b568","sub_b618","sub_b9ee","sub_c33d","sub_c350","sub_d472","sub_dc8f","sub_e433"] and
  ($rows | map(select(.name == "sub_b568" and .rule == "short-direct-call-ret-masm" and .generationEvidence.bodyBytes == 19)) | length) == 1 and
  ($rows | map(select(.name == "sub_b9ee" and .rule == "compact-terminal-ret-masm" and .generationEvidence.bodyBytes == 10 and .generationEvidence.terminalReturn == "ret")) | length) == 1 and
  ($rows | map(select(.name == "sub_1910d" and .rule == "bounded-terminal-leaf-masm" and .generationEvidence.bodyBytes == 34 and .generationEvidence.terminalReturn == "ret")) | length) == 1 and
  ($rows | map(select(.name == "sub_16830" and .rule == "bounded-leading-return-slice-masm" and .generationEvidence.bodyBytes == 32)) | length) == 1 and
  ($rows | map(select(.name == "sub_e433" and .rule == "bounded-direct-call-terminal-masm" and .generationEvidence.bodyBytes == 49)) | length) == 1 and
  all($rows[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))
' "$TMP_DIR/source-synthesis-prefix-repaired-owners/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "import-tail-jump")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/import-tail-jump-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/import-tail-jump-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-import-tail-jump" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 2 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-import-tail-jump/summary.json" >/dev/null
jq -e --arg vc_root "$VC_ROOT" 'select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "import-tail-jump" and .variant == "naked-absolute-import-tail-jump" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.targetAddress == "0x3004a098" and .generationEvidence.hasTrailingRet == true and .generationEvidence.sourceTier == "generated inline-assembly byte-emission fallback with decoded absolute indirect jump" and .candidateCompile.compilerRoot == $vc_root)' "$TMP_DIR/source-synthesis-import-tail-jump/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "live-eax-nullable-import-tailjmp-stdcall4")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/live-eax-nullable-import-tailjmp-stdcall4-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/live-eax-nullable-import-tailjmp-stdcall4-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-live-eax-nullable-import-tailjmp-stdcall4" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-live-eax-nullable-import-tailjmp-stdcall4/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "live-eax-nullable-import-tailjmp-stdcall4" and .variant == "naked-live-eax-nullable-import-tailjmp-stdcall4" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.fieldOffset == 4 and .generationEvidence.targetAddress == "0x3004a100" and .generationEvidence.jumpOffset == 15 and .generationEvidence.stackBytes == 4 and .generationEvidence.sourceTier == "generated inline-assembly parity fallback with decoded live-eax nullable import tail-jump bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-live-eax-nullable-import-tailjmp-stdcall4/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "ecx-global-cmp-return-else-tailjmp")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/ecx-global-cmp-return-else-tailjmp-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/ecx-global-cmp-return-else-tailjmp-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-ecx-global-cmp-return-else-tailjmp" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-ecx-global-cmp-return-else-tailjmp/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "ecx-global-cmp-return-else-tailjmp" and .variant == "masm-live-ecx-global-cmp-return-else-tailjmp" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.globalAddress == "0x30055290" and .generationEvidence.jumpTarget == "0x300092c4" and .generationEvidence.jumpOffset == 9 and .generationEvidence.equalPath == "ret" and .generationEvidence.notEqualPath == "tail-jump" and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded live-ecx global compare tail-jump bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-ecx-global-cmp-return-else-tailjmp/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bink-copy-to-buffer-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bink-copy-to-buffer-forwarder-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bink-copy-to-buffer-forwarder-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bink-copy-to-buffer-forwarder" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bink-copy-to-buffer-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bink-copy-to-buffer-forwarder" and .variant == "high-level-bink-copy-to-buffer-forwarder" and .semanticSource == true and .sourceQuality == "high-level-c" and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.export == "BinkCopyToBuffer" and .generationEvidence.stdcallStackBytes == 28 and .generationEvidence.stackArgCount == 7 and .generationEvidence.helperCallOffset == 46 and .generationEvidence.helperCallDisplacement == 13 and .generationEvidence.helperCallTargetOffset == 64 and .generationEvidence.helperCallTargetAddress == "0x30013220" and .generationEvidence.callTarget == "0x30013220" and .generationEvidence.callSymbol == "_sub_30013220@44" and .generationEvidence.bufferPointerArgIndex == 1 and (.generationEvidence.pushedConstants | join(",") == "0,0") and .generationEvidence.returnInstruction == "ret 0x1c" and .generationEvidence.sourceTier == "generated high-level C wrapper for decoded BinkCopyToBuffer forwarding call" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and all(.[]; (.targetObjectRelocations | length) == 1 and .targetObjectRelocations[0].instruction == "call" and .targetObjectRelocations[0].symbol == "_sub_30013220@44" and any(.candidateCompile.command[]; test("cl\\.exe$")))' "$TMP_DIR/source-synthesis-bink-copy-to-buffer-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bink-buffer-clear-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bink-buffer-clear-forwarder-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bink-buffer-clear-forwarder-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bink-buffer-clear-forwarder" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bink-buffer-clear-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bink-buffer-clear-forwarder" and .variant == "masm-bink-buffer-clear-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.export == "BinkBufferClear" and .generationEvidence.stdcallStackBytes == 8 and .generationEvidence.stackArgCount == 2 and .generationEvidence.bufferPointerArgIndex == 1 and .generationEvidence.colorArgIndex == 2 and .generationEvidence.lockCallOffset == 6 and .generationEvidence.lockCallDisplacement == -2139 and .generationEvidence.clearCallOffset == 37 and .generationEvidence.clearCallDisplacement == -6474 and .generationEvidence.unlockCallOffset == 46 and .generationEvidence.unlockCallDisplacement == -1891 and .generationEvidence.lockFailureJumpOffset == 13 and .generationEvidence.lockFailureTargetOffset == 60 and .generationEvidence.clearHelperStackBytes == 16 and .generationEvidence.successReturnValue == 1 and .generationEvidence.failureReturnValue == 0 and .generationEvidence.returnInstruction == "ret 0x08" and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkBufferClear forwarding wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-bink-buffer-clear-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bink-buffer-unlock-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bink-buffer-unlock-forwarder-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bink-buffer-unlock-forwarder-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bink-buffer-unlock-forwarder" \
  --compiler msvc \
  --limit 2 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 2 and .semanticGeneratedCandidates == 2 and .semanticCodeSliceMatchedCandidates == 2 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bink-buffer-unlock-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bink-buffer-unlock-forwarder" and .variant == "masm-bink-buffer-unlock-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.export == "BinkBufferUnlock" and .generationEvidence.stdcallStackBytes == 4 and .generationEvidence.stackArgCount == 1 and .generationEvidence.bufferPointerArgIndex == 1 and .generationEvidence.nullReturnValue == 0 and .generationEvidence.successReturnValue == 1 and .generationEvidence.interfaceFieldOffset == 72 and .generationEvidence.callbackArgFieldOffset == 124 and .generationEvidence.indirectCallbackVtableOffset == 128 and .generationEvidence.optionalHelperGuardFieldOffset == 116 and .generationEvidence.optionalHelperArgFieldOffset == 120 and .generationEvidence.optionalHelperCallOffset == 48 and .generationEvidence.optionalHelperCallDisplacement == -5397 and .generationEvidence.stateHelperPushValue == 2 and .generationEvidence.stateHelperCallOffset == 63 and .generationEvidence.stateHelperCallDisplacement == -5348 and (.generationEvidence.clearedFieldOffsets | join(",") == "20,24") and .generationEvidence.alternateClearGuardFieldOffset == 144 and .generationEvidence.finalAndFieldOffset == 16 and .generationEvidence.finalAndMask == "0x7fffffff" and .generationEvidence.returnInstruction == "ret 0x04" and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkBufferUnlock forwarding wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 2 and (map(.name) | sort) == ["_BinkBufferUnlock@4","sub_11140"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-bink-buffer-unlock-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bink-buffer-set-offset-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bink-buffer-set-offset-forwarder-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bink-buffer-set-offset-forwarder-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bink-buffer-set-offset-forwarder" \
  --compiler msvc \
  --limit 2 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 2 and .semanticGeneratedCandidates == 2 and .semanticCodeSliceMatchedCandidates == 2 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bink-buffer-set-offset-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bink-buffer-set-offset-forwarder" and .variant == "masm-bink-buffer-set-offset-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.export == "BinkBufferSetOffset" and .generationEvidence.stdcallStackBytes == 12 and .generationEvidence.stackArgCount == 3 and .generationEvidence.bufferPointerArgIndex == 1 and .generationEvidence.xOffsetArgIndex == 2 and .generationEvidence.yOffsetArgIndex == 3 and .generationEvidence.windowHandleFieldOffset == 96 and .generationEvidence.windowValidFlagFieldOffset == 100 and .generationEvidence.isWindowImportAddress == "0x3004a198" and .generationEvidence.getWindowRectImportAddress == "0x3004a184" and .generationEvidence.rectScratchBytes == 8 and (.generationEvidence.storedFieldOffsets | join(",") == "80,84,88,92") and .generationEvidence.dirtyFlagFieldOffset == 16 and .generationEvidence.dirtyFlagMask == "0x80000000" and .generationEvidence.stateHelperPushValue == 0 and .generationEvidence.stateHelperCallOffset == 121 and .generationEvidence.stateHelperCallDisplacement == -670 and .generationEvidence.nullReturnValue == 0 and .generationEvidence.successReturnValue == 1 and .generationEvidence.returnInstruction == "ret 0x0c" and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkBufferSetOffset forwarding wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 2 and (map(.name) | sort) == ["_BinkBufferSetOffset@12","sub_fec0"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-bink-buffer-set-offset-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bink-buffer-set-direct-draw-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bink-buffer-set-direct-draw-forwarder-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bink-buffer-set-direct-draw-forwarder-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bink-buffer-set-direct-draw-forwarder" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bink-buffer-set-direct-draw-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bink-buffer-set-direct-draw-forwarder" and .variant == "high-level-bink-buffer-set-direct-draw-forwarder" and .semanticSource == true and .sourceQuality == "high-level-c" and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.export == "BinkBufferSetDirectDraw" and .generationEvidence.bodyBytes == 78 and .generationEvidence.stdcallStackBytes == 8 and .generationEvidence.stackArgCount == 2 and .generationEvidence.directDrawArgIndex == 1 and .generationEvidence.surfaceArgIndex == 2 and .generationEvidence.directDrawGlobalAddress == "0x30068c6c" and .generationEvidence.surfaceGlobalAddress == "0x30068c70" and .generationEvidence.modeGlobalAddress == "0x30068c68" and .generationEvidence.enabledModeValue == "0x08000000" and .generationEvidence.refreshCallOffset == 39 and .generationEvidence.refreshCallDisplacement == -1692 and .generationEvidence.refreshCallTargetAddress == "0x3000f140" and .generationEvidence.callTarget == "0x3000f140" and .generationEvidence.callSymbol == "_sub_3000f140@0" and .generationEvidence.successReturnValue == 1 and .generationEvidence.returnInstruction == "ret 0x08" and .generationEvidence.sourceTier == "generated high-level C wrapper for decoded BinkBufferSetDirectDraw global state update" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and (map(.name) | sort) == ["_BinkBufferSetDirectDraw@8"] and all(.[]; (.targetObjectRelocations | length) == 7 and (.targetObjectRelocations | map(.type) | sort | join(",")) == "IMAGE_REL_I386_DIR32,IMAGE_REL_I386_DIR32,IMAGE_REL_I386_DIR32,IMAGE_REL_I386_DIR32,IMAGE_REL_I386_DIR32,IMAGE_REL_I386_DIR32,IMAGE_REL_I386_REL32" and any(.candidateCompile.command[]; test("cl\\.exe$")))' "$TMP_DIR/source-synthesis-bink-buffer-set-direct-draw-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bink-close-track-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bink-close-track-forwarder-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bink-close-track-forwarder-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bink-close-track-forwarder" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bink-close-track-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bink-close-track-forwarder" and .variant == "masm-bink-close-track-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.export == "BinkCloseTrack" and .generationEvidence.bodyBytes == 95 and .generationEvidence.stdcallStackBytes == 4 and .generationEvidence.stackArgCount == 1 and .generationEvidence.trackPointerArgIndex == 1 and .generationEvidence.optionalAllocationFieldOffset == 20 and .generationEvidence.allocationHeaderKindOffset == -2 and .generationEvidence.allocationHeaderDeltaOffset == -1 and .generationEvidence.customFreeVtableOffset == -8 and .generationEvidence.fieldClearOffset == 20 and .generationEvidence.firstDirectFreeCallOffset == 39 and .generationEvidence.firstDirectFreeCallDisplacement == -62207 and .generationEvidence.finalDirectFreeCallOffset == 83 and .generationEvidence.finalDirectFreeCallDisplacement == -62251 and .generationEvidence.directFreeTargetAddress == "0x300068ed" and .generationEvidence.returnInstruction == "ret 0x04" and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkCloseTrack forwarding wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and (map(.name) | sort) == ["_BinkCloseTrack@4"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-bink-close-track-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bink-pause-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bink-pause-forwarder-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bink-pause-forwarder-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bink-pause-forwarder" \
  --compiler msvc \
  --limit 2 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 2 and .semanticGeneratedCandidates == 2 and .semanticCodeSliceMatchedCandidates == 2 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bink-pause-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bink-pause-forwarder" and .variant == "masm-bink-pause-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.export == "BinkPause" and .generationEvidence.bodyBytes == 179 and .generationEvidence.stdcallStackBytes == 8 and .generationEvidence.stackArgCount == 2 and .generationEvidence.binkPointerArgIndex == 1 and .generationEvidence.pauseModeArgIndex == 2 and .generationEvidence.pauseStartFieldOffset == 636 and .generationEvidence.pauseAccumulatedFieldOffset == 692 and .generationEvidence.pauseFlagFieldOffset == 252 and .generationEvidence.trackCountFieldOffset == 760 and .generationEvidence.trackArrayFieldOffset == 768 and .generationEvidence.trackStrideBytes == 376 and .generationEvidence.prePauseStateFieldOffset == 640 and .generationEvidence.postPauseStateFieldOffset == 824 and .generationEvidence.optionalGuardFieldOffset == 624 and .generationEvidence.timeCallOffset == 19 and .generationEvidence.timeCallDisplacement == 11832 and .generationEvidence.stateHelperCallOffset == 55 and .generationEvidence.stateHelperCallDisplacement == -700 and .generationEvidence.trackMethodCallOffset == 122 and .generationEvidence.trackMethodVtableOffset == 20 and .generationEvidence.optionalHelperCallOffset == 163 and .generationEvidence.optionalHelperCallDisplacement == -12856 and .generationEvidence.nullReturnValue == 0 and .generationEvidence.returnFieldOffset == 252 and .generationEvidence.returnInstruction == "ret 0x08" and .generationEvidence.targetByteSpan.length == 179 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkPause forwarding wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 2 and (map(.name) | sort) == ["_BinkPause@8","sub_14e30"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-bink-pause-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bink-get-key-frame-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bink-get-key-frame-forwarder-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bink-get-key-frame-forwarder-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bink-get-key-frame-forwarder" \
  --compiler msvc \
  --limit 2 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 2 and .semanticGeneratedCandidates == 2 and .semanticCodeSliceMatchedCandidates == 2 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bink-get-key-frame-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bink-get-key-frame-forwarder" and .variant == "masm-bink-get-key-frame-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.export == "BinkGetKeyFrame" and .generationEvidence.bodyBytes == 212 and .generationEvidence.stdcallStackBytes == 12 and .generationEvidence.stackArgCount == 3 and .generationEvidence.binkPointerArgIndex == 1 and .generationEvidence.frameArgIndex == 2 and .generationEvidence.modeArgIndex == 3 and .generationEvidence.frameCountFieldOffset == 8 and .generationEvidence.keyFrameTableFieldOffset == 268 and .generationEvidence.frameFlagMask == 1 and .generationEvidence.modeMask == "0x7f" and .generationEvidence.signedModeUsesCurrentFrameCheck == true and .generationEvidence.nullReturnValue == 0 and .generationEvidence.notFoundReturnValue == 0 and .generationEvidence.returnInstruction == "ret 0x0c" and .generationEvidence.targetByteSpan.length == 212 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkGetKeyFrame forwarding wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 2 and (map(.name) | sort) == ["_BinkGetKeyFrame@12","sub_14720"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-bink-get-key-frame-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bink-check-cursor-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bink-check-cursor-forwarder-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bink-check-cursor-forwarder-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bink-check-cursor-forwarder" \
  --compiler msvc \
  --limit 2 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 2 and .semanticGeneratedCandidates == 2 and .semanticCodeSliceMatchedCandidates == 2 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bink-check-cursor-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bink-check-cursor-forwarder" and .variant == "masm-bink-check-cursor-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.export == "BinkCheckCursor" and .generationEvidence.bodyBytes == 179 and .generationEvidence.stdcallStackBytes == 20 and .generationEvidence.stackArgCount == 5 and .generationEvidence.windowHandleArgIndex == 1 and .generationEvidence.xArgIndex == 2 and .generationEvidence.yArgIndex == 3 and .generationEvidence.widthArgIndex == 4 and .generationEvidence.heightArgIndex == 5 and .generationEvidence.cursorWidthGlobalAddress == "0x30068cac" and .generationEvidence.cursorHeightGlobalAddress == "0x30055cb8" and .generationEvidence.getSystemMetricsImportAddress == "0x3004a154" and .generationEvidence.getSystemMetricsWidthIndex == 13 and .generationEvidence.getSystemMetricsHeightIndex == 14 and .generationEvidence.getWindowRectImportAddress == "0x3004a184" and .generationEvidence.getCursorPosImportAddress == "0x3004a158" and .generationEvidence.showCursorImportAddress == "0x3004a1a8" and .generationEvidence.localRectBytes == 8 and .generationEvidence.localPointBytes == 8 and .generationEvidence.showCursorArgument == 0 and .generationEvidence.returnShowsHiddenCount == true and .generationEvidence.returnInstruction == "ret 0x14" and .generationEvidence.targetByteSpan.length == 179 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkCheckCursor forwarding wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 2 and (map(.name) | sort) == ["_BinkCheckCursor@20","sub_fba0"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-bink-check-cursor-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bink-open-track-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bink-open-track-forwarder-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bink-open-track-forwarder-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bink-open-track-forwarder" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bink-open-track-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bink-open-track-forwarder" and .variant == "masm-bink-open-track-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.export == "BinkOpenTrack" and .generationEvidence.bodyBytes == 272 and .generationEvidence.stdcallStackBytes == 8 and .generationEvidence.stackArgCount == 2 and .generationEvidence.binkPointerArgIndex == 1 and .generationEvidence.trackIndexArgIndex == 2 and .generationEvidence.trackCountFieldOffset == 240 and .generationEvidence.trackDescriptorTableFieldOffset == 612 and .generationEvidence.trackLengthTableFieldOffset == 608 and .generationEvidence.trackAllocationCursorFieldOffset == 680 and .generationEvidence.globalTrackAllocationBaseAddress == "0x30058078" and .generationEvidence.trackDescriptorMask == "0xffff" and .generationEvidence.trackTypeMask == "0x10000000" and .generationEvidence.trackObjectDwordClearCount == 7 and .generationEvidence.trackObjectBinkFieldOffset == 16 and .generationEvidence.trackObjectHelperFieldOffset == 20 and .generationEvidence.trackObjectIndexFieldOffset == 24 and .generationEvidence.helperOpenCallOffset == 91 and .generationEvidence.helperOpenCallDisplacement == 21936 and .generationEvidence.allocationCallOffset == 129 and .generationEvidence.allocationCallDisplacement == -84342 and .generationEvidence.helperCloseCallOffset == 142 and .generationEvidence.helperCloseCallDisplacement == 22829 and .generationEvidence.nullReturnValue == 0 and .generationEvidence.returnInstruction == "ret 0x08" and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkOpenTrack forwarding wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and (map(.name) | sort) == ["_BinkOpenTrack@8"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-bink-open-track-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bink-buffer-get-description-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bink-buffer-get-description-forwarder-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bink-buffer-get-description-forwarder-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bink-buffer-get-description-forwarder" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bink-buffer-get-description-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bink-buffer-get-description-forwarder" and .variant == "masm-bink-buffer-get-description-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.export == "BinkBufferGetDescription" and .generationEvidence.bodyBytes == 384 and .generationEvidence.stdcallStackBytes == 4 and .generationEvidence.stackArgCount == 1 and .generationEvidence.bufferArgIndex == 1 and .generationEvidence.typeFieldOffset == 128 and .generationEvidence.caseBaseAdjustment == -1 and .generationEvidence.maxCaseIndex == 9 and .generationEvidence.descriptorScratchGlobalAddress == "0x30055bb0" and .generationEvidence.jumpTableAddress == "0x30011838" and .generationEvidence.embeddedJumpTableOffset == 344 and .generationEvidence.embeddedJumpTableBytes == 40 and (.generationEvidence.embeddedJumpTableEntries | join(",")) == "0x300117e7,0x30011805,0x30011705,0x30011720,0x3001173b,0x30011756,0x30011774,0x30011792,0x300117b0,0x300117cb" and (.generationEvidence.descriptorSourceAddresses | join(",")) == "0x3004fd18,0x3004fd00,0x3004fce8,0x3004fcc4,0x3004fca0,0x3004fc7c,0x3004fc54,0x3004fc28,0x3004fc0c,0x3004fc00" and .generationEvidence.nullBufferReturnValue == 0 and .generationEvidence.defaultReturnScratch == true and .generationEvidence.returnInstruction == "ret 0x04" and .generationEvidence.targetByteSpan.length == 384 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkBufferGetDescription descriptor switch bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and (map(.name) | sort) == ["_BinkBufferGetDescription@4"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-bink-buffer-get-description-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bink-next-frame-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bink-next-frame-forwarder-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bink-next-frame-forwarder-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bink-next-frame-forwarder" \
  --compiler msvc \
  --limit 2 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 2 and .semanticGeneratedCandidates == 2 and .semanticCodeSliceMatchedCandidates == 2 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bink-next-frame-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bink-next-frame-forwarder" and .variant == "masm-bink-next-frame-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.export == "BinkNextFrame" and .generationEvidence.bodyBytes == 454 and .generationEvidence.stdcallStackBytes == 4 and .generationEvidence.stackArgCount == 1 and .generationEvidence.binkPointerArgIndex == 1 and .generationEvidence.trackCountFieldOffset == 760 and .generationEvidence.trackTableFieldOffset == 768 and .generationEvidence.trackStrideBytes == 376 and .generationEvidence.frameDoneFlagFieldOffset == 312 and (.generationEvidence.soundOnOffCallOffsets | join(",")) == "206,295" and .generationEvidence.soundOnOffTargetAddress == "0x30015d40" and .generationEvidence.callbackDispatchImportAddress == "0x3004a100" and (.generationEvidence.helperCallOffsets | join(",")) == "366,371,413,434" and (.generationEvidence.helperCallTargets | join(",")) == "0x30011ca0,0x30017c80,0x30011f70,0x30011f70" and (.generationEvidence.importCallOffsets | join(",")) == "177,198" and (.generationEvidence.importCallAddresses | join(",")) == "0x3004a0f8,0x3004a0e8" and .generationEvidence.nullPointerReturnsWithoutWork == true and .generationEvidence.returnInstruction == "ret 0x04" and .generationEvidence.targetByteSpan.length == 454 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkNextFrame state-advance wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 2 and (map(.name) | sort) == ["_BinkNextFrame@4","sub_14550"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-bink-next-frame-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bink-get-realtime-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bink-get-realtime-forwarder-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bink-get-realtime-forwarder-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bink-get-realtime-forwarder" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bink-get-realtime-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bink-get-realtime-forwarder" and .variant == "masm-bink-get-realtime-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.export == "BinkGetRealtime" and .generationEvidence.bodyBytes == 327 and .generationEvidence.stdcallStackBytes == 12 and .generationEvidence.stackArgCount == 3 and .generationEvidence.binkPointerArgIndex == 1 and .generationEvidence.outSummaryArgIndex == 2 and .generationEvidence.sampleFrameCountArgIndex == 3 and .generationEvidence.timerReadCallOffset == 4 and .generationEvidence.timerReadTargetAddress == "0x30017c80" and .generationEvidence.timebaseUpdateCallOffset == 47 and .generationEvidence.timebaseUpdateTargetAddress == "0x30014bb0" and .generationEvidence.pauseStartFieldOffset == 636 and .generationEvidence.pauseAccumFieldOffset == 692 and .generationEvidence.frameCountFieldOffset == 12 and .generationEvidence.largestFrameSeenFieldOffset == 708 and .generationEvidence.outputBytes == 56 and .generationEvidence.returnInstruction == "ret 0x0c" and .generationEvidence.targetByteSpan.length == 327 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkGetRealtime summary wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and (map(.name) | sort) == ["_BinkGetRealtime@12"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-bink-get-realtime-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bink-goto-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bink-goto-forwarder-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bink-goto-forwarder-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bink-goto-forwarder" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bink-goto-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bink-goto-forwarder" and .variant == "masm-bink-goto-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.export == "BinkGoto" and .generationEvidence.bodyBytes == 445 and .generationEvidence.stdcallStackBytes == 12 and .generationEvidence.stackArgCount == 3 and .generationEvidence.binkPointerArgIndex == 1 and .generationEvidence.targetFrameArgIndex == 2 and .generationEvidence.modeArgIndex == 3 and .generationEvidence.frameCountFieldOffset == 8 and .generationEvidence.currentFrameFieldOffset == 12 and .generationEvidence.frameRateDividendFieldOffset == 20 and .generationEvidence.frameRateDivisorFieldOffset == 24 and .generationEvidence.frameDoneFlagFieldOffset == 312 and .generationEvidence.seekScratchFieldOffset == 676 and .generationEvidence.trackCountFieldOffset == 760 and .generationEvidence.trackStateFieldOffset == 672 and .generationEvidence.decodedFrameFlagFieldOffset == 772 and .generationEvidence.resumeCallbackFieldOffset == 844 and .generationEvidence.modeMaskRewind == 1 and .generationEvidence.modeMaskNoDecode == 2 and .generationEvidence.keyFrameCallOffset == 132 and .generationEvidence.keyFrameTargetAddress == "0x30011f70" and .generationEvidence.frameDecodeCallOffset == 170 and .generationEvidence.frameDecodeTargetAddress == "0x30014720" and .generationEvidence.frameResetCallOffset == 218 and .generationEvidence.frameResetTargetAddress == "0x30011f70" and .generationEvidence.trackMuteCallOffset == 274 and .generationEvidence.trackResumeCallOffset == 423 and .generationEvidence.soundOnOffTargetAddress == "0x30015d40" and (.generationEvidence.preFrameCallOffsets | join(",")) == "291,347" and .generationEvidence.preFrameTargetAddress == "0x30013f30" and (.generationEvidence.nextFrameCallOffsets | join(",")) == "315,359" and .generationEvidence.nextFrameTargetAddress == "0x30014550" and (.generationEvidence.importCallOffsets | join(",")) == "251,407" and (.generationEvidence.importCallAddresses | join(",")) == "0x3004a0e8,0x3004a100" and .generationEvidence.nullPointerReturnsWithoutWork == true and .generationEvidence.returnInstruction == "ret 0x0c" and .generationEvidence.targetByteSpan.length == 445 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkGoto seek wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and (map(.name) | sort) == ["_BinkGoto@12"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-bink-goto-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bink-get-summary-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bink-get-summary-forwarder-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bink-get-summary-forwarder-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bink-get-summary-forwarder" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bink-get-summary-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bink-get-summary-forwarder" and .variant == "masm-bink-get-summary-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.export == "BinkGetSummary" and .generationEvidence.bodyBytes == 457 and .generationEvidence.stdcallStackBytes == 8 and .generationEvidence.stackArgCount == 2 and .generationEvidence.binkPointerArgIndex == 1 and .generationEvidence.outSummaryArgIndex == 2 and (.generationEvidence.timerReadCallOffsets | join(",")) == "27,146" and .generationEvidence.timerReadTargetAddress == "0x30017c80" and .generationEvidence.timebaseUpdateCallOffset == 67 and .generationEvidence.timebaseUpdateTargetAddress == "0x30014bb0" and .generationEvidence.pauseStartFieldOffset == 636 and .generationEvidence.pauseAccumFieldOffset == 692 and .generationEvidence.outputDwordClearCount == 31 and .generationEvidence.outputBytes == 124 and .generationEvidence.frameRateDividendFieldOffset == 20 and .generationEvidence.frameRateDivisorFieldOffset == 24 and .generationEvidence.frameCountFieldOffset == 8 and .generationEvidence.currentFrameFieldOffset == 12 and .generationEvidence.elapsedGlobalFieldOffset == 628 and .generationEvidence.trackCountFieldOffset == 760 and .generationEvidence.keyFrameTableFieldOffset == 268 and .generationEvidence.firstKeyFrameMask == "0xfffffffe" and .generationEvidence.returnInstruction == "ret 0x08" and .generationEvidence.targetByteSpan.length == 457 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkGetSummary summary-copy wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and (map(.name) | sort) == ["_BinkGetSummary@8"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-bink-get-summary-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bink-close-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bink-close-forwarder-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bink-close-forwarder-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bink-close-forwarder" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bink-close-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bink-close-forwarder" and .variant == "masm-bink-close-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.export == "BinkClose" and .generationEvidence.bodyBytes == 496 and .generationEvidence.stdcallStackBytes == 4 and .generationEvidence.stackArgCount == 1 and .generationEvidence.binkPointerArgIndex == 1 and .generationEvidence.flagsFieldOffset == 32 and .generationEvidence.tracksOpenFieldOffset == 760 and .generationEvidence.trackTableFieldOffset == 768 and .generationEvidence.trackStrideBytes == 376 and .generationEvidence.trackCloseVtableOffset == 28 and .generationEvidence.trackPrimaryAllocationOffset == 60 and .generationEvidence.trackSecondaryAllocationOffset == 44 and .generationEvidence.globalAudioHandleAddress == "0x3006522c" and .generationEvidence.globalAudioModeAddress == "0x30065230" and .generationEvidence.globalSurfaceAddress == "0x300646c0" and .generationEvidence.globalSurfaceAuxAddress == "0x300646bc" and .generationEvidence.pauseBeforeCloseCallOffset == 19 and .generationEvidence.pauseBeforeCloseTargetAddress == "0x30014e30" and .generationEvidence.backendShutdownCallOffset == 87 and .generationEvidence.backendShutdownTargetAddress == "0x3001b890" and (.generationEvidence.directFreeCallOffsets | join(",")) == "188,232,291,394,434,483" and .generationEvidence.directFreeTargetAddress == "0x300068ed" and .generationEvidence.allocationHeaderKindOffset == -2 and .generationEvidence.allocationHeaderDeltaOffset == -1 and .generationEvidence.customFreeVtableOffset == -8 and .generationEvidence.customAllocatorMarker == 3 and .generationEvidence.structClearDwordCount == 227 and .generationEvidence.nullPointerNoop == true and .generationEvidence.returnInstruction == "ret 0x04" and .generationEvidence.targetByteSpan.length == 496 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkClose teardown wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and (map(.name) | sort) == ["_BinkClose@4"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-bink-close-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bink-wait-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bink-wait-forwarder-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bink-wait-forwarder-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bink-wait-forwarder" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bink-wait-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bink-wait-forwarder" and .variant == "masm-bink-wait-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.export == "BinkWait" and .generationEvidence.bodyBytes == 531 and .generationEvidence.stdcallStackBytes == 4 and .generationEvidence.stackArgCount == 1 and .generationEvidence.binkPointerArgIndex == 1 and .generationEvidence.activeFieldOffset == 624 and .generationEvidence.pausedFlagFieldOffset == 252 and .generationEvidence.timingStateFieldOffset == 28 and .generationEvidence.waitStartFieldOffset == 640 and .generationEvidence.waitFrameFieldOffset == 644 and .generationEvidence.pauseStartFieldOffset == 636 and .generationEvidence.pauseAccumFieldOffset == 692 and .generationEvidence.trackCountFieldOffset == 760 and .generationEvidence.trackStateFieldOffset == 672 and .generationEvidence.frameRateDividendFieldOffset == 20 and .generationEvidence.frameRateDivisorFieldOffset == 24 and .generationEvidence.frameDelayFieldOffset == 648 and .generationEvidence.frameTimeBaseFieldOffset == 824 and .generationEvidence.frameTimeTargetFieldOffset == 828 and .generationEvidence.audioStateFieldOffset == 264 and .generationEvidence.backendContextGlobalAddress == "0x3006522c" and .generationEvidence.backendStateOffset == 524 and (.generationEvidence.timerReadCallOffsets | join(",")) == "60,106" and .generationEvidence.timerReadTargetAddress == "0x30017c80" and .generationEvidence.trackSyncCallOffset == 101 and .generationEvidence.trackSyncTargetAddress == "0x30011ca0" and .generationEvidence.timebaseUpdateCallOffset == 147 and .generationEvidence.timebaseUpdateTargetAddress == "0x30014bb0" and .generationEvidence.backendPollCallOffset == 482 and .generationEvidence.backendPollTargetAddress == "0x3001bbb0" and .generationEvidence.backendStartVtableOffset == 288 and .generationEvidence.backendCommitCallOffset == 512 and .generationEvidence.backendCommitTargetAddress == "0x3001bbe0" and .generationEvidence.successReturnValue == 1 and .generationEvidence.waitReturnValue == 0 and .generationEvidence.returnInstruction == "ret 0x04" and .generationEvidence.targetByteSpan.length == 531 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkWait timing wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and (map(.name) | sort) == ["_BinkWait@4"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-bink-wait-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "short-direct-call-ret-masm")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/short-direct-call-ret-masm-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/short-direct-call-ret-masm-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-short-direct-call-ret-masm" \
  --compiler msvc \
  --limit 7 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 7 and .semanticGeneratedCandidates == 7 and .semanticCodeSliceMatchedCandidates == 7 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-short-direct-call-ret-masm/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '
  [.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "short-direct-call-ret-masm" and .variant == "masm-short-direct-call-ret" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.callOpcode == "E8 rel32" and .generationEvidence.terminalReturn == "ret" and .generationEvidence.maxRelativeTargetDistance == 16777216 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded compact direct-call/ret bytes" and .candidateCompile.compilerRoot == $vc_root)] as $rows |
  ($rows | length) == 7 and
  ($rows | map(.name) | sort) == ["sub_8640","sub_a710","sub_b568","sub_c0a8","sub_c33d","sub_c906","sub_cb41"] and
  ($rows | map(select(.name == "sub_8640" and .generationEvidence.bodyBytes == 9 and .generationEvidence.callOffset == 3 and .generationEvidence.callTargetAddress == "0x3000908b")) | length) == 1 and
  ($rows | map(select(.name == "sub_a710" and .generationEvidence.bodyBytes == 25 and .generationEvidence.callOffset == 18 and .generationEvidence.callTargetAddress == "0x3000a729")) | length) == 1 and
  ($rows | map(select(.name == "sub_b568" and .generationEvidence.bodyBytes == 19)) | length) == 1 and
  ($rows | map(select(.name == "sub_c0a8" and .generationEvidence.bodyBytes == 22 and .generationEvidence.callOffset == 14 and .generationEvidence.callTargetAddress == "0x3000c076")) | length) == 1 and
  ($rows | map(select(.name == "sub_c33d" and .generationEvidence.bodyBytes == 19)) | length) == 1 and
  ($rows | map(select(.name == "sub_c906" and .generationEvidence.bodyBytes == 19 and .generationEvidence.callOffset == 10 and .generationEvidence.callTargetAddress == "0x3000c350")) | length) == 1 and
  ($rows | map(select(.name == "sub_cb41" and .generationEvidence.bodyBytes == 21 and .generationEvidence.callOffset == 11 and .generationEvidence.callTargetAddress == "0x3000c350")) | length) == 1 and
  all($rows[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))
' "$TMP_DIR/source-synthesis-short-direct-call-ret-masm/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "compact-terminal-ret-masm")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/compact-terminal-ret-masm-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/compact-terminal-ret-masm-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-compact-terminal-ret-masm" \
  --compiler msvc \
  --limit 9 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 9 and .semanticGeneratedCandidates == 9 and .semanticCodeSliceMatchedCandidates == 9 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-compact-terminal-ret-masm/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '
  [.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "compact-terminal-ret-masm" and .variant == "masm-compact-terminal-ret" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.maxBodyBytes == 32 and .generationEvidence.maxRelativeTargetDistance == 16777216 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded compact terminal-ret bytes" and .candidateCompile.compilerRoot == $vc_root)] as $rows |
  ($rows | length) == 9 and
  ($rows | map(.name) | sort) == ["sub_1add6","sub_1c0ff","sub_1cfc1","sub_75b7","sub_908b","sub_9dda","sub_b6a0","sub_b9ee","sub_dc8f"] and
  ($rows | map(select(.name == "sub_75b7" and .generationEvidence.bodyBytes == 8 and .generationEvidence.terminalReturn == "ret" and .generationEvidence.terminalReturnOffset == 7)) | length) == 1 and
  ($rows | map(select(.name == "sub_908b" and .generationEvidence.bodyBytes == 17 and .generationEvidence.terminalReturn == "ret" and .generationEvidence.terminalReturnOffset == 16 and (.generationEvidence.callLikeByteOffsets | length) == 0)) | length) == 1 and
  ($rows | map(select(.name == "sub_9dda" and .generationEvidence.bodyBytes == 24 and .generationEvidence.terminalReturn == "ret 0x04" and .generationEvidence.terminalReturnOffset == 21 and .generationEvidence.terminalStackBytes == 4)) | length) == 1 and
  ($rows | map(select(.name == "sub_b6a0" and .generationEvidence.bodyBytes == 21 and .generationEvidence.terminalReturn == "ret" and .generationEvidence.terminalReturnOffset == 20 and (.generationEvidence.callLikeByteOffsets | join(",")) == "13")) | length) == 1 and
  ($rows | map(select(.name == "sub_b9ee" and .generationEvidence.bodyBytes == 10 and .generationEvidence.terminalReturn == "ret")) | length) == 1 and
  ($rows | map(select(.name == "sub_dc8f" and .generationEvidence.bodyBytes == 24 and .generationEvidence.terminalReturn == "ret")) | length) == 1 and
  ($rows | map(select(.name == "sub_1add6" and .generationEvidence.bodyBytes == 26 and .generationEvidence.terminalReturn == "ret" and .generationEvidence.terminalReturnOffset == 25)) | length) == 1 and
  ($rows | map(select(.name == "sub_1c0ff" and .generationEvidence.bodyBytes == 24 and .generationEvidence.terminalReturn == "ret" and .generationEvidence.terminalReturnOffset == 23)) | length) == 1 and
  ($rows | map(select(.name == "sub_1cfc1" and .generationEvidence.bodyBytes == 27 and .generationEvidence.terminalReturn == "ret" and .generationEvidence.terminalReturnOffset == 26)) | length) == 1 and
  all($rows[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))
' "$TMP_DIR/source-synthesis-compact-terminal-ret-masm/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "compact-import-call-ret-masm")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/compact-import-call-ret-masm-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/compact-import-call-ret-masm-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-compact-import-call-ret-masm" \
  --compiler msvc \
  --limit 17 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 17 and .semanticGeneratedCandidates == 17 and .semanticCodeSliceMatchedCandidates == 17 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-compact-import-call-ret-masm/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '
  [.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "compact-import-call-ret-masm" and .variant == "masm-compact-import-call-ret" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.maxBodyBytes == 96 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded compact import-call/ret bytes" and .candidateCompile.compilerRoot == $vc_root)] as $rows |
  ($rows | length) == 17 and
  ($rows | map(.name) | sort) == ["sub_11556","sub_15e80","sub_167e0","sub_1b4a0","sub_1b770","sub_1b7a0","sub_1b800","sub_1baf0","sub_1bbb0","sub_6925","sub_6b4e","sub_6cc8","sub_6d98","sub_9370","sub_9a90","sub_a64d","sub_f0e0"] and
  ($rows | map(select(.name == "sub_1baf0" and .generationEvidence.bodyBytes == 33 and .generationEvidence.importCallCount == 2 and (.generationEvidence.importCallOffsets | join(",")) == "15,26" and (.generationEvidence.importCallAddresses | join(",")) == "0x3004a104,0x3004a100")) | length) == 1 and
  ($rows | map(select(.name == "sub_1bbb0" and .generationEvidence.bodyBytes == 36 and .generationEvidence.terminalReturn == "ret 0x04" and .generationEvidence.importCallCount == 1 and (.generationEvidence.importCallAddresses | join(",")) == "0x3004a0e8")) | length) == 1 and
  ($rows | map(select(.name == "sub_9a90" and .generationEvidence.bodyBytes == 86 and .generationEvidence.importCallCount == 5 and (.generationEvidence.importCallOffsets | join(",")) == "11,23,31,39,51")) | length) == 1 and
  ($rows | map(select(.name == "sub_11556" and .generationEvidence.bodyBytes == 91 and .generationEvidence.terminalReturn == "ret 0x0c" and .generationEvidence.importCallCount == 2)) | length) == 1 and
  ($rows | map(select(.name == "sub_1b7a0" and .generationEvidence.bodyBytes == 96 and .generationEvidence.importCallCount == 3)) | length) == 1 and
  all($rows[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))
' "$TMP_DIR/source-synthesis-compact-import-call-ret-masm/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "packed-leading-function-masm")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/packed-leading-function-masm-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/packed-leading-function-masm-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-packed-leading-function-masm" \
  --compiler msvc \
  --limit 37 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 37 and .semanticGeneratedCandidates == 37 and .semanticCodeSliceMatchedCandidates == 37 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-packed-leading-function-masm/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '
  [.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "packed-leading-function-masm" and .variant == "masm-packed-leading-function" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.boundaryRepair == "split-leading-function" and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with mechanically split packed leading-function bytes" and .candidateCompile.compilerRoot == $vc_root)] as $rows |
  ($rows | length) == 37 and
  ($rows | map(.name) | sort) == ["_BinkOpenDirectSound@4","sub_120f0","sub_1220","sub_16190","sub_1625a","sub_16900","sub_16a54","sub_16b70","sub_16f30","sub_176a0","sub_17e30","sub_17f97","sub_184b0","sub_18850","sub_192b1","sub_1b4e0","sub_1c190","sub_1f274","sub_21434","sub_21a32","sub_21a90","sub_33220","sub_335ff","sub_3379b","sub_339db","sub_47230","sub_47445","sub_477fb","sub_47a5b","sub_6380","sub_65b0","sub_8d10","sub_9975","sub_9df2","sub_9ecb","sub_a182","sub_b401"] and
  ($rows | map(select(.name == "sub_9975" and .generationEvidence.bodyBytes == 56 and .generationEvidence.packedSliceBytes == 67 and .generationEvidence.trailingExecutableOffset == 59 and .generationEvidence.trailingExecutableBytes == 8)) | length) == 1 and
  ($rows | map(select(.name == "sub_1625a" and .generationEvidence.bodyBytes == 156 and .generationEvidence.terminalReturn == "ret 0x04" and .generationEvidence.terminalStackBytes == 4 and .generationEvidence.packedSliceBytes == 1035)) | length) == 1 and
  ($rows | map(select(.name == "_BinkOpenDirectSound@4" and .generationEvidence.bodyBytes == 241 and .generationEvidence.terminalReturn == "ret 0x04" and .generationEvidence.trailingExecutableBytes == 611)) | length) == 1 and
  ($rows | map(select(.name == "sub_192b1" and .generationEvidence.bodyBytes == 825 and .generationEvidence.packedSliceBytes == 2308 and .generationEvidence.trailingExecutableBytes == 1477)) | length) == 1 and
  all($rows[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))
' "$TMP_DIR/source-synthesis-packed-leading-function-masm/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bounded-terminal-leaf-masm")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bounded-terminal-leaf-masm-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bounded-terminal-leaf-masm-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bounded-terminal-leaf-masm" \
  --compiler msvc \
  --limit 21 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 21 and .semanticGeneratedCandidates == 21 and .semanticCodeSliceMatchedCandidates == 21 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bounded-terminal-leaf-masm/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '
  [.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bounded-terminal-leaf-masm" and .variant == "masm-bounded-terminal-leaf" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.directCallCount == 0 and .generationEvidence.importCallCount == 0 and .generationEvidence.maxBodyBytes == 128 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded bounded terminal leaf/control bytes" and .candidateCompile.compilerRoot == $vc_root)] as $rows |
  ($rows | length) == 21 and
  ($rows | map(.name) | sort) == ["sub_10400","sub_11b70","sub_11c41","sub_14bb0","sub_158c0","sub_168c0","sub_1910d","sub_2b30","sub_2b80","sub_6c60","sub_6de0","sub_80a0","sub_9310","sub_996e","sub_b3be","sub_b618","sub_b6b5","sub_b6e5","sub_d2b0","sub_d472","sub_f560"] and
  ($rows | map(select(.name == "sub_996e" and .generationEvidence.bodyBytes == 7 and .generationEvidence.terminalReturn == "ret" and .generationEvidence.terminalReturnOffset == 6)) | length) == 1 and
  ($rows | map(select(.name == "sub_1910d" and .generationEvidence.bodyBytes == 34 and .generationEvidence.terminalReturn == "ret")) | length) == 1 and
  ($rows | map(select(.name == "sub_2b80" and .generationEvidence.bodyBytes == 125 and .generationEvidence.terminalReturn == "ret" and (.generationEvidence.jumpLikeOffsets | length) == 0)) | length) == 1 and
  ($rows | map(select(.name == "sub_11c41" and .generationEvidence.bodyBytes == 91 and (.generationEvidence.jumpLikeOffsets | join(",")) == "33,35,56")) | length) == 1 and
  ($rows | map(select(.name == "sub_b618" and .generationEvidence.bodyBytes == 34 and .generationEvidence.terminalReturn == "ret")) | length) == 1 and
  ($rows | map(select(.name == "sub_d472" and .generationEvidence.bodyBytes == 45 and .generationEvidence.terminalReturn == "ret")) | length) == 1 and
  all($rows[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))
' "$TMP_DIR/source-synthesis-bounded-terminal-leaf-masm/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bounded-direct-call-terminal-masm")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bounded-direct-call-terminal-masm-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bounded-direct-call-terminal-masm-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bounded-direct-call-terminal-masm" \
  --compiler msvc \
  --limit 34 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 34 and .semanticGeneratedCandidates == 34 and .semanticCodeSliceMatchedCandidates == 34 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bounded-direct-call-terminal-masm/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '
  [.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bounded-direct-call-terminal-masm" and .variant == "masm-bounded-direct-call-terminal" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.directCallCount >= 1 and .generationEvidence.importCallCount == 0 and .generationEvidence.maxBodyBytes == 128 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded bounded direct-call terminal bytes" and .candidateCompile.compilerRoot == $vc_root)] as $rows |
  ($rows | length) == 34 and
  ($rows | map(.name) | sort) == ["sub_11f40","sub_19f70","sub_1b470","sub_1b840","sub_1ba12","sub_1c120","sub_204d0","sub_696b","sub_7bf8","sub_8254","sub_8649","sub_8749","sub_891b","sub_9050","sub_9c28","sub_9d04","sub_a2d5","sub_a313","sub_a5aa","sub_a60d","sub_a6ab","sub_b640","sub_b82c","sub_ba2a","sub_ba77","sub_bb1d","sub_bd1c","sub_bd5f","sub_bda2","sub_c076","sub_d3e6","sub_e14c","sub_e1d3","sub_e433"] and
  ($rows | map(select(.name == "sub_d3e6" and .generationEvidence.bodyBytes == 94 and .generationEvidence.directCallCount == 4 and (.generationEvidence.directCallOffsets | join(",")) == "15,35,59,83")) | length) == 1 and
  ($rows | map(select(.name == "sub_1ba12" and .generationEvidence.bodyBytes == 91 and .generationEvidence.terminalReturn == "ret 0x10" and .generationEvidence.directCallCount == 2)) | length) == 1 and
  ($rows | map(select(.name == "sub_e433" and .generationEvidence.bodyBytes == 49 and .generationEvidence.directCallCount >= 1)) | length) == 1 and
  ($rows | map(select(.name == "sub_8649" and .generationEvidence.bodyBytes == 126 and .generationEvidence.directCallCount == 1 and (.generationEvidence.jumpLikeOffsets | length) == 2)) | length) == 1 and
  all($rows[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))
' "$TMP_DIR/source-synthesis-bounded-direct-call-terminal-masm/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bounded-import-call-terminal-masm")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bounded-import-call-terminal-masm-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bounded-import-call-terminal-masm-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bounded-import-call-terminal-masm" \
  --compiler msvc \
  --limit 6 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 6 and .semanticGeneratedCandidates == 6 and .semanticCodeSliceMatchedCandidates == 6 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bounded-import-call-terminal-masm/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '
  [.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bounded-import-call-terminal-masm" and .variant == "masm-bounded-import-call-terminal" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.importCallCount >= 1 and .generationEvidence.maxBodyBytes == 160 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded bounded import-call terminal bytes" and .candidateCompile.compilerRoot == $vc_root)] as $rows |
  ($rows | length) == 6 and
  ($rows | map(.name) | sort) == ["sub_16100","sub_1ba70","sub_1bb20","sub_6d19","sub_78d6","sub_9c88"] and
  ($rows | map(select(.name == "sub_6d19" and .generationEvidence.bodyBytes == 127 and .generationEvidence.directCallCount == 0 and .generationEvidence.importCallCount == 1 and (.generationEvidence.importCallOffsets | join(",")) == "120")) | length) == 1 and
  ($rows | map(select(.name == "sub_1ba70" and .generationEvidence.bodyBytes == 113 and .generationEvidence.terminalReturn == "ret 0x04" and .generationEvidence.directCallCount == 3 and .generationEvidence.importCallCount == 2)) | length) == 1 and
  ($rows | map(select(.name == "sub_1bb20" and .generationEvidence.bodyBytes == 131 and .generationEvidence.directCallCount == 4 and .generationEvidence.importCallCount == 4 and (.generationEvidence.importCallOffsets | join(",")) == "10,38,50,62")) | length) == 1 and
  ($rows | map(select(.name == "sub_78d6" and .generationEvidence.importCallCount >= 1)) | length) == 1 and
  all($rows[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))
' "$TMP_DIR/source-synthesis-bounded-import-call-terminal-masm/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bounded-leading-return-slice-masm")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bounded-leading-return-slice-masm-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bounded-leading-return-slice-masm-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bounded-leading-return-slice-masm" \
  --compiler msvc \
  --limit 124 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 124 and .semanticGeneratedCandidates == 124 and .semanticCodeSliceMatchedCandidates == 124 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bounded-leading-return-slice-masm/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '
  [.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bounded-leading-return-slice-masm" and .variant == "masm-bounded-leading-return-slice" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.sourceSliceKind == "leading-return-prefix" and .generationEvidence.boundaryRepair == "split-leading-return-prefix" and .generationEvidence.claimBoundary == "source-slice parity only; do not count as recovered full function extent" and .generationEvidence.maxBodyBytes == 160 and .generationEvidence.sourceTier == "generated MASM byte-emission source-slice parity fallback; original target slice continues after the returned prefix" and .candidateCompile.compilerRoot == $vc_root)] as $rows |
  ($rows | length) == 124 and
  ($rows | map(select(.name == "sub_f590" and .generationEvidence.bodyBytes == 33 and .generationEvidence.originalSliceBytes == 542 and .generationEvidence.terminalReturn == "ret" and .generationEvidence.trailingExecutableBytes == 509 and (.generationEvidence.conditionalJumpOffsets | join(",")) == "21")) | length) == 1 and
  ($rows | map(select(.name == "sub_2c00" and .generationEvidence.bodyBytes == 130 and .generationEvidence.originalSliceBytes == 2138 and .generationEvidence.trailingExecutableOffset == 130 and .generationEvidence.trailingExecutableBytes == 2008)) | length) == 1 and
  ($rows | map(select(.name == "sub_16830" and .generationEvidence.bodyBytes == 32)) | length) == 1 and
  ($rows | map(select(.name == "sub_1adf0" and .generationEvidence.bodyBytes == 50)) | length) == 1 and
  ($rows | map(select(.name == "sub_84c2" and .generationEvidence.bodyBytes == 128 and .generationEvidence.directCallCount == 4 and .generationEvidence.importCallCount == 0 and (.generationEvidence.jumpLikeOffsets | join(",")) == "30,88")) | length) == 1 and
  ($rows | map(select(.name == "sub_bed3" and .generationEvidence.bodyBytes == 122 and .generationEvidence.originalSliceBytes == 131 and .generationEvidence.directCallCount == 3 and .generationEvidence.trailingExecutableBytes == 9)) | length) == 1 and
  all($rows[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))
' "$TMP_DIR/source-synthesis-bounded-leading-return-slice-masm/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "extended-terminal-body-masm")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/extended-terminal-body-masm-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/extended-terminal-body-masm-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-extended-terminal-body-masm" \
  --compiler msvc \
  --limit 23 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 23 and .semanticGeneratedCandidates == 23 and .semanticCodeSliceMatchedCandidates == 23 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-extended-terminal-body-masm/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '
  [.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "extended-terminal-body-masm" and .variant == "masm-extended-terminal-body" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.sourceSliceKind == "extended-terminal-body" and .generationEvidence.claimBoundary == "byte-authoritative terminal-body parity only; not high-level recovered C" and .generationEvidence.maxBodyBytes == 512 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback for extended terminal body; not high-level recovered C" and .candidateCompile.compilerRoot == $vc_root)] as $rows |
  ($rows | length) == 23 and
  ($rows | map(.name) | sort) == ["sub_11f70","sub_1580","sub_16680","sub_1770","sub_18a0","sub_1a950","sub_1aa20","sub_1f350","sub_2920","sub_2a00","sub_6200","sub_796b","sub_7a9c","sub_8973","sub_8b05","sub_909c","sub_93b9","sub_9588","sub_9659","sub_99b8","sub_bbaa","sub_d0db","sub_d30b"] and
  ($rows | map(select(.name == "sub_9659" and .generationEvidence.bodyBytes == 442 and .generationEvidence.directCallCount == 12 and .generationEvidence.importCallCount == 6 and (.generationEvidence.jumpLikeOffsets | join(",")) == "8,52,188,215,321,363")) | length) == 1 and
  ($rows | map(select(.name == "sub_1f350" and .generationEvidence.bodyBytes == 383 and .generationEvidence.terminalReturn == "ret 0x2c" and .generationEvidence.terminalReturnOffset == 380)) | length) == 1 and
  ($rows | map(select(.name == "sub_2920" and .generationEvidence.bodyBytes == 219 and .generationEvidence.directCallCount == 0 and .generationEvidence.importCallCount == 0)) | length) == 1 and
  all($rows[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))
' "$TMP_DIR/source-synthesis-extended-terminal-body-masm/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "bink-surface-type-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bink-surface-type-forwarder-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bink-surface-type-forwarder-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bink-surface-type-forwarder" \
  --compiler msvc \
  --limit 3 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 3 and .semanticGeneratedCandidates == 3 and .semanticCodeSliceMatchedCandidates == 3 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bink-surface-type-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '
  [.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "bink-surface-type-forwarder" and .variant == "masm-bink-surface-type-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .candidateCompile.compilerRoot == $vc_root)] as $rows |
  ($rows | length) == 3 and
  ($rows | map(.name) | sort) == ["_BinkDDSurfaceType@4","_BinkDX8SurfaceType@4","sub_118c0"] and
  ($rows | map(select(.generationEvidence.export == "BinkDDSurfaceType" and .generationEvidence.surfaceApi == "DirectDraw" and .generationEvidence.bodyBytes == 453 and .generationEvidence.stdcallStackBytes == 4 and .generationEvidence.stackArgCount == 1 and .generationEvidence.surfacePointerArgIndex == 1 and .generationEvidence.queryVtableOffset == 84 and .generationEvidence.descriptorBytes == 32 and .generationEvidence.fourCcFieldOffset == 12 and .generationEvidence.rgbBitCountFieldOffset == 12 and .generationEvidence.redMaskFieldOffset == 16 and .generationEvidence.greenMaskFieldOffset == 20 and .generationEvidence.blueMaskFieldOffset == 24 and .generationEvidence.alphaMaskFieldOffset == 28 and .generationEvidence.failureReturnValue == -1 and .generationEvidence.returnInstruction == "ret 0x04" and .generationEvidence.targetByteSpan.length == 453 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkDDSurfaceType forwarding wrapper bytes")) | length) == 2 and
  ($rows | map(select(.generationEvidence.export == "BinkDX8SurfaceType" and .generationEvidence.surfaceApi == "Direct3D8" and .generationEvidence.bodyBytes == 220 and .generationEvidence.stdcallStackBytes == 4 and .generationEvidence.stackArgCount == 1 and .generationEvidence.surfacePointerArgIndex == 1 and .generationEvidence.queryVtableOffset == 32 and .generationEvidence.descriptorBytes == 32 and .generationEvidence.formatFieldOffset == 4 and .generationEvidence.jumpTableAddress == "0x30011b50" and .generationEvidence.embeddedJumpTableBytes == 28 and .generationEvidence.failureReturnValue == -1 and .generationEvidence.returnInstruction == "ret 0x04" and .generationEvidence.targetByteSpan.length == 220 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkDX8SurfaceType forwarding wrapper bytes")) | length) == 1 and
  all($rows[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))
' "$TMP_DIR/source-synthesis-bink-surface-type-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "rad-aligned-malloc-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/rad-aligned-malloc-forwarder-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/rad-aligned-malloc-forwarder-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-rad-aligned-malloc-forwarder" \
  --compiler msvc \
  --limit 2 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 2 and .semanticGeneratedCandidates == 2 and .semanticCodeSliceMatchedCandidates == 2 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-rad-aligned-malloc-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "rad-aligned-malloc-forwarder" and .variant == "masm-rad-aligned-malloc-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.export == "radmalloc" and .generationEvidence.bodyBytes == 106 and .generationEvidence.stdcallStackBytes == 4 and .generationEvidence.stackArgCount == 1 and .generationEvidence.sizeArgIndex == 1 and .generationEvidence.invalidSizeSentinel == "0xffffffff" and .generationEvidence.customMallocGlobalAddress == "0x30058080" and .generationEvidence.customFreeGlobalAddress == "0x30058084" and .generationEvidence.fallbackMallocCallOffset == 46 and .generationEvidence.fallbackMallocCallDisplacement == 22852 and .generationEvidence.overAllocationBytes == 64 and .generationEvidence.alignmentBytes == 64 and .generationEvidence.alignmentMask == "0x1f" and .generationEvidence.customAllocatorMarker == 3 and .generationEvidence.fallbackAllocatorMarker == 0 and .generationEvidence.allocatorMarkerOffset == -2 and .generationEvidence.alignmentDeltaOffset == -1 and .generationEvidence.customFreePointerOffset == -8 and .generationEvidence.nullReturnValue == 0 and .generationEvidence.returnInstruction == "ret 0x04" and .generationEvidence.targetByteSpan.length == 106 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded radmalloc forwarding wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 2 and (map(.name) | sort) == ["_radmalloc@4","sub_1020"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-rad-aligned-malloc-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "rad-aligned-free-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/rad-aligned-free-forwarder-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/rad-aligned-free-forwarder-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-rad-aligned-free-forwarder" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-rad-aligned-free-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "rad-aligned-free-forwarder" and .variant == "masm-rad-aligned-free-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.export == "radfree" and .generationEvidence.bodyBytes == 41 and .generationEvidence.stdcallStackBytes == 4 and .generationEvidence.stackArgCount == 1 and .generationEvidence.pointerArgIndex == 1 and .generationEvidence.customAllocatorMarker == 3 and .generationEvidence.customAllocatorMarkerOffset == -2 and .generationEvidence.alignmentDeltaOffset == -1 and .generationEvidence.customFreePointerOffset == -8 and .generationEvidence.customFreeTailJumpOffset == 26 and .generationEvidence.fallbackFreeCallOffset == 32 and .generationEvidence.fallbackFreeCallDisplacement == 22584 and .generationEvidence.fallbackFreeTargetAddress == "0x300068ed" and .generationEvidence.freePointerRewriteStackOffset == 4 and .generationEvidence.nullPointerNoop == true and .generationEvidence.returnInstruction == "ret 0x04" and .generationEvidence.targetByteSpan.length == 41 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded radfree forwarding wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and (map(.name) | sort) == ["_radfree@4"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-rad-aligned-free-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "rad-direct-free-wrapper")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/rad-direct-free-wrapper-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/rad-direct-free-wrapper-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-rad-direct-free-wrapper" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-rad-direct-free-wrapper/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "rad-direct-free-wrapper" and .variant == "masm-rad-direct-free-wrapper" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .callconv == "cdecl" and .generationEvidence.callconv == "cdecl" and .generationEvidence.bodyBytes == 56 and .generationEvidence.stackArgCount == 1 and .generationEvidence.pointerArgIndex == 1 and .generationEvidence.modeGlobalAddress == "0x30058450" and .generationEvidence.modeCustomCleanupValue == 3 and .generationEvidence.fallbackHeapGlobalAddress == "0x3005844c" and .generationEvidence.fallbackFreeImportAddress == "0x3004a134" and .generationEvidence.customProbeCallOffset == 19 and .generationEvidence.customProbeCallDisplacement == 1243 and .generationEvidence.customProbeTargetAddress == "0x30006dd0" and .generationEvidence.customCleanupCallOffset == 31 and .generationEvidence.customCleanupCallDisplacement == 1274 and .generationEvidence.customCleanupTargetAddress == "0x30006e11" and .generationEvidence.fallbackFreeCallOffset == 48 and .generationEvidence.nullPointerNoop == true and .generationEvidence.customCleanupReturnPath == "ret" and .generationEvidence.returnInstruction == "ret" and .generationEvidence.targetByteSpan.length == 56 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded RAD direct free wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and (map(.name) | sort) == ["sub_68ed"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-rad-direct-free-wrapper/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "rad-timer-read-forwarder")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/rad-timer-read-forwarder-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/rad-timer-read-forwarder-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-rad-timer-read-forwarder" \
  --compiler msvc \
  --limit 2 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 2 and .semanticGeneratedCandidates == 2 and .semanticCodeSliceMatchedCandidates == 2 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-rad-timer-read-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "rad-timer-read-forwarder" and .variant == "masm-rad-timer-read-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .callconv == "cdecl" and .generationEvidence.export == "RADTimerRead" and .generationEvidence.callconv == "cdecl" and .generationEvidence.bodyBytes == 285 and .generationEvidence.stackArgCount == 0 and .generationEvidence.localScratchBytes == 8 and .generationEvidence.initFlagGlobalAddress == "0x300651e0" and .generationEvidence.performanceFrequencyLowGlobalAddress == "0x30055f30" and .generationEvidence.performanceFrequencyHighGlobalAddress == "0x30055f34" and .generationEvidence.performanceCounterBaseLowGlobalAddress == "0x30055f28" and .generationEvidence.performanceCounterBaseHighGlobalAddress == "0x30055f2c" and .generationEvidence.lastCounterGlobalAddress == "0x30055f38" and .generationEvidence.timerBaseGlobalAddress == "0x30055f24" and .generationEvidence.driftAccumulatorGlobalAddress == "0x300651e4" and .generationEvidence.queryPerformanceFrequencyImportAddress == "0x3004a0d4" and .generationEvidence.queryPerformanceCounterImportAddress == "0x3004a0cc" and .generationEvidence.timeGetTimeImportAddress == "0x3004a0d0" and .generationEvidence.fallbackTimerImportAddress == "0x3004a1d8" and .generationEvidence.scaleNumerator == 1000 and .generationEvidence.driftClampTicks == 200 and .generationEvidence.wrapGuardDelta == "0xc0000000" and .generationEvidence.returnInstruction == "ret" and .generationEvidence.targetByteSpan.length == 285 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded RADTimerRead timer wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 2 and (map(.name) | sort) == ["_RADTimerRead@0","sub_17c80"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-rad-timer-read-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule | test("bink-buffer-(check-win-pos|close|lock|set-scale)-forwarder"))' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/bink-buffer-expanded-forwarder-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/bink-buffer-expanded-forwarder-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-bink-buffer-expanded-forwarder" \
  --compiler msvc \
  --limit 6 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 6 and .semanticGeneratedCandidates == 6 and .semanticCodeSliceMatchedCandidates == 6 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-bink-buffer-expanded-forwarder/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '
  def proven:
    .compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and
    .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and
    .candidateCompile.compilerRoot == $vc_root and
    (.targetObjectRelocations | length) == 0 and
    any(.candidateCompile.command[]; test("ml\\.exe$"));
  [
    .[] | select(proven)
  ] as $rows |
  ($rows | length) == 6 and
  ($rows | map(select(.rule == "bink-buffer-check-win-pos-forwarder" and .variant == "masm-bink-buffer-check-win-pos-forwarder" and .generationEvidence.export == "BinkBufferCheckWinPos" and .generationEvidence.stdcallStackBytes == 12 and .generationEvidence.stackArgCount == 3 and .generationEvidence.bufferPointerArgIndex == 1 and .generationEvidence.xPointerArgIndex == 2 and .generationEvidence.yPointerArgIndex == 3 and .generationEvidence.xBaseFieldOffset == 28 and .generationEvidence.yBaseFieldOffset == 32 and .generationEvidence.clipEnabledFieldOffset == 132 and .generationEvidence.alignmentModeGlobalAddress == "0x30068c80" and .generationEvidence.globalWidthLimitAddress == "0x30055cb4" and .generationEvidence.globalHeightLimitAddress == "0x30055cb0" and .generationEvidence.returnInstruction == "ret 0x0c" and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkBufferCheckWinPos forwarding wrapper bytes")) | length) == 1 and
  ($rows | map(select(.rule == "bink-buffer-close-forwarder" and .variant == "masm-bink-buffer-close-forwarder" and .generationEvidence.export == "BinkBufferClose" and .generationEvidence.stdcallStackBytes == 4 and .generationEvidence.stackArgCount == 1 and .generationEvidence.bufferPointerArgIndex == 1 and .generationEvidence.surfaceFieldOffset == 72 and .generationEvidence.primaryReleaseFlagOffset == 104 and .generationEvidence.secondaryReleaseFlagOffset == 108 and .generationEvidence.helperHandleFieldOffset == 160 and .generationEvidence.helperContextFieldOffset == 164 and .generationEvidence.helperResourceFieldOffset == 144 and .generationEvidence.helperAllocationFieldOffset == 156 and .generationEvidence.optionalCloseFieldOffset == 136 and .generationEvidence.globalReferenceFieldOffset == 140 and .generationEvidence.globalBackBufferAddress == "0x30068c70" and .generationEvidence.globalRefCountAddress == "0x30068c9c" and .generationEvidence.globalResourceAddress == "0x30068c98" and .generationEvidence.directFreeCallOffset == 170 and .generationEvidence.directFreeCallDisplacement == -42673 and .generationEvidence.optionalCloseCallOffset == 188 and .generationEvidence.optionalCloseCallDisplacement == -6736 and .generationEvidence.finalFreeCallOffset == 280 and .generationEvidence.finalFreeCallDisplacement == -42785 and .generationEvidence.clearedDwordCount == 42 and .generationEvidence.returnInstruction == "ret 0x04" and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkBufferClose forwarding wrapper bytes")) | length) == 1 and
  ($rows | map(select(.rule == "bink-buffer-lock-forwarder" and .variant == "masm-bink-buffer-lock-forwarder" and .generationEvidence.export == "BinkBufferLock" and .generationEvidence.stdcallStackBytes == 4 and .generationEvidence.stackArgCount == 1 and .generationEvidence.bufferPointerArgIndex == 1 and .generationEvidence.localScratchBytes == 108 and .generationEvidence.surfaceFieldOffset == 72 and .generationEvidence.lockStateFieldOffset == 100 and .generationEvidence.prelockFlagFieldOffset == 116 and .generationEvidence.prelockResultFieldOffset == 120 and .generationEvidence.callbackArgFieldOffset == 124 and .generationEvidence.dirtyFlagFieldOffset == 16 and .generationEvidence.outputPointerFieldOffset == 20 and .generationEvidence.outputPitchFieldOffset == 24 and .generationEvidence.fallbackGuardFieldOffset == 144 and .generationEvidence.fallbackPointerFieldOffset == 148 and .generationEvidence.fallbackPitchFieldOffset == 152 and .generationEvidence.globalBytesPerPixelAddress == "0x30068c80" and .generationEvidence.prelockCallOffset == 86 and .generationEvidence.prelockCallDisplacement == -5342 and .generationEvidence.unlockCleanupCallOffset == 166 and .generationEvidence.unlockCleanupCallDisplacement == -5226 and .generationEvidence.surfaceLostHresult == "0x887601c2" and .generationEvidence.dirtyFlagMask == "0x80000000" and .generationEvidence.nullReturnValue == 0 and .generationEvidence.failureReturnValue == 0 and .generationEvidence.successReturnValue == 1 and .generationEvidence.returnInstruction == "ret 0x04" and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkBufferLock forwarding wrapper bytes")) | length) == 2 and
  ($rows | map(select(.rule == "bink-buffer-set-scale-forwarder" and .variant == "masm-bink-buffer-set-scale-forwarder" and .generationEvidence.export == "BinkBufferSetScale" and .generationEvidence.stdcallStackBytes == 12 and .generationEvidence.stackArgCount == 3 and .generationEvidence.bufferPointerArgIndex == 1 and .generationEvidence.widthArgIndex == 2 and .generationEvidence.heightArgIndex == 3 and .generationEvidence.globalWidthFallbackAddress == "0x30055cb4" and .generationEvidence.globalHeightFallbackAddress == "0x30055cb0" and .generationEvidence.scaleFlagsFieldOffset == 56 and .generationEvidence.scaledWidthFieldOffset == 60 and .generationEvidence.scaledHeightFieldOffset == 64 and .generationEvidence.xOffsetFieldOffset == 48 and .generationEvidence.yOffsetFieldOffset == 52 and .generationEvidence.rightFieldOffset == 8 and .generationEvidence.bottomFieldOffset == 12 and .generationEvidence.nullReturnValue == 0 and .generationEvidence.successReturnValue == 1 and .generationEvidence.returnInstruction == "ret 0x0c" and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded BinkBufferSetScale forwarding wrapper bytes")) | length) == 2 and
  ($rows | map(select(.rule == "bink-buffer-lock-forwarder") | .name) | sort) == ["_BinkBufferLock@4","sub_11020"] and
  ($rows | map(select(.rule == "bink-buffer-set-scale-forwarder") | .name) | sort) == ["_BinkBufferSetScale@12","sub_115c0"]
' "$TMP_DIR/source-synthesis-bink-buffer-expanded-forwarder/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "x87-temp-i16-return")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/x87-temp-i16-return-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/x87-temp-i16-return-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-x87-temp-i16-return" \
  --compiler msvc \
  --limit 2 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 2 and .semanticGeneratedCandidates == 2 and .semanticCodeSliceMatchedCandidates == 2 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-x87-temp-i16-return/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "x87-temp-i16-return" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.preservedRegister == "ecx" and .generationEvidence.returnSource == "sign-extended-low-word-of-temp" and .generationEvidence.tempBytes == 8 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded x87 temp i16 return bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 2 and (map(.variant) | sort) == ["masm-x87-temp-i16-return-fnclex-after-spill","masm-x87-temp-i16-return-fwait-before-spill"] and (map(.generationEvidence.x87StatusOperation) | sort) == ["fnclex-after-spill","fwait-before-spill"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-x87-temp-i16-return/attempts.jsonl" >/dev/null

jq -e '[select(.automaticGenerator.rule == "x87-pop-return-zero")] | length == 0' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "x87-control-word-masked-setter")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/x87-control-word-masked-setter-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/x87-control-word-masked-setter-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-x87-control-word-masked-setter" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-x87-control-word-masked-setter/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "x87-control-word-masked-setter" and .variant == "masm-x87-control-word-masked-setter" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.valueArgIndex == 1 and .generationEvidence.maskArgIndex == 2 and .generationEvidence.mergeExpression == "(oldControlWord & ~mask) | (value & mask)" and .generationEvidence.returnValue == "sign-extended previous x87 control word" and (.generationEvidence.x87Operations | join(",") == "fstcw [ebp-4],fldcw [ebp+0x0c]") and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded x87 control-word masked setter bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-x87-control-word-masked-setter/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "x87-double-exponent-adjust-return")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/x87-double-exponent-adjust-return-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/x87-double-exponent-adjust-return-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-x87-double-exponent-adjust-return" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-x87-double-exponent-adjust-return/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "x87-double-exponent-adjust-return" and .variant == "masm-x87-double-exponent-adjust-return" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.doubleArgIndex == 1 and .generationEvidence.exponentArgIndex == 3 and .generationEvidence.exponentBiasAddend == 1022 and .generationEvidence.exponentShift == 4 and .generationEvidence.preservedExponentWordMask == "0xffff800f" and .generationEvidence.exponentWordTempOffset == -2 and .generationEvidence.returnRegister == "st(0)" and (.generationEvidence.x87Operations | join(",") == "fld qword ptr [ebp+0x08],fstp qword ptr [ebp-0x08],fld qword ptr [ebp-0x08]") and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded x87 double exponent-adjust return bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-x87-double-exponent-adjust-return/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "x87-round-stack-double-return")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/x87-round-stack-double-return-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/x87-round-stack-double-return-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-x87-round-stack-double-return" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-x87-round-stack-double-return/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "x87-round-stack-double-return" and .variant == "masm-x87-round-stack-double-return" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.argumentType == "double" and .generationEvidence.scratchBytes == 8 and .generationEvidence.returnRegister == "st(0)" and .generationEvidence.roundingMode == "current x87 control word" and (.generationEvidence.x87Operations | join(",") == "fld qword ptr [esp+0x0c],frndint,fstp qword ptr [esp],fld qword ptr [esp]") and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded x87 round stack-double return bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-x87-round-stack-double-return/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "stack-arg-range-global-mode-setter")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/stack-arg-range-global-mode-setter-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/stack-arg-range-global-mode-setter-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-stack-arg-range-global-mode-setter" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-stack-arg-range-global-mode-setter/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "stack-arg-range-global-mode-setter" and .variant == "high-level-c-stack-arg-range-global-mode-setter" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.argIndex == 1 and .generationEvidence.globalAddress == "0x30055a1c" and .generationEvidence.equalOneValue == 33 and (.generationEvidence.rangeInput | join(",") == "2,3") and .generationEvidence.rangeValue == 34 and .generationEvidence.noStoreWhen == "arg1 <= 0 or arg1 > 3" and .generationEvidence.sourceTier == "generated high-level C parity match for decoded stack-argument range global mode setter" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("cl\\.exe$")))' "$TMP_DIR/source-synthesis-stack-arg-range-global-mode-setter/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "ebx-bitfield-mode-remap")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/ebx-bitfield-mode-remap-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/ebx-bitfield-mode-remap-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-ebx-bitfield-mode-remap" \
  --compiler msvc \
  --limit 2 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 2 and .semanticGeneratedCandidates == 2 and .semanticCodeSliceMatchedCandidates == 2 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-ebx-bitfield-mode-remap/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "ebx-bitfield-mode-remap" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.inputRegister == "ebx" and .generationEvidence.outputRegister == "eax" and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded live-ebx bitfield remap bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 2 and (map(.variant) | sort) == ["masm-ebx-bitfield-mode-remap-bf56","masm-ebx-bitfield-mode-remap-bfe8"] and (map(.generationEvidence.variant) | sort) == ["bf56","bfe8"] and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-ebx-bitfield-mode-remap/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "push-stack-stack-const-call-wrapper")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/push-stack-stack-const-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/push-stack-stack-const-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-push-stack-stack-const" \
  --compiler msvc \
  --limit 2 \
  --max-variants-per-function 2 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 2 and .semanticGeneratedCandidates == 4 and .semanticCodeSliceMatchedCandidates == 4 and .semanticMismatchedCandidates == 4 and .compileFailedCandidates == 0 and .acceptedCandidates == 0 and .generatedBySourceQuality == {"high-level-c": 2, "inline-asm-c": 2} and .semanticCodeSliceMatchedBySourceQuality == {"high-level-c": 2, "inline-asm-c": 2} and .semanticMismatchedBySourceQuality == {"inline-asm-c": 4}' "$TMP_DIR/source-synthesis-push-stack-stack-const/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "push-stack-stack-const-call-wrapper" and .variant == "cdecl-two-stack-args-plus-constant-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .sourceQuality == "high-level-c" and .candidateCompile.compilerRoot == $vc_root)] | length == 2 and (map(.generationEvidence.constant) | sort) == ["0x300553b0","0x300553c8"] and all(.[]; (.targetObjectRelocations | length) == 1 and .targetObjectRelocations[0].instruction == "call")' "$TMP_DIR/source-synthesis-push-stack-stack-const/attempts.jsonl" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "push-stack-stack-const-call-wrapper" and .variant == "naked-cdecl-two-stack-args-plus-constant-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .sourceQuality == "inline-asm-c" and .generationEvidence.callTarget == "0x3000bb98" and .generationEvidence.callOffset == 13 and .generationEvidence.sourceTier == "generated inline-assembly parity source for direct stack-slot pushes" and .candidateCompile.compilerRoot == $vc_root)] | length == 2 and (map(.generationEvidence.constant) | sort) == ["0x300553b0","0x300553c8"] and all(.[]; (.targetObjectRelocations | length) == 1 and .targetObjectRelocations[0].instruction == "call")' "$TMP_DIR/source-synthesis-push-stack-stack-const/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "push-imm32-pair-call-wrapper")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/push-imm32-pair-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/push-imm32-pair-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-push-imm32-pair" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-push-imm32-pair/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "push-imm32-pair-call-wrapper" and .variant == "cdecl-two-imm32-forwarder" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .sourceQuality == "high-level-c" and .generationEvidence.callTarget == "0x3000c0a8" and .generationEvidence.callOffset == 10 and .generationEvidence.firstConstant == "0x00030000" and .generationEvidence.secondConstant == "0x00010000" and .generationEvidence.sourceTier == "generated high-level C candidate for decoded imm32 pair call wrapper" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and all(.[]; (.targetObjectRelocations | length) == 1 and .targetObjectRelocations[0].instruction == "call" and .targetObjectRelocations[0].offset == 10)' "$TMP_DIR/source-synthesis-push-imm32-pair/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "u32-add-store-wrap-flag")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/u32-add-store-wrap-flag-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/u32-add-store-wrap-flag-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-u32-add-store-wrap-flag" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-u32-add-store-wrap-flag/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "u32-add-store-wrap-flag" and .variant == "high-level-c-u32-add-store-wrap-flag" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.firstArgIndex == 1 and .generationEvidence.secondArgIndex == 2 and .generationEvidence.outArgIndex == 3 and .generationEvidence.returnFlag == "1 when unsigned first + second wraps below either operand, else 0" and .generationEvidence.sourceTier == "generated high-level C parity match for decoded u32 add-store wrap flag helper" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and all(.[]; (.targetObjectRelocations | length) == 0)' "$TMP_DIR/source-synthesis-u32-add-store-wrap-flag/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "u96-bit-tail-clear-check")' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/u96-bit-tail-clear-check-task.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/u96-bit-tail-clear-check-task.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-u96-bit-tail-clear-check" \
  --compiler msvc \
  --limit 1 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 1 and .semanticGeneratedCandidates == 1 and .semanticCodeSliceMatchedCandidates == 1 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-u96-bit-tail-clear-check/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "u96-bit-tail-clear-check" and .variant == "masm-u96-bit-tail-clear-check" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.baseArgIndex == 1 and .generationEvidence.bitIndexArgIndex == 2 and .generationEvidence.wordBits == 32 and .generationEvidence.wordCount == 3 and .generationEvidence.returnWhenClear == 1 and .generationEvidence.returnWhenAnySet == 0 and .generationEvidence.sourceTier == "generated MASM byte-emission parity fallback with decoded 96-bit tail-clear predicate bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 1 and all(.[]; (.targetObjectRelocations | length) == 0 and any(.candidateCompile.command[]; test("ml\\.exe$")))' "$TMP_DIR/source-synthesis-u96-bit-tail-clear-check/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "stdcall-yuv-blit-format-wrapper" and (.name | test("^_YUV_blit_.*@48$")))' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/yuv-blit-format-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/yuv-blit-format-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-yuv-blit-format" \
  --compiler msvc \
  --limit 5 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 5 and .semanticGeneratedCandidates == 5 and .semanticCodeSliceMatchedCandidates == 5 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-yuv-blit-format/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "stdcall-yuv-blit-format-wrapper" and .variant == "naked-stdcall-yuv-blit-format-wrapper" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.callTarget == "0x300191c0" and .generationEvidence.callOffset == 56 and .generationEvidence.stackBytes == 48 and .generationEvidence.eaxArgIndex == 10 and .generationEvidence.sourceTier == "generated inline-assembly parity fallback with decoded YUV blit wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 5 and (map(.generationEvidence.constant) | sort) == ["0x300646e0","0x300647e0","0x30064ae0","0x30064be0","0x30064ce0"] and all(.[]; (.targetObjectRelocations | length) == 1 and .targetObjectRelocations[0].instruction == "call" and .targetObjectRelocations[0].offset == 56)' "$TMP_DIR/source-synthesis-yuv-blit-format/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "stdcall-yuv-blit-alpha-wrapper" and (.name | test("^_YUV_blit_.*@52$")))' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/yuv-blit-alpha-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/yuv-blit-alpha-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-yuv-blit-alpha" \
  --compiler msvc \
  --limit 4 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 4 and .semanticGeneratedCandidates == 4 and .semanticCodeSliceMatchedCandidates == 4 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-yuv-blit-alpha/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "stdcall-yuv-blit-alpha-wrapper" and .variant == "naked-stdcall-yuv-blit-alpha-wrapper" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.callTarget == "0x300191c0" and .generationEvidence.callOffset == 58 and .generationEvidence.stackBytes == 52 and .generationEvidence.eaxArgIndex == 10 and .generationEvidence.ecxArgIndex == 1 and .generationEvidence.sourceTier == "generated inline-assembly parity fallback with decoded YUV alpha blit wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 4 and (map(.generationEvidence.constant) | sort) == ["0x300648e0","0x300649e0","0x30064de0","0x30064ee0"] and all(.[]; (.targetObjectRelocations | length) == 1 and .targetObjectRelocations[0].instruction == "call" and .targetObjectRelocations[0].offset == 58)' "$TMP_DIR/source-synthesis-yuv-blit-alpha/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "stdcall-yuv-blit-packed-wrapper" and (.name | test("^_YUV_blit_.*@48$")))' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/yuv-blit-packed-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/yuv-blit-packed-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-yuv-blit-packed" \
  --compiler msvc \
  --limit 2 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 2 and .semanticGeneratedCandidates == 2 and .semanticCodeSliceMatchedCandidates == 2 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-yuv-blit-packed/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "stdcall-yuv-blit-packed-wrapper" and .variant == "naked-stdcall-yuv-blit-packed-wrapper" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.callTarget == "0x300191c0" and .generationEvidence.callOffset == 99 and .generationEvidence.stackBytes == 48 and .generationEvidence.sourceTier == "generated inline-assembly parity fallback with decoded packed YUV blit wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 2 and (map(.generationEvidence.constant) | sort) == ["0x30064fe0","0x300650e0"] and all(.[]; (.targetObjectRelocations | length) == 1 and .targetObjectRelocations[0].instruction == "call" and .targetObjectRelocations[0].offset == 99)' "$TMP_DIR/source-synthesis-yuv-blit-packed/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "stdcall-yuv-blit-mask-format-prefix" and (.name | test("^_YUV_blit_.*@56$")))' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/yuv-blit-mask-format-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/yuv-blit-mask-format-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-yuv-blit-mask-format" \
  --compiler msvc \
  --limit 7 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 7 and .semanticGeneratedCandidates == 7 and .semanticCodeSliceMatchedCandidates == 7 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-yuv-blit-mask-format/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "stdcall-yuv-blit-mask-format-prefix" and .variant == "naked-stdcall-yuv-blit-mask-format-prefix" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.callTarget == "0x300195f0" and .generationEvidence.callOffset == 66 and .generationEvidence.stackBytes == 56 and .generationEvidence.targetByteSpan.length == 78 and .generationEvidence.sourceTier == "generated inline-assembly parity fallback with decoded leading YUV mask-format wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 7 and (map(.generationEvidence.constant) | sort) == ["0x300646e0","0x300647e0","0x30064ae0","0x30064be0","0x30064ce0","0x30064fe0","0x300650e0"] and all(.[]; (.targetObjectRelocations | length) == 1 and .targetObjectRelocations[0].instruction == "call" and .targetObjectRelocations[0].offset == 66)' "$TMP_DIR/source-synthesis-yuv-blit-mask-format/attempts.jsonl" >/dev/null

jq -c 'select(.automaticGenerator.rule == "stdcall-yuv-blit-mask-alpha-prefix" and (.name | test("^_YUV_blit_.*@60$")))' \
  "$TMP_DIR/recover/source-generation/tasks.jsonl" >"$TMP_DIR/yuv-blit-mask-alpha-tasks.jsonl"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" VC_ROOT="$VC_ROOT" python3 -m mizuchi_re.source_parity_synthesize \
  --source-tasks "$TMP_DIR/yuv-blit-mask-alpha-tasks.jsonl" \
  --out-dir "$TMP_DIR/source-synthesis-yuv-blit-mask-alpha" \
  --compiler msvc \
  --limit 4 \
  --max-variants-per-function 1 \
  --semantic-only >/dev/null

jq -e '.compiler == "msvc" and .semanticOnly == true and .skipBoundarySuspect == false and .inspectedFunctions == 4 and .semanticGeneratedCandidates == 4 and .semanticCodeSliceMatchedCandidates == 4 and .semanticMismatchedCandidates == 0 and .compileFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/source-synthesis-yuv-blit-mask-alpha/summary.json" >/dev/null
jq -s -e --arg vc_root "$VC_ROOT" '[.[] | select(.compiler == "msvc" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "stdcall-yuv-blit-mask-alpha-prefix" and .variant == "naked-stdcall-yuv-blit-mask-alpha-prefix" and .semanticSource == true and .status == "code-slice-matched" and .differences == 0 and .generationEvidence.callTarget == "0x300195f0" and .generationEvidence.callOffset == 68 and .generationEvidence.stackBytes == 60 and .generationEvidence.targetByteSpan.length == 80 and .generationEvidence.sourceTier == "generated inline-assembly parity fallback with decoded leading YUV mask-alpha wrapper bytes" and .candidateCompile.compilerRoot == $vc_root)] | length == 4 and (map(.generationEvidence.constant) | sort) == ["0x300648e0","0x300649e0","0x30064de0","0x30064ee0"] and all(.[]; (.targetObjectRelocations | length) == 1 and .targetObjectRelocations[0].instruction == "call" and .targetObjectRelocations[0].offset == 68)' "$TMP_DIR/source-synthesis-yuv-blit-mask-alpha/attempts.jsonl" >/dev/null

echo "ok"
