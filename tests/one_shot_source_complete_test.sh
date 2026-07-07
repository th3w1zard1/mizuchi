#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PACKAGE="$TMP_DIR/one-shot-true"
RESULT="$TMP_DIR/result.json"

"$ROOT/scripts/decomp-cli.sh" one-shot-source \
  --binary /bin/true \
  --out "$PACKAGE" \
  --complete \
  --timeout 30 \
  --result-out "$RESULT" >/dev/null

jq -e '.status == "authoritative"' "$RESULT" >/dev/null
jq -e '.standaloneVerifier.ok == true' "$RESULT" >/dev/null
jq -e '.archiveVerifier.status == "matched"' "$RESULT" >/dev/null
jq -e '.completeStatus.ok == true' "$RESULT" >/dev/null
jq -e '.completeStatus.byteAccurateResponseProof == "matched"' "$RESULT" >/dev/null
jq -e '.completeStatus.bundleVerifier == "matched"' "$RESULT" >/dev/null

jq -e '.status == "matched" and .ok == true and .matchedCount == .taskCount and .taskCount > 0' \
  "$PACKAGE/receipts/byte-accurate-response-proof.json" >/dev/null
jq -e '.status == "matched"' "$PACKAGE/receipts/bundle-verify.json" >/dev/null
grep -q 'verify_one_shot_response_contract' "$PACKAGE/VERIFY.py"
grep -q 'JSON replay report shapes mismatch' "$PACKAGE/VERIFY.py"
grep -q 'python3 VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json --allow-build-command' \
  "$PACKAGE/README.md"
grep -q 'candidate-build.env' "$PACKAGE/README.md"
grep -q 'Response replay entrypoints' "$PACKAGE/receipts/proof.md"
grep -q 'responseJsonImportWithBuildCommand' "$PACKAGE/receipts/proof.md"
jq -e '.oneShotReceiptRefresher.path == "REFRESH_RECONSTRUCTION_RECEIPTS.py" and (.oneShotReceiptRefresher.sha256 | type == "string")' \
  "$PACKAGE/receipts/bundle-verify.json" >/dev/null
jq -e '.oneShotReceiptRefresher.path == "REFRESH_RECONSTRUCTION_RECEIPTS.py" and (.oneShotReceiptRefresher.sha256 | type == "string")' \
  "$PACKAGE/PACKAGE_PROOF.json" >/dev/null
jq -e '.oneShotReceiptRefresher == input.oneShotReceiptRefresher' \
  "$PACKAGE/receipts/deliverable.json" "$PACKAGE/PACKAGE_PROOF.json" >/dev/null
jq -e '.commands.refreshReceipts == "./REFRESH_RECONSTRUCTION_RECEIPTS.py"' \
  "$PACKAGE/ONE_SHOT_RECONSTRUCTION_REQUEST.json" >/dev/null
jq -e '
  .sourceArtifacts.functionReconstructionTasks.path == "FUNCTION_RECONSTRUCTION_TASKS.json" and
  .sourceArtifacts.markdownRequest.path == "ONE_SHOT_RECONSTRUCTION_REQUEST.md" and
  .sourceArtifacts.candidateImporter.path == "IMPORT_RECONSTRUCTION_CANDIDATES.py" and
  .sourceArtifacts.byteAccurateResponseExporter.path == "EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py" and
  .sourceArtifacts.jsonImporter.path == "IMPORT_RECONSTRUCTION_RESPONSE_JSON.py" and
  .sourceArtifacts.jsonValidator.path == "VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py" and
  .sourceArtifacts.receiptRefresher.path == "REFRESH_RECONSTRUCTION_RECEIPTS.py" and
  .sourceArtifacts.candidateReplay.path == "REPLAY_RECONSTRUCTION_CANDIDATES.py" and
  .sourceArtifacts.semanticAuthorityEvaluator.path == "EVALUATE_SEMANTIC_SOURCE_AUTHORITY.py" and
  ([.sourceArtifacts[] | .sha256 | type] | all(. == "string"))
' \
  "$PACKAGE/ONE_SHOT_RECONSTRUCTION_BUNDLE.json" >/dev/null
jq -e '.receiptRefreshCommand == "./REFRESH_RECONSTRUCTION_RECEIPTS.py" and (.receiptRefresherSha256 | type == "string")' \
  "$PACKAGE/RECONSTRUCTION_RESPONSE_TEMPLATE.json" >/dev/null
