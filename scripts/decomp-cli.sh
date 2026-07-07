#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./scripts/decomp-cli.sh <command> [args]

Commands:
  decomp-prompt <prompt-name>
  decomp-validate <prompt-name|--all>
  decomp-readiness <prompt-name|--all>
  decomp-atlas <prompt-name>
  matcher <prompt-name> [matcher args]
  export-context <input> --out-dir <out-dir> [--format json|md] [--binary-analysis light|standard|deep] [--max-files N] [--max-depth N] [--max-hash-bytes N] [--max-text-bytes N] [--max-binary-analysis-bytes N] [--max-container-members N] [--strings-limit N] [--max-index-text-chars N]
  export-context-batch <input> --out-dir <out-dir> [--item-mode matching-files|top-level] [--suffix suffixes...] [--max-items N] [--min-size N] [--max-files-per-item N] [--max-depth N] [--max-hash-bytes N] [--max-text-bytes N] [--max-binary-analysis-bytes N] [--max-container-members N] [--strings-limit N] [--max-index-text-chars N]
  scorer [--prompts-dir prompts] [--out state/scores.json] [--queue state/queue.json] [--update-queue]
  vacuum <init|start|resume|status|inspect-queue|reset-queue> [vacuum args]
  init-vacuum-state [--prompts-dir prompts] [--queue state/queue.json]
  import-one-shot-tasks --package <one-shot-source-dir> [--prompts-dir prompts]
  one-shot-task-coverage --package <one-shot-source-dir> [--prompts-dir prompts] [--queue state/queue.json]
  queue <init|next|move|attempt|summary> [queue args]
  decomp-function <prompt-name>
  decomp-integrate <prompt-name> <source-out>
  commit-verified-match --prompt <prompts/name> [--candidate candidate.c] [--dry-run]
  one-shot-source --binary <file> --out <dir> [--candidate-source <file>|--candidate-source-dir <dir>] [--candidate-build-command <cmd>] [--artifact-mode full|lean]
  one-shot-source-verify --package <dir>
  one-shot-source-archive-verify --archive <package.tar.gz>
  one-shot-source-claims (--package <dir>|--archive <package.tar.gz>)
  one-shot-source-validate (--package <dir>|--archive <package.tar.gz>)
  one-shot-source-proof (--package <dir>|--archive <package.tar.gz>)
  one-shot-source-deliverable-verify (--deliverable <deliverable.json>|--bundle <deliverable.tar.gz>)
  one-shot-source-clean --package <dir> [--dry-run]
  binary-source-roundtrip --binary <file> --out <dir> [--artifact-mode full|lean]
  elf-auto-trivial --binary <game-binary> --out <dir>
  elf-function-slice <scaffold|verify> [args]
  pe-auto-trivial --binary <game-dll> --out <dir>
  pe-code-roundtrip --package <one-shot-source-dir> --out-dir <dir> [--prompts-dir prompts]
  pe-code-source-roundtrip --binary <game-exe> --out-dir <dir>
  pe-segmented-code-source-roundtrip --binary <game-exe> --package <one-shot-source-dir> --prompts-dir prompts --out-dir <dir>
  source-parity-one-shot <folder-or-binary> [--resume] [--stop-after <stage>] [--synthesis-limit N] [--synthesis-max-attempts-per-function N]
  recover <folder-or-binary> [--resume] [--stop-after <stage>] [--function-analysis auto|none|objdump] [--function-facts-jsonl <facts.jsonl>] [--source-synthesis clang|clang-cl|dry-run|msvc|none] [--source-synthesis-semantic-only] [--source-synthesis-skip-boundary-suspect] [--snapshot-existing-recovery <label>]
  source-parity-feature-index [--out-dir <dir>]
  source-parity-profile-corpus [--max-cases N]
  source-parity-synthesize [--source-tasks source-generation/tasks.jsonl] [--limit N] [--offset N] [--max-attempts-per-function N] [--semantic-only] [--verify-packaged-source] [--upgrade-packaged-source] [--skip-boundary-suspect] [--source-shape-search] [--dry-run]
  steam-roundtrip-run --out <dir> [--app <substring>]
  steam-roundtrip-verify-manifest --manifest <app/source-roundtrip-manifest.json> --out <dir>
  source-authority-report --input <source-roundtrip-manifest.json|binary-source-roundtrip.json>
  steam-roundtrip-progress [--search-root <dir>]
  steam-roundtrip [--app <substring>] [--json] [--out <path>]
  verify-surface
