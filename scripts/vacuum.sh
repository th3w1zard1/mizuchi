#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/vacuum-backoff.sh
source "$ROOT/scripts/lib/vacuum-backoff.sh"

usage() {
  cat >&2 <<'EOF'
usage:
  vacuum.sh start|resume [--queue state/queue.json] [--prompts-dir prompts] [--scores state/scores.json]
                          [--max-functions N] [--max-attempts N] [--runner-command <cmd>]
                          [--log logs/progress.log] [--session state/vacuum-session.json]
                          [--timeout 8h|30m|60s] [--backoff-base seconds] [--backoff-max seconds] [--no-sleep]
                          [--commit-after-match] [--commit-dry-run] [--commit-path <path>]
  vacuum.sh status [--queue state/queue.json]
  vacuum.sh inspect-queue [--queue state/queue.json]
  vacuum.sh reset-queue --name <fn> [--queue state/queue.json]
  vacuum.sh init [--prompts-dir prompts] [--queue state/queue.json] [--scores state/scores.json]

Runner placeholders: {{name}}, {{promptDir}}, {{queue}}.
Default runner: ./scripts/decomp-cli.sh decomp-function {{name}}
EOF
}

queue="state/queue.json"
prompts_dir="$ROOT/prompts"
scores="state/scores.json"
max_functions=1
max_attempts=10
runner_command=""
progress_log="logs/vacuum-progress.log"
session_file="state/vacuum-session.json"
backoff_base=300
backoff_max=3600
sleep_enabled=true
reset_name=""
timeout_seconds=""
commit_after_match=false
commit_dry_run=false
commit_paths=()

cmd="${1:-}"
[[ -n "$cmd" ]] && shift || true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --queue) queue="$2"; shift 2 ;;
    --prompts-dir) prompts_dir="$2"; shift 2 ;;
    --scores) scores="$2"; shift 2 ;;
    --max-functions) max_functions="$2"; shift 2 ;;
    --max-attempts) max_attempts="$2"; shift 2 ;;
    --runner-command) runner_command="$2"; shift 2 ;;
    --log) progress_log="$2"; shift 2 ;;
    --session) session_file="$2"; shift 2 ;;
    --timeout) timeout_seconds="$2"; shift 2 ;;
    --backoff-base) backoff_base="$2"; shift 2 ;;
    --backoff-max) backoff_max="$2"; shift 2 ;;
    --no-sleep) sleep_enabled=false; shift ;;
    --commit-after-match) commit_after_match=true; shift ;;
    --commit-dry-run) commit_dry_run=true; shift ;;
    --commit-path) commit_paths+=("$2"); shift 2 ;;
    --name) reset_name="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "vacuum: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