jq -e '
  .jsonImportCommandWithBuildCommand == "./IMPORT_RECONSTRUCTION_RESPONSE_JSON.py --response-json <response.json> --allow-build-command" and
  .jsonValidateCommandWithBuildCommand == "./VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json <response.json> --allow-build-command" and
  .jsonResponseShape.files["function-reconstruction-tasks/<task>/candidate.c"] == "C source text" and
  (.jsonResponseShape.candidates | not) and
  .jsonStructuredResponseShape.candidates[0].build.command == "optional custom command that writes $CANDIDATE_OUTPUT; requires --allow-build-command" and
  .jsonReplayReportShapes.preflight.buildOverrideExpectedPaths[0] == "expected candidate paths with build overrides" and
  .jsonReplayReportShapes.import.buildOverrideExtraPaths[0] == "extra candidate paths with build overrides that were not imported"
' "$PACKAGE/RECONSTRUCTION_RESPONSE_TEMPLATE.json" >/dev/null
jq -e '
  .preferredResponse.replayReportShapes.preflight.schema == "mizuchi.one-shot-source-reconstruction-json-preflight.v1" and
  .preferredResponse.replayReportShapes.import.schema == "mizuchi.one-shot-source-reconstruction-json-import.v1"
' "$PACKAGE/ONE_SHOT_RECONSTRUCTION_REQUEST.json" >/dev/null
jq -e '
  .responseTemplate.jsonReplayReportShapes.preflight.buildOverridePaths[0] == "all response candidate paths with build overrides" and
  .responseTemplate.jsonReplayReportShapes.import.buildOverrideCount == "number of response candidate paths with build overrides, including extras"
' "$PACKAGE/ONE_SHOT_RECONSTRUCTION_BUNDLE.json" >/dev/null
jq -e '.entrypoints.byteAccurateResponseProof == ["python3 PROVE_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py"]' \
  "$PACKAGE/PROOF_COMMANDS.json" >/dev/null
jq -e '
  .entrypoints.responseJsonPreflight == ["python3 VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json"] and
  .entrypoints.responseJsonImport == ["python3 IMPORT_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json"] and
  .entrypoints.responseJsonPreflightWithBuildCommand == ["python3 VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json --allow-build-command"] and
  .entrypoints.responseJsonImportWithBuildCommand == ["python3 IMPORT_RECONSTRUCTION_RESPONSE_JSON.py --response-json response.json --allow-build-command"]
' "$PACKAGE/PROOF_COMMANDS.json" >/dev/null
jq -e '.expectedSuccess.byteAccurateResponseProof == "BYTE_ACCURATE_RECONSTRUCTION_RESPONSE_PROOF_OK"' \
  "$PACKAGE/PROOF_COMMANDS.json" >/dev/null
jq -e '
  .expectedSuccess.responseJsonPreflightWithBuildCommand == "same as responseJsonPreflight; permits candidates[].build.command" and
  .expectedSuccess.responseJsonImportWithBuildCommand == "same as responseJsonImport; permits candidates[].build.command"
' "$PACKAGE/PROOF_COMMANDS.json" >/dev/null

"$ROOT/scripts/decomp-cli.sh" one-shot-source-validate \
  --package "$PACKAGE" \
  --require-complete >/dev/null

"$ROOT/scripts/decomp-cli.sh" one-shot-source-deliverable-verify \
  --bundle "$TMP_DIR/one-shot-true.deliverable.tar.gz" \
  --out "$TMP_DIR/deliverable-verify.json" >/dev/null
jq -e '.oneShotReceiptRefresher.path == "REFRESH_RECONSTRUCTION_RECEIPTS.py" and (.oneShotReceiptRefresher.sha256 | type == "string")' \
  "$TMP_DIR/deliverable-verify.json" >/dev/null

grep -q 'CCACHE_DISABLE' "$PACKAGE/function-reconstruction-tasks/"*/VERIFY_CANDIDATE.sh