EOF
}

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
prompt_root="${MIZUCHI_PROMPTS_DIR:-$root_dir/prompts}"
cmd="${1:-}"

json_field() {
  local file="$1" field="$2"
  if [[ -f "$file" ]]; then
    jq -r "$field // \"\"" "$file" 2>/dev/null || true
  fi
}

write_decomp_function_report() {
  local prompt_dir="$1" status="$2" exit_code="$3" terminal_phase="$4" reason="${5:-}"
  local build_dir="$prompt_dir/build"
  local prompt_name
  prompt_name="$(basename "$prompt_dir")"
  local programmatic_report="$build_dir/programmatic-phase.json"
  local ai_report="$build_dir/ai-phase.json"
  local programmatic_status matched_stage ai_status ai_runner

  mkdir -p "$build_dir"
  programmatic_status="$(json_field "$programmatic_report" '.status')"
  matched_stage="$(json_field "$programmatic_report" '.matchedStage')"
  if [[ "$terminal_phase" == "ai" ]]; then
    ai_status="$(json_field "$ai_report" '.status')"
    ai_runner="$(json_field "$ai_report" '.runner')"
  else
    ai_status=""
    ai_runner=""
  fi
  if [[ -z "$reason" ]]; then
    case "$terminal_phase" in
      programmatic) reason="$(json_field "$programmatic_report" '.reason')" ;;
      ai) reason="$(json_field "$ai_report" '.reason')" ;;
    esac
  fi

  jq -n \
    --arg schema "mizuchi.decomp-function.v1" \
    --arg status "$status" \
    --arg prompt "$prompt_name" \
    --arg prompt_dir "$prompt_dir" \
    --arg terminal_phase "$terminal_phase" \
    --arg reason "$reason" \
    --arg programmatic_report "$programmatic_report" \
    --arg ai_report "$ai_report" \
    --arg programmatic_status "$programmatic_status" \
    --arg matched_stage "$matched_stage" \
    --arg ai_status "$ai_status" \
    --arg ai_runner "$ai_runner" \
    --argjson exit_code "$exit_code" \
    '{
      schema: $schema,
      status: $status,
      prompt: $prompt,
      promptDir: $prompt_dir,
      exitCode: $exit_code,
      terminalPhase: $terminal_phase,
      reason: (if $reason == "" then null else $reason end),
      programmaticReport: (if $programmatic_status == "" then null else $programmatic_report end),
      programmaticStatus: (if $programmatic_status == "" then null else $programmatic_status end),
      matchedStage: (if $matched_stage == "" then null else $matched_stage end),
      aiReport: (if $ai_status == "" then null else $ai_report end),
      aiStatus: (if $ai_status == "" then null else $ai_status end),
      aiRunner: (if $ai_runner == "" then null else $ai_runner end)
    }' >"$build_dir/decomp-function.json"
}

if [[ -z "$cmd" ]]; then
  usage
  exit 1
fi
shift || true

