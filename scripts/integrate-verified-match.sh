#!/usr/bin/env bash
# Land a verified candidate source file after re-running the match gate.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/prompt-settings.sh
. "$ROOT/scripts/lib/prompt-settings.sh"
# shellcheck source=scripts/lib/case-metadata.sh
. "$ROOT/scripts/lib/case-metadata.sh"

usage() {
  cat <<'EOF'
usage: integrate-verified-match.sh --prompt <prompts/<name>> --source-out <path> [--candidate <candidate.c>]

Re-runs build-and-verify for the candidate, copies it to the integration
destination only when the verifier reports a match, updates case.yaml status to
integrated, and writes <prompt>/build/integration-receipt.json.
EOF
}

prompt_dir=""
candidate=""
source_out=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) prompt_dir="$2"; shift 2 ;;
    --candidate) candidate="$2"; shift 2 ;;
    --source-out) source_out="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "integrate-verified-match: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$prompt_dir" || -z "$source_out" ]]; then
  echo "integrate-verified-match: --prompt and --source-out are required" >&2
  usage
  exit 2
fi

prompt_settings_require_dir "$prompt_dir" || exit $?
prompt_dir="$(cd "$prompt_dir" && pwd)"
prompt_name="$(basename "$prompt_dir")"
function_name="$(prompt_settings_get "$prompt_dir" functionName)"

case_status="$(case_metadata_get_default "$prompt_dir" status "")"
if [[ "$case_status" == "blocked" ]]; then
  blocked_reason="$(case_metadata_get_default "$prompt_dir" blockedReason "case.yaml status is blocked")"
  echo "integrate-verified-match: prompt is blocked: $blocked_reason" >&2
  exit 3
fi

if [[ -z "$candidate" ]]; then
  candidate="$(case_metadata_get_default "$prompt_dir" candidateSourcePath "prompt:/candidate.c")"
fi
candidate="$(case_metadata_expand "$candidate" "$function_name" "$prompt_name")"
candidate="$(case_metadata_resolve_path "$ROOT" "$prompt_dir" "$candidate")"
source_out="$(case_metadata_expand "$source_out" "$function_name" "$prompt_name")"
source_out="$(case_metadata_resolve_path "$ROOT" "$prompt_dir" "$source_out")"

if [[ ! -f "$candidate" ]]; then
  echo "integrate-verified-match: candidate source not found: $candidate" >&2
  exit 1
fi

mkdir -p "$prompt_dir/build"
set +e
"$ROOT/scripts/build-and-verify.sh" --prompt "$prompt_dir" --candidate "$candidate" >"$prompt_dir/build/integration-verify.stdout" 2>"$prompt_dir/build/integration-verify.stderr"
verify_rc=$?
set -e

verify_report="$prompt_dir/build/build-and-verify.json"
if [[ "$verify_rc" -eq 3 ]]; then
  cat "$prompt_dir/build/integration-verify.stderr" >&2 || true
  exit 3
fi
if [[ "$verify_rc" -ne 0 ]]; then
  cat "$prompt_dir/build/integration-verify.stderr" >&2 || true
  echo "integrate-verified-match: verification did not match; refusing integration" >&2
  exit 1
fi
if [[ ! -f "$verify_report" ]]; then
  echo "integrate-verified-match: verifier report missing: $verify_report" >&2
  exit 1
fi
if ! jq -e '
  .schema == "reconkit.build-and-verify.v1"
  and .status == "matched"
  and .byte_identical == true
  and .target_sha256 == .candidate_sha256
  and .target_size == .candidate_size
  and (.target_size > 0)
' "$verify_report" >/dev/null; then
  echo "integrate-verified-match: verifier report is not a byte-identical match" >&2
  exit 1
fi

mkdir -p "$(dirname "$source_out")"
cp "$candidate" "$source_out"

receipt="$prompt_dir/build/integration-receipt.json"
integrated_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
candidate_sha="$(sha256sum "$candidate" | awk '{print $1}')"
source_sha="$(sha256sum "$source_out" | awk '{print $1}')"

jq -n \
  --arg schema "reconkit.integration-receipt.v1" \
  --arg status "integrated" \
  --arg prompt "$prompt_name" \
  --arg function_name "$function_name" \
  --arg candidate "$candidate" \
  --arg source_out "$source_out" \
  --arg candidate_sha "$candidate_sha" \
  --arg source_sha "$source_sha" \
  --arg verifier_report "$verify_report" \
  --arg integrated_at "$integrated_at" \
  '{
    schema: $schema,
    status: $status,
    prompt: $prompt,
    functionName: $function_name,
    candidateSource: $candidate,
    sourceOut: $source_out,
    candidateSourceSha256: $candidate_sha,
    sourceOutSha256: $source_sha,
    verifierReport: $verifier_report,
    integratedAt: $integrated_at
  }' | tee "$receipt"

case_file="$prompt_dir/case.yaml"
if [[ -f "$case_file" ]]; then
  ruby -ryaml - "$case_file" "$source_out" "$receipt" "$integrated_at" <<'RUBY'
path, source_out, receipt, integrated_at = ARGV
data = YAML.load_file(path)
abort "case.yaml must be a mapping" unless data.is_a?(Hash)
data["status"] = "integrated"
data["integratedSourcePath"] = source_out
data["integrationReceiptPath"] = receipt
data["integratedAt"] = integrated_at
File.write(path, YAML.dump(data))
RUBY
fi