GOOD_RESPONSE="$TMP_DIR/good-response.json"
BAD_SEMANTIC="$TMP_DIR/bad-semantic-response.json"
BAD_TASK_COUNT="$TMP_DIR/bad-task-count-response.json"
BAD_SOURCE_HASH="$TMP_DIR/bad-source-hash-response.json"
BAD_DUPLICATE_PATH="$TMP_DIR/bad-duplicate-path-response.json"
BAD_MIXED_SHAPE="$TMP_DIR/bad-mixed-shape-response.json"
BAD_METADATA_TYPES="$TMP_DIR/bad-metadata-types-response.json"
BUILD_RESPONSE="$TMP_DIR/build-response.json"
BUILD_PREFLIGHT="$TMP_DIR/build-preflight.json"
BUILD_IMPORT="$TMP_DIR/build-import.json"
COMMAND_RESPONSE="$TMP_DIR/command-response.json"
COMMAND_PREFLIGHT="$TMP_DIR/command-preflight.json"
COMMAND_IMPORT="$TMP_DIR/command-import.json"
EXTRA_RESPONSE="$TMP_DIR/extra-response.json"
EXTRA_PREFLIGHT="$TMP_DIR/extra-preflight.json"
EXTRA_IMPORT="$TMP_DIR/extra-import.json"
DIR_RESPONSE="$TMP_DIR/dir-response"
DIR_IMPORT="$TMP_DIR/dir-import.json"
DIR_EXTRA_IMPORT="$TMP_DIR/dir-extra-import.json"
PARTIAL_PACKAGE="$TMP_DIR/partial-package"
PARTIAL_PREFLIGHT="$TMP_DIR/partial-preflight.json"
PARTIAL_IMPORT="$TMP_DIR/partial-import.json"
BAD_REPLAY_STATUS_PACKAGE="$TMP_DIR/bad-replay-status-package"
BAD_REPLAY_ROW_PACKAGE="$TMP_DIR/bad-replay-row-package"
BAD_REPLAY_ROW_VALIDATE="$TMP_DIR/bad-replay-row-validate.json"
BAD_SEMANTIC_BLOCKERS_PACKAGE="$TMP_DIR/bad-semantic-blockers-package"
BAD_SEMANTIC_BLOCKERS_VALIDATE="$TMP_DIR/bad-semantic-blockers-validate.json"
BAD_SEMANTIC_COUNTS_PACKAGE="$TMP_DIR/bad-semantic-counts-package"
BAD_SEMANTIC_COUNTS_ARCHIVE="$TMP_DIR/bad-semantic-counts-package.tar.gz"
BAD_SEMANTIC_COUNTS_ARCHIVE_VERIFY="$TMP_DIR/bad-semantic-counts-archive-verify.json"
BAD_TEMPLATE_PACKAGE="$TMP_DIR/bad-template-package"
BAD_TEMPLATE_VALIDATE="$TMP_DIR/bad-template-validate.json"
BAD_TEMPLATE_ARCHIVE="$TMP_DIR/bad-template-package.tar.gz"
BAD_TEMPLATE_ARCHIVE_VERIFY="$TMP_DIR/bad-template-archive-verify.json"
BAD_BUILD_ENV_PACKAGE="$TMP_DIR/bad-build-env-package"
BAD_BUILD_ENV_ARCHIVE="$TMP_DIR/bad-build-env-package.tar.gz"
BAD_BUILD_ENV_ARCHIVE_VERIFY="$TMP_DIR/bad-build-env-archive-verify.json"

(cd "$PACKAGE" && python3 EXPORT_BYTE_ACCURATE_RECONSTRUCTION_RESPONSE.py --out "$GOOD_RESPONSE" >/dev/null)
(cd "$PACKAGE" && python3 VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json "$GOOD_RESPONSE" >/dev/null)

python3 - "$GOOD_RESPONSE" "$BAD_SEMANTIC" "$BAD_TASK_COUNT" "$BAD_SOURCE_HASH" "$BAD_METADATA_TYPES" <<'PY'
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
semantic = pathlib.Path(sys.argv[2])
task_count = pathlib.Path(sys.argv[3])
source_hash = pathlib.Path(sys.argv[4])
metadata_types = pathlib.Path(sys.argv[5])
doc = json.loads(source.read_text())

bad_semantic = dict(doc)
bad_semantic["semanticDecompilation"] = True
semantic.write_text(json.dumps(bad_semantic, indent=2, sort_keys=True) + "\n")

bad_task_count = dict(doc)
bad_task_count["taskCount"] = int(doc.get("taskCount") or 0) + 1
task_count.write_text(json.dumps(bad_task_count, indent=2, sort_keys=True) + "\n")

