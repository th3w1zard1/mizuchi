#!/usr/bin/env bash
# Compile a candidate for a prompt and verify byte identity against its proof target.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/prompt-settings.sh
. "$ROOT/scripts/lib/prompt-settings.sh"
# shellcheck source=scripts/lib/mizuchi-config.sh
. "$ROOT/scripts/lib/mizuchi-config.sh"
# shellcheck source=scripts/lib/case-metadata.sh
. "$ROOT/scripts/lib/case-metadata.sh"

usage() {
  cat <<'EOF'
usage: build-and-verify.sh --prompt <prompts/<name>/> [--candidate <candidate.c>] [--refresh-target]

Compiles candidate C to <prompt>/build/candidate.o and verifies byte identity
against settings.yaml targetObjectPath. If case.yaml provides verifierCommand,
that command must write {{candidateOutputPath}} and the output bytes are compared
against the target. If case.yaml provides targetSourcePath and compilerCommand,
--refresh-target rebuilds the golden object first.
EOF
}

prompt_dir=""
candidate=""
refresh_target=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) prompt_dir="$2"; shift 2 ;;
    --candidate) candidate="$2"; shift 2 ;;
    --refresh-target) refresh_target=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$prompt_dir" ]]; then
  echo "missing --prompt" >&2
  usage
  exit 2
fi

prompt_settings_require_dir "$prompt_dir" || exit $?
prompt_dir="$(cd "$prompt_dir" && pwd)"
prompt_name="$(basename "$prompt_dir")"
case_status="$(case_metadata_get_default "$prompt_dir" status "")"
if [[ "$case_status" == "blocked" ]]; then
  blocked_reason="$(case_metadata_get_default "$prompt_dir" blockedReason "case.yaml status is blocked")"
  echo "build-and-verify: prompt is blocked: $blocked_reason" >&2
  exit 3
fi
function_name="$(prompt_settings_get "$prompt_dir" functionName)"
target_object="$(prompt_settings_get "$prompt_dir" targetObjectPath)"
target_object="$(case_metadata_expand "$target_object" "$function_name" "$prompt_name")"
target_object="$(case_metadata_resolve_path "$ROOT" "$prompt_dir" "$target_object")"

if [[ -z "$candidate" ]]; then
  candidate="$(case_metadata_get_default "$prompt_dir" candidateSourcePath "prompt:/candidate.c")"
fi
candidate="$(case_metadata_expand "$candidate" "$function_name" "$prompt_name")"
candidate="$(case_metadata_resolve_path "$ROOT" "$prompt_dir" "$candidate")"

if [[ ! -f "$candidate" ]]; then
  echo "build-and-verify: candidate C not found: $candidate" >&2
  exit 1
fi

build_dir="$prompt_dir/build"
mkdir -p "$build_dir"
candidate_object="$build_dir/candidate.o"
custom_candidate_output="$build_dir/candidate.bin"
compile_log="$build_dir/build-and-verify.compile.log"
compile_summary="$build_dir/build-and-verify.compile.summary.txt"
verify_log="$build_dir/build-and-verify.verify.log"
report_json="$build_dir/build-and-verify.json"

sha256_file() {
  local file="$1"
  if [[ -f "$file" ]]; then
    sha256sum "$file" | awk '{print $1}'
  fi
}

size_file() {
  local file="$1"
  if [[ -f "$file" ]]; then
    stat -c %s "$file"
  else
    printf '0'
  fi
}

compiler_command="$(case_metadata_get_default "$prompt_dir" compilerCommand "")"
verifier_command="$(case_metadata_get_default "$prompt_dir" verifierCommand "")"
if [[ -z "$compiler_command" ]]; then
  if mizuchi_config_resolve >/dev/null 2>&1; then
    compiler_command="$(mizuchi_config_get "global.compilerScript" optional || true)"
  fi
fi
if [[ -z "$compiler_command" ]]; then
  compiler_command='bash ./scripts/compile-placeholder.sh "{{cFilePath}}" "{{objFilePath}}"'
fi

compile_command_for() {
  local c_file="$1" obj_file="$2" source_role="$3"
  local expanded="$compiler_command"
  expanded="${expanded//\{\{cFilePath\}\}/$c_file}"
  expanded="${expanded//\{\{objFilePath\}\}/$obj_file}"
  expanded="${expanded//\{\{functionName\}\}/$function_name}"
  expanded="${expanded//\{\{promptName\}\}/$prompt_name}"
  expanded="${expanded//\{\{sourceRole\}\}/$source_role}"
  printf '%s\n' "$expanded"
}

verifier_command_for() {
  local expanded="$verifier_command"
  expanded="${expanded//\{\{candidateSourcePath\}\}/$candidate}"
  expanded="${expanded//\{\{candidateOutputPath\}\}/$custom_candidate_output}"
  expanded="${expanded//\{\{targetObjectPath\}\}/$target_object}"
  expanded="${expanded//\{\{functionName\}\}/$function_name}"
  expanded="${expanded//\{\{promptName\}\}/$prompt_name}"
  expanded="${expanded//\{\{promptDir\}\}/$prompt_dir}"
  printf '%s\n' "$expanded"
}