queue_abs="$queue"
scores_abs="$scores"
log_abs="$progress_log"
session_abs="$session_file"
[[ "$queue_abs" = /* ]] || queue_abs="$ROOT/$queue_abs"
[[ "$scores_abs" = /* ]] || scores_abs="$ROOT/$scores_abs"
[[ "$log_abs" = /* ]] || log_abs="$ROOT/$log_abs"
[[ "$session_abs" = /* ]] || session_abs="$ROOT/$session_abs"

timestamp() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

epoch_seconds() {
  date -u +%s
}

parse_duration_seconds() {
  local value="$1" number unit
  if [[ "$value" =~ ^([0-9]+)([smh]?)$ ]]; then
    number="${BASH_REMATCH[1]}"
    unit="${BASH_REMATCH[2]}"
    case "$unit" in
      h) printf '%s\n' "$((number * 3600))" ;;
      m) printf '%s\n' "$((number * 60))" ;;
      s|"") printf '%s\n' "$number" ;;
    esac
  else
    echo "vacuum: invalid --timeout duration: $value" >&2
    exit 2
  fi
}

log_progress() {
  local line="$1"
  mkdir -p "$(dirname "$log_abs")"
  printf '[%s] %s\n' "$(timestamp)" "$line" | tee -a "$log_abs" >&2
}

json_summary() {
  "$ROOT/scripts/lib/queue-state.sh" summary --queue "$queue_abs"
}

write_session() {
  local status="$1" name="${2:-}" message="${3:-}" backoff="${4:-0}" debug_log="${5:-}"
  mkdir -p "$(dirname "$session_abs")"
  jq -n \
    --arg schema "reconkit.vacuum-session.v1" \
    --arg status "$status" \
    --arg name "$name" \
    --arg message "$message" \
    --arg queue "$queue_abs" \
    --arg scores "$scores_abs" \
    --arg log "$log_abs" \
    --arg debug_log "$debug_log" \
    --arg updated_at "$(timestamp)" \
    --argjson backoff "$backoff" \
    '{
      schema: $schema,
      status: $status,
      updatedAt: $updated_at,
      queue: $queue,
      scores: $scores,
      log: $log,
      currentFunction: (if $name == "" then null else $name end),
      message: (if $message == "" then null else $message end),
      backoffSeconds: $backoff,
      debugLog: (if $debug_log == "" then null else $debug_log end)
    }' >"$session_abs"
}

render_runner_command() {
  local name="$1" prompt_dir="$2" command="$runner_command"
  if [[ -z "$command" ]]; then
    printf '%q ' "$ROOT/scripts/decomp-cli.sh" "decomp-function" "$name"
    return 0
  fi
  command="${command//\{\{name\}\}/$name}"
  command="${command//\{\{promptDir\}\}/$prompt_dir}"
  command="${command//\{\{queue\}\}/$queue_abs}"
  printf '%s\n' "$command"
}

attempt_count_for() {
  local name="$1"
  if [[ ! -f "$queue_abs" ]]; then
    printf '0\n'
    return 0
  fi
  jq -r --arg name "$name" '.attempts[$name].count // 0' "$queue_abs"
}

commit_verified_match() {
  local prompt_dir="$1" name="$2" commit_log="$3"
  local args=("$ROOT/scripts/commit-verified-match.sh" --prompt "$prompt_dir" --message "Match $name (verified)")
  if [[ "$commit_dry_run" == true ]]; then
    args+=(--dry-run)
  fi
  for path in "${commit_paths[@]}"; do
    args+=(--path "$path")
  done
  "${args[@]}" >"$commit_log" 2>&1
}

vacuum_start() {
  local deadline=0
  if [[ -n "$timeout_seconds" ]]; then
    deadline="$(( $(epoch_seconds) + $(parse_duration_seconds "$timeout_seconds") ))"
  fi

  on_interrupt() {
    log_progress "VACUUM_INTERRUPTED signal received"
    write_session "interrupted" "" "signal received" 0 ""
    exit 130
  }
  trap on_interrupt INT TERM

  if [[ ! -f "$queue_abs" ]]; then
    "$ROOT/scripts/lib/queue-state.sh" init --queue "$queue_abs" --prompts-dir "$prompts_dir" >/dev/null
  fi

  "$ROOT/scripts/scorer.sh" --prompts-dir "$prompts_dir" --queue "$queue_abs" --update-queue --out "$scores_abs" >/dev/null

  local processed=0
  while [[ "$processed" -lt "$max_functions" ]]; do
    local next name prompt_dir debug_log rendered rc attempts status backoff commit_log
    if [[ "$deadline" -gt 0 && "$(epoch_seconds)" -ge "$deadline" ]]; then
      log_progress "VACUUM_TIMEOUT deadline reached"
      write_session "timeout" "" "deadline reached" 0 ""
      break
    fi

    next="$("$ROOT/scripts/lib/queue-state.sh" next --queue "$queue_abs")"
    if [[ -z "$next" ]]; then
      log_progress "VACUUM_IDLE no pending functions"
      write_session "idle" "" "no pending functions" 0 ""
      break
    fi

    name="$(printf '%s\n' "$next" | jq -r '.name')"
    prompt_dir="$prompts_dir/$name"
    debug_log="$(dirname "$log_abs")/vacuum-$name-$(date -u +%Y%m%dT%H%M%SZ).log"
    rendered="$(render_runner_command "$name" "$prompt_dir")"

    log_progress "$name START runner=$rendered"
    write_session "running" "$name" "runner started" 0 "$debug_log"

    set +e
    bash -lc "$rendered" >"$debug_log" 2>&1
    rc=$?
    set -e

    if [[ "$rc" -eq 0 ]]; then
      if [[ "$commit_after_match" == true ]]; then
        commit_log="$(dirname "$log_abs")/vacuum-$name-commit-$(date -u +%Y%m%dT%H%M%SZ).log"
        if ! commit_verified_match "$prompt_dir" "$name" "$commit_log"; then
          "$ROOT/scripts/lib/queue-state.sh" attempt --queue "$queue_abs" --name "$name" --status commit_failed --message "log=$commit_log" >/dev/null
          "$ROOT/scripts/lib/queue-state.sh" move --queue "$queue_abs" --name "$name" --to failed --reason "verified commit failed; log=$commit_log" >/dev/null
          log_progress "$name COMMIT_FAILED log=$commit_log"
          write_session "commit_failed" "$name" "verified commit failed" 0 "$commit_log"
          processed=$((processed + 1))
          continue
        fi
        log_progress "$name COMMIT_VERIFIED log=$commit_log"
      fi
      "$ROOT/scripts/lib/queue-state.sh" move --queue "$queue_abs" --name "$name" --to matched --reason "vacuum runner matched; log=$debug_log" >/dev/null
      "$ROOT/scripts/lib/queue-state.sh" attempt --queue "$queue_abs" --name "$name" --status matched --message "exit=0 log=$debug_log" >/dev/null
      log_progress "$name MATCHED log=$debug_log"
      write_session "matched" "$name" "runner matched" 0 "$debug_log"
      processed=$((processed + 1))
      continue
    fi

    if vacuum_is_quota_log "$debug_log"; then
      "$ROOT/scripts/lib/queue-state.sh" attempt --queue "$queue_abs" --name "$name" --status quota --message "exit=$rc log=$debug_log" >/dev/null
      attempts="$(attempt_count_for "$name")"
      backoff="$(vacuum_backoff_seconds "$attempts" "$backoff_base" "$backoff_max")"
      log_progress "$name BACKOFF quota exit=$rc attempts=$attempts wait=${backoff}s log=$debug_log"
      write_session "backoff" "$name" "quota detected" "$backoff" "$debug_log"
      if [[ "$sleep_enabled" == true ]]; then
        sleep "$backoff"
      fi
      processed=$((processed + 1))
      continue
    fi

    "$ROOT/scripts/lib/queue-state.sh" attempt --queue "$queue_abs" --name "$name" --status failed --message "exit=$rc log=$debug_log" >/dev/null
    attempts="$(attempt_count_for "$name")"
    if [[ "$attempts" -ge "$max_attempts" ]]; then
      status="difficult"
    else
      status="failed"
    fi
    "$ROOT/scripts/lib/queue-state.sh" move --queue "$queue_abs" --name "$name" --to "$status" --reason "vacuum runner exit=$rc after $attempts attempts; log=$debug_log" >/dev/null
    log_progress "$name ${status^^} exit=$rc attempts=$attempts log=$debug_log"
    write_session "$status" "$name" "runner failed" 0 "$debug_log"
    processed=$((processed + 1))
  done

  jq -n \
    --arg schema "reconkit.vacuum.v1" \
    --arg queue "$queue_abs" \
    --arg scores "$scores_abs" \
    --arg log "$log_abs" \
    --arg session "$session_abs" \
    --arg timeout "$timeout_seconds" \
    --argjson processed "$processed" \
    --argjson summary "$(json_summary)" \
    '{schema: $schema, status: "finished", processed: $processed, queue: $queue, scores: $scores, log: $log, session: $session, timeout: (if $timeout == "" then null else $timeout end), summary: $summary}'
}

case "$cmd" in
  init)
    "$ROOT/scripts/init-vacuum-state.sh" --prompts-dir "$prompts_dir" --queue "$queue_abs" --scores "$scores_abs" --log-dir "$(dirname "$log_abs")" --session "$session_abs"
    ;;
  start|resume)
    vacuum_start
    ;;
  status)
    if [[ -f "$session_abs" ]]; then
      jq -n \
        --arg schema "reconkit.vacuum-status.v1" \
        --arg queue "$queue_abs" \
        --arg log "$log_abs" \
        --arg session "$session_abs" \
        --slurpfile session_doc "$session_abs" \
        --argjson summary "$(json_summary)" \
        '{schema: $schema, queue: $queue, log: $log, session: $session, summary: $summary, lastSession: ($session_doc[0] // null)}'
    else
      jq -n \
        --arg schema "reconkit.vacuum-status.v1" \
        --arg queue "$queue_abs" \
        --arg log "$log_abs" \
        --arg session "$session_abs" \
        --argjson summary "$(json_summary)" \
        '{schema: $schema, queue: $queue, log: $log, session: $session, summary: $summary, lastSession: null}'
    fi
    ;;
  inspect-queue)
    "$ROOT/scripts/lib/queue-state.sh" summary --queue "$queue_abs" >/dev/null
    jq '.' "$queue_abs"
    ;;
  reset-queue)
    if [[ -z "$reset_name" ]]; then
      echo "vacuum: reset-queue requires --name <fn>" >&2
      exit 2
    fi
    "$ROOT/scripts/lib/queue-state.sh" move --queue "$queue_abs" --name "$reset_name" --to pending --reason "reset by vacuum reset-queue"
    ;;
  -h|--help|"")
    usage
    ;;
  *)
    echo "vacuum: unknown command: $cmd" >&2
    usage
    exit 2
    ;;
esac