bad_source_hash = dict(doc)
bad_source_hash["sourceSha256"] = "0" * 64
source_hash.write_text(json.dumps(bad_source_hash, indent=2, sort_keys=True) + "\n")

bad_metadata_types = dict(doc)
bad_metadata_types["taskCount"] = str(doc.get("taskCount"))
bad_metadata_types["sourceSha256"] = 123
metadata_types.write_text(json.dumps(bad_metadata_types, indent=2, sort_keys=True) + "\n")
PY

python3 - "$GOOD_RESPONSE" "$BUILD_RESPONSE" <<'PY'
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
dest = pathlib.Path(sys.argv[2])
doc = json.loads(source.read_text())
files = doc.pop("files")
doc["candidates"] = [
    {
        "path": path,
        "content": content,
        "build": {
            "cc": "gcc",
            "cflags": "-O2",
            "objcopy": "objcopy",
        },
    }
    for path, content in sorted(files.items())
]
dest.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
PY

python3 - "$BUILD_RESPONSE" "$EXTRA_RESPONSE" <<'PY'
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
dest = pathlib.Path(sys.argv[2])
doc = json.loads(source.read_text())
doc["candidates"].append(
    {
        "path": "function-reconstruction-tasks/extra_response_path/candidate.c",
        "content": "int ignored_extra_response_path(void) { return 0; }\n",
        "build": {"cc": "gcc"},
    }
)
doc["taskCount"] = len(doc["candidates"])
dest.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
PY

python3 - "$GOOD_RESPONSE" "$COMMAND_RESPONSE" <<'PY'
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
dest = pathlib.Path(sys.argv[2])
doc = json.loads(source.read_text())
files = doc.pop("files")
doc["candidates"] = [
    {
        "path": path,
        "content": "/* custom build command supplies candidate.bin for this fixture. */\n",
        "build": {
            "command": 'cp target.bin "$CANDIDATE_OUTPUT"',
        },
    }
    for path in sorted(files)
]
dest.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
PY

python3 - "$BUILD_RESPONSE" "$BAD_DUPLICATE_PATH" <<'PY'
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
dest = pathlib.Path(sys.argv[2])
doc = json.loads(source.read_text())
doc["candidates"].append(dict(doc["candidates"][0]))
doc["taskCount"] = len(doc["candidates"])
dest.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
PY

python3 - "$GOOD_RESPONSE" "$BUILD_RESPONSE" "$BAD_MIXED_SHAPE" <<'PY'
import json
import pathlib
import sys

files_source = pathlib.Path(sys.argv[1])
candidates_source = pathlib.Path(sys.argv[2])
dest = pathlib.Path(sys.argv[3])
doc = json.loads(files_source.read_text())
doc["candidates"] = json.loads(candidates_source.read_text())["candidates"]
dest.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
PY

(cd "$PACKAGE" && python3 VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py \
  --response-json "$BUILD_RESPONSE" \
  --out "$BUILD_PREFLIGHT" >/dev/null)
jq -e '.status == "valid" and .buildOverrideCount == .candidateCount and .candidateCount > 0' \
  "$BUILD_PREFLIGHT" >/dev/null
jq -e '([.candidates[].buildOverride] | all) and ([.candidates[].buildOverrideKeys] | all(. == ["cc", "cflags", "objcopy"])) and ((.buildOverridePaths | length) == .candidateCount) and (.buildOverridePaths == .buildOverrideExpectedPaths) and (.buildOverrideExtraPaths == [])' \
  "$BUILD_PREFLIGHT" >/dev/null

(cd "$PACKAGE" && python3 IMPORT_RECONSTRUCTION_RESPONSE_JSON.py \
  --response-json "$BUILD_RESPONSE" \
  --out "$BUILD_IMPORT" >/dev/null)
jq -e '.status == "imported" and ([.imported[].buildOverride] | all)' "$BUILD_IMPORT" >/dev/null
jq -e '([.imported[].buildOverrideKeys] | all(. == ["CC", "CFLAGS", "OBJCOPY"])) and ((.buildOverridePaths | length) == .importedCount) and (.buildOverridePaths == .buildOverrideExpectedPaths) and (.buildOverrideExtraPaths == [])' \
  "$BUILD_IMPORT" >/dev/null
