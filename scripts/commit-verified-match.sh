#!/usr/bin/env bash
# Commit a verified match without staging unrelated workspace changes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/prompt-settings.sh
. "$ROOT/scripts/lib/prompt-settings.sh"
# shellcheck source=scripts/lib/case-metadata.sh
. "$ROOT/scripts/lib/case-metadata.sh"

usage() {
  cat >&2 <<'EOF'
usage:
  commit-verified-match.sh --prompt <prompts/<name>> [--candidate <candidate.c>]
                           [--message <msg>] [--path <extra-path>] [--dry-run]
                           [--allow-existing-index]

Re-runs build-and-verify, accepts only objdiff matched or cmp byte-identical,
stages only the candidate/proof receipt paths plus explicit --path entries, and
commits them as one verified unit.
EOF
}

prompt_dir=""
candidate=""
message=""
dry_run=false
allow_existing_index=false
extra_paths=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) prompt_dir="$2"; shift 2 ;;
    --candidate) candidate="$2"; shift 2 ;;
    --message) message="$2"; shift 2 ;;
    --path) extra_paths+=("$2"); shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    --allow-existing-index) allow_existing_index=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "commit-verified-match: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$prompt_dir" ]]; then
  echo "commit-verified-match: --prompt is required" >&2
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
  echo "commit-verified-match: prompt is blocked: $blocked_reason" >&2
  exit 3
fi

if [[ -z "$candidate" ]]; then
  candidate="$(case_metadata_get_default "$prompt_dir" candidateSourcePath "prompt:/candidate.c")"
fi
candidate="$(case_metadata_expand "$candidate" "$function_name" "$prompt_name")"
candidate="$(case_metadata_resolve_path "$ROOT" "$prompt_dir" "$candidate")"

if [[ ! -f "$candidate" ]]; then
  echo "commit-verified-match: candidate source not found: $candidate" >&2
  exit 1
fi

build_dir="$prompt_dir/build"
mkdir -p "$build_dir"
set +e
"$ROOT/scripts/build-and-verify.sh" --prompt "$prompt_dir" --candidate "$candidate" >"$build_dir/commit-verify.stdout" 2>"$build_dir/commit-verify.stderr"
verify_rc=$?
set -e

if [[ "$verify_rc" -eq 3 ]]; then
  cat "$build_dir/commit-verify.stderr" >&2 || true
  exit 3
fi
if [[ "$verify_rc" -ne 0 ]]; then
  cat "$build_dir/commit-verify.stderr" >&2 || true
  echo "commit-verified-match: verification did not match; refusing commit" >&2
  exit 1
fi

verify_report="$build_dir/build-and-verify.json"
if [[ ! -f "$verify_report" ]]; then
  echo "commit-verified-match: verifier report missing: $verify_report" >&2
  exit 1
fi

if ! jq -e '
  .schema == "mizuchi.build-and-verify.v1"
  and .status == "matched"
  and (
    (.method == "objdiff")
    or ((.method == "cmp" or .method == "custom") and .byte_identical == true and .target_sha256 == .candidate_sha256 and .target_size == .candidate_size)
  )
  and (.target_size > 0)
' "$verify_report" >/dev/null; then
  echo "commit-verified-match: verifier report is not an accepted verified match" >&2
  exit 1
fi

receipt="$build_dir/commit-receipt.json"
committed_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

paths=(
  "$candidate"
  "$verify_report"
  "$build_dir/build-and-verify.compile.summary.txt"
  "$build_dir/build-and-verify.verify.log"
)
for path in "${extra_paths[@]}"; do
  path="$(case_metadata_expand "$path" "$function_name" "$prompt_name")"
  path="$(case_metadata_resolve_path "$ROOT" "$prompt_dir" "$path")"
  paths+=("$path")
done

for path in "${paths[@]}"; do
  if [[ ! -e "$path" ]]; then
    echo "commit-verified-match: staged path does not exist: $path" >&2
    exit 1
  fi
done

if [[ -z "$message" ]]; then
  message="Match ${function_name} (verified objdiff)"
fi

jq -n \
  --arg schema "mizuchi.commit-receipt.v1" \
  --arg status "verified" \
  --arg prompt "$prompt_name" \
  --arg function_name "$function_name" \
  --arg candidate "$candidate" \
  --arg verifier_report "$verify_report" \
  --arg committed_at "$committed_at" \
  --arg message "$message" \
  --argjson dry_run "$($dry_run && printf 'true' || printf 'false')" \
  --argjson paths "$(printf '%s\n' "${paths[@]}" "$receipt" | jq -R . | jq -s '.')" \
  '{
    schema: $schema,
    status: $status,
    prompt: $prompt,
    functionName: $function_name,
    candidateSource: $candidate,
    verifierReport: $verifier_report,
    message: $message,
    dryRun: $dry_run,
    paths: $paths,
    verifiedAt: $committed_at
  }' >"$receipt"

paths+=("$receipt")

if [[ "$dry_run" == true ]]; then
  jq '.' "$receipt"
  exit 0
fi

if ! git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "commit-verified-match: not inside a git worktree" >&2
  exit 1
fi

if [[ "$allow_existing_index" != true ]] && ! git -C "$ROOT" diff --cached --quiet --; then
  echo "commit-verified-match: refusing to commit with pre-existing staged changes" >&2
  git -C "$ROOT" diff --cached --name-only >&2 || true
  exit 1
fi

git -C "$ROOT" add -- "${paths[@]}"
if git -C "$ROOT" diff --cached --quiet --; then
  echo "commit-verified-match: no staged changes after verified add" >&2
  exit 1
fi
git -C "$ROOT" commit -m "$message"
jq '.' "$receipt"
