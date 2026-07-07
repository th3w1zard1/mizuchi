#!/usr/bin/env bash
# Parse a one-shot matcher response into candidate C source.
set -euo pipefail

matcher_parse_usage() {
  echo "usage: matcher-parse.sh --input <response.txt> --out <trial.c> [--json <report.json>]" >&2
}

matcher_parse_main() {
  local input="" out_file="" json_file=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --input) input="$2"; shift 2 ;;
      --out) out_file="$2"; shift 2 ;;
      --json) json_file="$2"; shift 2 ;;
      -h|--help) matcher_parse_usage; return 0 ;;
      *) echo "matcher-parse: unknown option: $1" >&2; matcher_parse_usage; return 2 ;;
    esac
  done

  if [[ -z "$input" || -z "$out_file" ]]; then
    matcher_parse_usage
    return 2
  fi
  if [[ ! -f "$input" ]]; then
    echo "matcher-parse: input not found: $input" >&2
    return 1
  fi

  local tmp code_bytes status reason
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' RETURN

  if ! awk '
    BEGIN { in_block = 0; seen = 0 }
    /^```/ {
      fence = $0
      if (in_block) {
        exit
      }
      sub(/^```[[:space:]]*/, "", fence)
      lang = tolower(fence)
      if (lang == "" || lang == "c" || lang == "cpp" || lang == "c++" || lang == "h") {
        in_block = 1
        seen = 1
      }
      next
    }
    in_block { print }
    END {
      if (seen == 0) {
        exit 3
      }
    }
  ' "$input" >"$tmp"; then
    if grep -qE '^[[:space:]]*(#include|__asm__[[:space:]]*\(|[A-Za-z_][A-Za-z0-9_[:space:]\*]*\([^;]*\)[[:space:]]*\{|typedef|struct|enum|static[[:space:]])' "$input"; then
      cp "$input" "$tmp"
    else
      status="parse_error"
      reason="no fenced C code block found"
      if [[ -n "$json_file" ]]; then
        mkdir -p "$(dirname "$json_file")"
        jq -n --arg status "$status" --arg reason "$reason" --arg input "$input" \
          '{schema:"reconkit.matcher-parse.v1", status:$status, reason:$reason, input:$input, output:null, bytes:0}' >"$json_file"
      fi
      echo "matcher-parse: $reason" >&2
      return 1
    fi
  fi

  if [[ ! -s "$tmp" ]]; then
    status="empty_response"
    reason="fenced C code block was empty"
    if [[ -n "$json_file" ]]; then
      mkdir -p "$(dirname "$json_file")"
      jq -n --arg status "$status" --arg reason "$reason" --arg input "$input" \
        '{schema:"reconkit.matcher-parse.v1", status:$status, reason:$reason, input:$input, output:null, bytes:0}' >"$json_file"
    fi
    echo "matcher-parse: $reason" >&2
    return 1
  fi

  mkdir -p "$(dirname "$out_file")"
  cp "$tmp" "$out_file"
  code_bytes="$(stat -c %s "$out_file")"
  if [[ -n "$json_file" ]]; then
    mkdir -p "$(dirname "$json_file")"
    jq -n \
      --arg input "$input" \
      --arg output "$out_file" \
      --argjson bytes "$code_bytes" \
      '{schema:"reconkit.matcher-parse.v1", status:"parsed", reason:null, input:$input, output:$output, bytes:$bytes}' >"$json_file"
  fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  matcher_parse_main "$@"
fi