if [[ "$refresh_target" -eq 1 || ! -f "$target_object" ]]; then
  target_source="$(case_metadata_get_default "$prompt_dir" targetSourcePath "")"
  if [[ -z "$target_source" ]]; then
    echo "build-and-verify: target missing and case.yaml has no targetSourcePath: $target_object" >&2
    exit 1
  fi
  target_source="$(case_metadata_expand "$target_source" "$function_name" "$prompt_name")"
  target_source="$(case_metadata_resolve_path "$ROOT" "$prompt_dir" "$target_source")"
  if [[ ! -f "$target_source" ]]; then
    echo "build-and-verify: target source not found: $target_source" >&2
    exit 1
  fi
  mkdir -p "$(dirname "$target_object")"
  echo "build-and-verify: compiling target $target_source -> $target_object" >&2
  compile_cmd="$(compile_command_for "$target_source" "$target_object" "target")"
  "$ROOT/scripts/lib/build-defensive.sh" \
    --log "$compile_log" \
    --summary "$compile_summary" \
    --cwd "$ROOT" \
    -- bash -c "$compile_cmd" || {
    echo "build-and-verify: target compile failed (see $compile_summary; full log $compile_log)" >&2
    cat "$compile_summary" >&2 || true
    exit 1
  }
fi

if [[ -n "$verifier_command" ]]; then
  candidate_object="$custom_candidate_output"
  rm -f "$candidate_object"
  echo "build-and-verify: running verifierCommand for $candidate -> $candidate_object" >&2
  verify_cmd="$(verifier_command_for)"
  "$ROOT/scripts/lib/build-defensive.sh" \
    --log "$compile_log" \
    --summary "$compile_summary" \
    --cwd "$ROOT" \
    -- bash -c "$verify_cmd" || {
    echo "build-and-verify: custom verifier failed (see $compile_summary; full log $compile_log)" >&2
    cat "$compile_summary" >&2 || true
    exit 1
  }
  method="custom"
  if [[ ! -f "$candidate_object" ]]; then
    echo "build-and-verify: custom verifier did not write candidate output: $candidate_object" >&2
    exit 1
  fi
  if cmp -s "$target_object" "$candidate_object"; then
    status="matched"
    printf 'custom verifier: byte-identical\n' >"$verify_log"
  else
    status="mismatched"
    cmp -l "$target_object" "$candidate_object" | head -50 >"$verify_log" || true
  fi
elif command -v objdiff >/dev/null 2>&1; then
  echo "build-and-verify: compiling candidate $candidate -> $candidate_object" >&2
  compile_cmd="$(compile_command_for "$candidate" "$candidate_object" "candidate")"
  "$ROOT/scripts/lib/build-defensive.sh" \
    --log "$compile_log" \
    --summary "$compile_summary" \
    --cwd "$ROOT" \
    -- bash -c "$compile_cmd" || {
    echo "build-and-verify: candidate compile failed (see $compile_summary; full log $compile_log)" >&2
    cat "$compile_summary" >&2 || true
    exit 1
  }
  set +e
  verify_report="$("$ROOT/scripts/lib/verify-objdiff.sh" "$target_object" "$candidate_object" --out "$verify_log" 2>&1)"
  verify_rc=$?
  set -e
  if [[ "$verify_rc" -eq 0 && "$(jq -r '.status' <<<"$verify_report" 2>/dev/null || true)" == "matched" ]]; then
    status="matched"
    method="objdiff"
  else
    status="mismatched"
    method="objdiff"
    printf '%s\n' "$verify_report" >&2
  fi
else
  echo "build-and-verify: compiling candidate $candidate -> $candidate_object" >&2
  compile_cmd="$(compile_command_for "$candidate" "$candidate_object" "candidate")"
  "$ROOT/scripts/lib/build-defensive.sh" \
    --log "$compile_log" \
    --summary "$compile_summary" \
    --cwd "$ROOT" \
    -- bash -c "$compile_cmd" || {
    echo "build-and-verify: candidate compile failed (see $compile_summary; full log $compile_log)" >&2
    cat "$compile_summary" >&2 || true
    exit 1
  }
  method="cmp"
  if cmp -s "$target_object" "$candidate_object"; then
    status="matched"
    printf 'cmp: byte-identical\n' >"$verify_log"
  else
    status="mismatched"
    cmp -l "$target_object" "$candidate_object" | head -50 >"$verify_log" || true
  fi
fi

jq -n \
  --arg schema "mizuchi.build-and-verify.v1" \
  --arg status "$status" \
  --arg method "$method" \
  --arg prompt "$prompt_name" \
  --arg function_name "$function_name" \
  --arg candidate_source "$candidate" \
  --arg target_object "$target_object" \
  --arg candidate_object "$candidate_object" \
  --arg target_sha256 "$(sha256_file "$target_object")" \
  --arg candidate_sha256 "$(sha256_file "$candidate_object")" \
  --argjson target_size "$(size_file "$target_object")" \
  --argjson candidate_size "$(size_file "$candidate_object")" \
  --arg compile_log "$compile_log" \
  --arg compile_summary "$compile_summary" \
  --arg verify_log "$verify_log" \
  '{
    schema: $schema,
    status: $status,
    method: $method,
    prompt: $prompt,
    function_name: $function_name,
    candidate_source: $candidate_source,
    target_object: $target_object,
    candidate_object: $candidate_object,
    target_sha256: $target_sha256,
    candidate_sha256: $candidate_sha256,
    target_size: $target_size,
    candidate_size: $candidate_size,
    compile_log: $compile_log,
    compile_summary: $compile_summary,
    verify_log: $verify_log,
    byte_identical: ($status == "matched" and $target_sha256 == $candidate_sha256 and $target_size == $candidate_size)
  }' | tee "$report_json"

[[ "$status" == "matched" ]]