case "$cmd" in
  decomp-prompt)
    prompt_name="${1:-}"
    if [[ -z "$prompt_name" ]]; then
      echo "missing prompt name" >&2
      exit 1
    fi
    "$root_dir/scripts/validate-prompt-settings.sh" "$prompt_root/$prompt_name"
    ;;
  decomp-validate)
    prompt_name="${1:-}"
    if [[ -z "$prompt_name" ]]; then
      echo "usage: decomp-validate <prompt-name|--all>" >&2
      exit 1
    fi
    if [[ "$prompt_name" == "--all" ]]; then
      "$root_dir/scripts/validate-case-manifests.sh" "$prompt_root"
    else
      "$root_dir/scripts/validate-prompt-settings.sh" "$prompt_root/$prompt_name"
      "$root_dir/scripts/validate-case-manifests.sh" "$prompt_root/$prompt_name"
    fi
    ;;
  decomp-readiness)
    prompt_name="${1:-}"
    if [[ -z "$prompt_name" ]]; then
      echo "usage: decomp-readiness <prompt-name|--all>" >&2
      exit 1
    fi
    if [[ "$prompt_name" == "--all" ]]; then
      "$root_dir/scripts/decomp-readiness.sh" --all --prompts-dir "$prompt_root"
    else
      "$root_dir/scripts/decomp-readiness.sh" --prompt "$prompt_root/$prompt_name"
    fi
    ;;
  decomp-atlas)
    prompt_name="${1:-}"
    if [[ -z "$prompt_name" ]]; then
      echo "missing prompt name" >&2
      exit 1
    fi
    echo "Use /decomp-atlas to gather similar matches for prompts/$prompt_name"
    ;;
  matcher)
    prompt_name="${1:-}"
    if [[ -z "$prompt_name" || "$prompt_name" == "-h" || "$prompt_name" == "--help" ]]; then
      "$root_dir/scripts/matcher.sh" --help
      exit 0
    fi
    if [[ -z "$prompt_name" ]]; then
      echo "usage: matcher <prompt-name> [matcher args]" >&2
      exit 1
    fi
    shift || true
    "$root_dir/scripts/matcher.sh" --prompt "$prompt_name" --prompts-dir "$prompt_root" "$@"
    ;;
  queue)
    "$root_dir/scripts/lib/queue-state.sh" "$@"
    ;;
  scorer)
    "$root_dir/scripts/scorer.sh" "$@"
    ;;
  vacuum)
    "$root_dir/scripts/vacuum.sh" "$@"
    ;;
  init-vacuum-state)
    "$root_dir/scripts/init-vacuum-state.sh" "$@"
    ;;
  import-one-shot-tasks)
    "$root_dir/scripts/import-one-shot-tasks.py" "$@"
    ;;
  one-shot-task-coverage)
    "$root_dir/scripts/one-shot-task-coverage.py" "$@"
    ;;
  decomp-function)
    prompt_name="${1:-}"
    if [[ -z "$prompt_name" ]]; then
      echo "missing prompt name" >&2
      exit 1
    fi
    prompt_dir="$prompt_root/$prompt_name"
    # Guide pipeline order: phase 1-2 programmatic (get-context -> m2c ->
    # compile/objdiff -> permuter); if that does not reach objdiff 0, fall
    # through to phase 3 (AI Claude loop). Matches the Macabeus/Mizuchi article.
    if "$root_dir/scripts/run-programmatic-phase.sh" --prompt "$prompt_dir"; then
      write_decomp_function_report "$prompt_dir" "matched" 0 "programmatic"
      exit 0
    else
      phase_rc=$?
      if [[ "$phase_rc" -eq 3 ]]; then
        phase_status="$(json_field "$prompt_dir/build/programmatic-phase.json" '.status')"
        if [[ "$phase_status" == "blocked" ]]; then
          write_decomp_function_report "$prompt_dir" "blocked" 3 "programmatic"
        else
          write_decomp_function_report "$prompt_dir" "manual-required" 3 "programmatic"
        fi
        exit 3
      fi
    fi
    echo "==> programmatic phase did not match; entering AI phase" >&2
    set +e
    "$root_dir/scripts/run-ai-phase.sh" --prompt "$prompt_dir"
    ai_rc=$?
    set -e
    ai_status="$(json_field "$prompt_dir/build/ai-phase.json" '.status')"
    if [[ "$ai_rc" -eq 0 ]]; then
      report_status="matched"
    elif [[ "$ai_status" == "blocked" ]]; then
      report_status="blocked"
    elif [[ "$ai_status" == "manual-required" ]]; then
      report_status="manual-required"
    else
      report_status="failed"
    fi
    write_decomp_function_report "$prompt_dir" "$report_status" "$ai_rc" "ai"
    exit "$ai_rc"
    ;;
  decomp-integrate)
    prompt_name="${1:-}"
    source_out="${2:-}"
    if [[ -z "$prompt_name" || -z "$source_out" ]]; then
      echo "usage: decomp-integrate <prompt-name> <source-out>" >&2
      exit 1
    fi
    "$root_dir/scripts/integrate-verified-match.sh" --prompt "$prompt_root/$prompt_name" --source-out "$source_out"
    ;;
  export-context)
    input="${1:-}"
    shift || true
    if [[ -z "${input}" || "$input" == "-h" || "$input" == "--help" || $# -lt 2 ]]; then
      echo "usage: export-context <input> --out-dir <out-dir>" >&2
      echo "       export-context --format json|md, --binary-analysis light|standard|deep" >&2
      echo "       export-context --max-files N --max-depth N --max-hash-bytes N --max-text-bytes N" >&2
      echo "       export-context --max-binary-analysis-bytes N --max-container-members N --strings-limit N --max-index-text-chars N" >&2
      echo "       writes manifest.json, TREE.md, LLM_CONTEXT.json, and LLM_CONTEXT.md" >&2
      exit 1
    fi

    PYTHONPATH="$root_dir/src${PYTHONPATH:+:$PYTHONPATH}" \
      python3 -m mizuchi_re.cli export-context "$input" "$@"
    ;;
  export-context-batch)
    input="${1:-}"
    shift || true
    if [[ -z "${input}" || "$input" == "-h" || "$input" == "--help" || $# -lt 2 ]]; then
      echo "usage: export-context-batch <input> --out-dir <out-dir>" >&2
      echo "       export-context-batch --item-mode matching-files|top-level --suffix suffix --max-items N --min-size N --max-files-per-item N" >&2
      echo "       export-context-batch --max-depth N --max-hash-bytes N --max-text-bytes N" >&2
      echo "       export-context-batch --max-binary-analysis-bytes N --max-container-members N --strings-limit N --max-index-text-chars N" >&2
      echo "       top-level mode writes one LLM-readable package per immediate child app/installer directory" >&2
      exit 1
    fi

    PYTHONPATH="$root_dir/src${PYTHONPATH:+:$PYTHONPATH}" \
      python3 -m mizuchi_re.cli export-context-batch "$input" "$@"
    ;;
  commit-verified-match)
    "$root_dir/scripts/commit-verified-match.sh" "$@"
    ;;
  one-shot-source)
    "$root_dir/scripts/one-shot-source.py" "$@"
    ;;
  one-shot-source-verify)
    "$root_dir/scripts/one-shot-source-verify.py" "$@"
    ;;
  one-shot-source-archive-verify)
    "$root_dir/scripts/one-shot-source-archive-verify.py" "$@"
    ;;
  one-shot-source-claims)
    "$root_dir/scripts/one-shot-source-claims.py" "$@"
    ;;
  one-shot-source-validate)
    "$root_dir/scripts/one-shot-source-validate.py" "$@"
    ;;
  one-shot-source-proof)
    "$root_dir/scripts/one-shot-source-proof.py" "$@"
    ;;
  one-shot-source-deliverable-verify)
    "$root_dir/scripts/one-shot-source-deliverable-verify.py" "$@"
    ;;
  one-shot-source-clean)
    "$root_dir/scripts/one-shot-source-clean.py" "$@"
    ;;
  binary-source-roundtrip)
    "$root_dir/scripts/binary-source-roundtrip.py" "$@"
    ;;
  elf-function-slice)
    "$root_dir/scripts/elf-function-slice.py" "$@"
    ;;
  elf-auto-trivial)
    "$root_dir/scripts/elf-auto-trivial.py" "$@"
    ;;
  pe-auto-trivial)
    "$root_dir/scripts/pe-auto-trivial.py" "$@"
    ;;
  pe-code-roundtrip)
    "$root_dir/scripts/pe-code-roundtrip.py" "$@"
    ;;
  pe-code-source-roundtrip)
    "$root_dir/scripts/pe-code-source-roundtrip.py" "$@"
    ;;
  pe-segmented-code-source-roundtrip)
    "$root_dir/scripts/pe-segmented-code-source-roundtrip.py" "$@"
    ;;
  source-parity-one-shot)
    PYTHONPATH="$root_dir/src" python3 -m mizuchi_re.source_parity_one_shot "$@"
    ;;
  recover)
    PYTHONPATH="$root_dir/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m mizuchi_re.cli recover "$@"
    ;;
  source-parity-feature-index)
    "$root_dir/scripts/source-parity-feature-index.py" "$@"
    ;;
  source-parity-profile-corpus)
    "$root_dir/scripts/source-parity-profile-corpus.py" "$@"
    ;;
  source-parity-synthesize)
    "$root_dir/scripts/source-parity-synthesize.py" "$@"
    ;;
  steam-roundtrip-run)
    "$root_dir/scripts/steam-roundtrip-run.py" "$@"
    ;;
  steam-roundtrip-verify-manifest)
    "$root_dir/scripts/steam-roundtrip-verify-manifest.py" "$@"
    ;;
  source-authority-report)
    "$root_dir/scripts/source-authority-report.py" "$@"
    ;;
  steam-roundtrip-progress)
    "$root_dir/scripts/steam-roundtrip-progress.py" "$@"
    ;;
  steam-roundtrip)
    "$root_dir/scripts/steam-roundtrip-inventory.py" "$@"
    ;;
  verify-surface)
    "$root_dir/scripts/verify-workspace-surface.sh"
    ;;
  *)
    echo "unknown command: $cmd" >&2
    usage
    exit 1
    ;;
esac