jq -e '.candidateResults.status == "matched"' "$BUILD_IMPORT" >/dev/null
jq -e '([.candidateResults.tasks[].candidateBuildEnv] | all(. != null))' "$BUILD_IMPORT" >/dev/null
jq -e '([.candidateResults.tasks[].candidateBuildEnvSha256] | all(type == "string"))' "$BUILD_IMPORT" >/dev/null
jq -e '.receiptRefresh.status == "refreshed"' "$BUILD_IMPORT" >/dev/null
jq -e '.receiptRefresh.deliverableRefreshed == true' "$BUILD_IMPORT" >/dev/null
jq -e '.receiptRefresh.retiredCompleteReceipts | index("receipts/bundle-verify.json")' "$BUILD_IMPORT" >/dev/null
jq -e '.receiptRefresh.retiredCompleteReceipts | index("receipts/one-shot-source-result.json")' "$BUILD_IMPORT" >/dev/null

if (cd "$PACKAGE" && python3 VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py \
  --response-json "$COMMAND_RESPONSE" >/dev/null 2>&1); then
  echo "expected command build override to require explicit opt-in" >&2
  exit 1
fi
(cd "$PACKAGE" && python3 VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py \
  --response-json "$COMMAND_RESPONSE" \
  --allow-build-command \
  --out "$COMMAND_PREFLIGHT" >/dev/null)
jq -e '.status == "valid" and .buildOverrideCount == .candidateCount and .candidateCount > 0' \
  "$COMMAND_PREFLIGHT" >/dev/null
jq -e '([.candidates[].buildOverrideKeys] | all(. == ["command"])) and ((.buildOverridePaths | length) == .candidateCount) and (.buildOverridePaths == .buildOverrideExpectedPaths) and (.buildOverrideExtraPaths == [])' \
  "$COMMAND_PREFLIGHT" >/dev/null
(cd "$PACKAGE" && python3 IMPORT_RECONSTRUCTION_RESPONSE_JSON.py \
  --response-json "$COMMAND_RESPONSE" \
  --allow-build-command \
  --out "$COMMAND_IMPORT" >/dev/null)
jq -e '.status == "imported" and .candidateResults.status == "matched"' "$COMMAND_IMPORT" >/dev/null
jq -e '([.imported[].buildOverrideKeys] | all(. == ["CANDIDATE_BUILD_COMMAND"])) and ((.buildOverridePaths | length) == .importedCount) and (.buildOverridePaths == .buildOverrideExpectedPaths) and (.buildOverrideExtraPaths == [])' \
  "$COMMAND_IMPORT" >/dev/null
jq -e '([.candidateResults.tasks[].candidateBuildEnv] | all(. != null))' "$COMMAND_IMPORT" >/dev/null
jq -e '([.candidateResults.tasks[].candidateBuildEnvSha256] | all(type == "string"))' "$COMMAND_IMPORT" >/dev/null

cp -a "$PACKAGE" "$BAD_BUILD_ENV_PACKAGE"
python3 - "$BAD_BUILD_ENV_PACKAGE/FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json" "$BAD_BUILD_ENV_ARCHIVE" "$BAD_BUILD_ENV_PACKAGE" <<'PY'
import json
import pathlib
import sys
import tarfile

results_path = pathlib.Path(sys.argv[1])
archive_path = pathlib.Path(sys.argv[2])
package = pathlib.Path(sys.argv[3])

doc = json.loads(results_path.read_text())
for row in doc["tasks"]:
    if row.get("candidateBuildEnv"):
        row["candidateBuildEnvSha256"] = "0" * 64
        break
else:
    raise SystemExit("expected at least one candidateBuildEnv row")
results_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")

with tarfile.open(archive_path, "w:gz") as archive:
    archive.add(package, arcname=package.name)
PY
if "$ROOT/scripts/decomp-cli.sh" one-shot-source-archive-verify \
  --archive "$BAD_BUILD_ENV_ARCHIVE" \
  --out "$BAD_BUILD_ENV_ARCHIVE_VERIFY" >/dev/null 2>&1; then
  echo "expected archive verifier to reject candidate build env evidence mismatch" >&2
  exit 1
fi
jq -e '.authorityContractErrors | map(select(startswith("candidate build env listed in results is missing: ") or startswith("candidate build env hash mismatch: "))) | length > 0' \
  "$BAD_BUILD_ENV_ARCHIVE_VERIFY" >/dev/null

(cd "$PACKAGE" && python3 VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py \
  --response-json "$EXTRA_RESPONSE" \
  --allow-extra \
  --out "$EXTRA_PREFLIGHT" >/dev/null)
(cd "$PACKAGE" && python3 IMPORT_RECONSTRUCTION_RESPONSE_JSON.py \
  --response-json "$EXTRA_RESPONSE" \
  --allow-extra \
  --out "$EXTRA_IMPORT" >/dev/null)
jq -e '.status == "valid-with-extra" and .candidateCount > 0 and .extraCount == 1' "$EXTRA_PREFLIGHT" >/dev/null
jq -e '.status == "imported-with-extra" and .importedCount > 0 and .extraCount == 1' "$EXTRA_IMPORT" >/dev/null
jq -e '.buildOverrideExtraPaths == ["function-reconstruction-tasks/extra_response_path/candidate.c"] and ((.buildOverridePaths | length) == .candidateCount) and ((.buildOverrideExpectedPaths | length) == (.candidateCount - 1))' \
  "$EXTRA_PREFLIGHT" >/dev/null
jq -e '.buildOverrideExtraPaths == ["function-reconstruction-tasks/extra_response_path/candidate.c"] and ((.buildOverridePaths | length) == .buildOverrideCount) and ((.buildOverrideExpectedPaths | length) == .importedCount)' \
  "$EXTRA_IMPORT" >/dev/null

python3 - "$GOOD_RESPONSE" "$DIR_RESPONSE" <<'PY'
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
dest = pathlib.Path(sys.argv[2])
doc = json.loads(source.read_text())
for rel, content in sorted(doc["files"].items()):
    path = dest / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
PY

(cd "$PACKAGE" && python3 IMPORT_RECONSTRUCTION_CANDIDATES.py \
  --source-dir "$DIR_RESPONSE" \
  --out "$DIR_IMPORT" >/dev/null)
jq -e '.status == "imported" and .importedCount > 0 and .extraCount == 0' "$DIR_IMPORT" >/dev/null

mkdir -p "$DIR_RESPONSE/notes"
printf 'ignored\n' > "$DIR_RESPONSE/notes/extra.txt"
if (cd "$PACKAGE" && python3 IMPORT_RECONSTRUCTION_CANDIDATES.py \
  --source-dir "$DIR_RESPONSE" >/dev/null 2>&1); then
  echo "expected directory importer to reject unexpected response files" >&2
  exit 1
fi
(cd "$PACKAGE" && python3 IMPORT_RECONSTRUCTION_CANDIDATES.py \
  --source-dir "$DIR_RESPONSE" \
  --allow-extra \
  --out "$DIR_EXTRA_IMPORT" >/dev/null)
jq -e '.status == "imported-with-extra" and .importedCount > 0 and .extraCount == 1 and (.extras | index("notes/extra.txt"))' \
  "$DIR_EXTRA_IMPORT" >/dev/null

cp -a "$PACKAGE" "$PARTIAL_PACKAGE"
python3 - "$PARTIAL_PACKAGE/RECONSTRUCTION_RESPONSE_TEMPLATE.json" "$PARTIAL_PACKAGE/FUNCTION_RECONSTRUCTION_TASKS.json" <<'PY'
import json
import pathlib
import sys

template_path = pathlib.Path(sys.argv[1])
tasks_path = pathlib.Path(sys.argv[2])
template = json.loads(template_path.read_text())
template["expectedCandidates"].append(
    {
        "path": "function-reconstruction-tasks/extra_expected_for_partial/candidate.c",
        "task": "extra_expected_for_partial",
        "semanticDecompilation": False,
    }
)
template["taskCount"] = len(template["expectedCandidates"])
template_path.write_text(json.dumps(template, indent=2, sort_keys=True) + "\n")

tasks = json.loads(tasks_path.read_text())
tasks["tasks"].append(
    {
        "name": "extra_expected_for_partial",
        "path": "function-reconstruction-tasks/extra_expected_for_partial",
        "candidateVerifier": "function-reconstruction-tasks/extra_expected_for_partial/VERIFY_CANDIDATE.sh",
        "targetBytesSha256": "0" * 64,
        "semanticDecompilation": False,
        "verifiedAgainstSource": False,
    }
)
tasks["taskCount"] = len(tasks["tasks"])
tasks_path.write_text(json.dumps(tasks, indent=2, sort_keys=True) + "\n")
PY
(cd "$PARTIAL_PACKAGE" && python3 VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py \
  --response-json "$BUILD_RESPONSE" \
  --allow-partial \
  --out "$PARTIAL_PREFLIGHT" >/dev/null)
(cd "$PARTIAL_PACKAGE" && python3 IMPORT_RECONSTRUCTION_RESPONSE_JSON.py \
  --response-json "$BUILD_RESPONSE" \
  --allow-partial \
  --out "$PARTIAL_IMPORT" >/dev/null)
jq -e '.status == "partial" and .candidateCount > 0 and .missingCount == 1' "$PARTIAL_PREFLIGHT" >/dev/null
jq -e '.status == "partial" and .importedCount > 0 and .missingCount == 1' "$PARTIAL_IMPORT" >/dev/null
jq -e '.candidateResults.status == "partial" and .candidateResults.matchedCount > 0 and .candidateResults.skippedCount == 1' \
  "$PARTIAL_IMPORT" >/dev/null

cp -a "$PARTIAL_PACKAGE" "$BAD_REPLAY_STATUS_PACKAGE"
python3 - "$BAD_REPLAY_STATUS_PACKAGE/FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
doc = json.loads(path.read_text())
doc["status"] = "matched"
path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
PY
if "$ROOT/scripts/decomp-cli.sh" one-shot-source-validate --package "$BAD_REPLAY_STATUS_PACKAGE" >/dev/null 2>&1; then
  echo "expected validator to reject inconsistent reconstruction replay status" >&2
  exit 1
fi

cp -a "$PARTIAL_PACKAGE" "$BAD_REPLAY_ROW_PACKAGE"
python3 - "$BAD_REPLAY_ROW_PACKAGE/FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
doc = json.loads(path.read_text())
for row in doc["tasks"]:
    if row.get("status") == "skipped":
        row["status"] = "matched"
        row["byteIdentical"] = True
        break
path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
PY
if "$ROOT/scripts/decomp-cli.sh" one-shot-source-validate \
  --package "$BAD_REPLAY_ROW_PACKAGE" \
  --out "$BAD_REPLAY_ROW_VALIDATE" >/dev/null 2>&1; then
  echo "expected validator to reject reconstruction replay row/count mismatch" >&2
  exit 1
fi
jq -e '.errors | index("FUNCTION_RECONSTRUCTION_CANDIDATE_RESULTS.json count row mismatch")' \
  "$BAD_REPLAY_ROW_VALIDATE" >/dev/null

cp -a "$PARTIAL_PACKAGE" "$BAD_SEMANTIC_BLOCKERS_PACKAGE"
python3 - "$BAD_SEMANTIC_BLOCKERS_PACKAGE/SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
doc = json.loads(path.read_text())
doc["blockers"] = [
    blocker for blocker in doc.get("blockers", [])
    if blocker.get("id") != "missing-reconstruction-candidates"
]
path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
PY
if "$ROOT/scripts/decomp-cli.sh" one-shot-source-validate \
  --package "$BAD_SEMANTIC_BLOCKERS_PACKAGE" \
  --out "$BAD_SEMANTIC_BLOCKERS_VALIDATE" >/dev/null 2>&1; then
  echo "expected validator to reject missing semantic authority blocker" >&2
  exit 1
fi
jq -e '.errors | index("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json missing blockers: missing-reconstruction-candidates")' \
  "$BAD_SEMANTIC_BLOCKERS_VALIDATE" >/dev/null

cp -a "$PACKAGE" "$BAD_SEMANTIC_COUNTS_PACKAGE"
python3 - "$BAD_SEMANTIC_COUNTS_PACKAGE/SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json" "$BAD_SEMANTIC_COUNTS_ARCHIVE" "$BAD_SEMANTIC_COUNTS_PACKAGE" <<'PY'
import json
import pathlib
import sys
import tarfile

semantic_path = pathlib.Path(sys.argv[1])
archive_path = pathlib.Path(sys.argv[2])
package = pathlib.Path(sys.argv[3])

doc = json.loads(semantic_path.read_text())
doc["matchedCount"] = int(doc.get("matchedCount") or 0) + 1
semantic_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")

with tarfile.open(archive_path, "w:gz") as archive:
    archive.add(package, arcname=package.name)
PY
if "$ROOT/scripts/decomp-cli.sh" one-shot-source-archive-verify \
  --archive "$BAD_SEMANTIC_COUNTS_ARCHIVE" \
  --out "$BAD_SEMANTIC_COUNTS_ARCHIVE_VERIFY" >/dev/null 2>&1; then
  echo "expected archive verifier to reject inconsistent semantic authority counts" >&2
  exit 1
fi
jq -e '.authorityContractErrors | index("SEMANTIC_SOURCE_AUTHORITY_EVALUATION.json candidate counts mismatch")' \
  "$BAD_SEMANTIC_COUNTS_ARCHIVE_VERIFY" >/dev/null

cp -a "$PACKAGE" "$BAD_TEMPLATE_PACKAGE"
python3 - "$BAD_TEMPLATE_PACKAGE/RECONSTRUCTION_RESPONSE_TEMPLATE.json" "$BAD_TEMPLATE_ARCHIVE" "$BAD_TEMPLATE_PACKAGE" <<'PY'
import json
import pathlib
import sys
import tarfile

template_path = pathlib.Path(sys.argv[1])
archive_path = pathlib.Path(sys.argv[2])
package = pathlib.Path(sys.argv[3])

doc = json.loads(template_path.read_text())
doc["jsonStructuredResponseShape"]["candidates"][0]["build"]["command"] = "unsafe unpinned command example"
doc["jsonReplayReportShapes"]["import"]["buildOverrideExtraPaths"] = ["missing audit detail"]
template_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")

with tarfile.open(archive_path, "w:gz") as archive:
    archive.add(package, arcname=package.name)
PY
if "$ROOT/scripts/decomp-cli.sh" one-shot-source-validate \
  --package "$BAD_TEMPLATE_PACKAGE" \
  --out "$BAD_TEMPLATE_VALIDATE" >/dev/null 2>&1; then
  echo "expected validator to reject corrupted structured response template" >&2
  exit 1
fi
jq -e '.errors | index("RECONSTRUCTION_RESPONSE_TEMPLATE.json structured JSON response build command mismatch")' \
  "$BAD_TEMPLATE_VALIDATE" >/dev/null
jq -e '.errors | index("RECONSTRUCTION_RESPONSE_TEMPLATE.json JSON replay report shapes mismatch")' \
  "$BAD_TEMPLATE_VALIDATE" >/dev/null
if "$ROOT/scripts/decomp-cli.sh" one-shot-source-archive-verify \
  --archive "$BAD_TEMPLATE_ARCHIVE" \
  --out "$BAD_TEMPLATE_ARCHIVE_VERIFY" >/dev/null 2>&1; then
  echo "expected archive verifier to reject corrupted structured response template" >&2
  exit 1
fi
jq -e '.authorityContractErrors | index("RECONSTRUCTION_RESPONSE_TEMPLATE.json structured JSON response build command mismatch")' \
  "$BAD_TEMPLATE_ARCHIVE_VERIFY" >/dev/null
jq -e '.authorityContractErrors | index("RECONSTRUCTION_RESPONSE_TEMPLATE.json JSON replay report shapes mismatch")' \
  "$BAD_TEMPLATE_ARCHIVE_VERIFY" >/dev/null

"$ROOT/scripts/decomp-cli.sh" one-shot-source-validate \
  --package "$PACKAGE" >/dev/null

for bad_response in "$BAD_SEMANTIC" "$BAD_TASK_COUNT" "$BAD_SOURCE_HASH" "$BAD_DUPLICATE_PATH" "$BAD_MIXED_SHAPE" "$BAD_METADATA_TYPES"; do
  if (cd "$PACKAGE" && python3 VALIDATE_RECONSTRUCTION_RESPONSE_JSON.py --response-json "$bad_response" >/dev/null 2>&1); then
    echo "expected validator to reject $bad_response" >&2
    exit 1
  fi
  if (cd "$PACKAGE" && python3 IMPORT_RECONSTRUCTION_RESPONSE_JSON.py --response-json "$bad_response" >/dev/null 2>&1); then
    echo "expected importer to reject $bad_response" >&2
    exit 1
  fi
done

echo "ok"
