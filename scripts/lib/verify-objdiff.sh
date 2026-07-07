#!/usr/bin/env bash
# Shared objdiff runner/parser for Mizuchi verification gates.
set -euo pipefail

verify_objdiff_usage() {
  cat >&2 <<'EOF'
usage: verify-objdiff.sh <target.o> <candidate.o> [--out <json>] [--raw-out <file>]

Runs objdiff and emits normalized JSON:
  {"status":"matched|mismatched|error","differences":0|N|-1,...}
Exit 0 when objdiff ran and parsed; exit 1 on tool/input errors; exit 2 on usage.
EOF
}

verify_objdiff_parse() {
  local raw_file="$1" objdiff_exit="$2"
  local body differences match_percents
  body="$(cat "$raw_file")"
  differences=-1

  if [[ "$objdiff_exit" -eq 0 ]]; then
    match_percents="$(
      jq -c '
        [
          .. | objects
          | select(has("match_percent"))
          | select((.kind == "SECTION_CODE") or (.kind == "SYMBOL_FUNCTION") or has("instructions"))
          | .match_percent
        ]
      ' "$raw_file" 2>/dev/null || printf '[]'
    )"
    if jq -e 'length > 0 and all(.[]; . == 100)' <<<"$match_percents" >/dev/null 2>&1; then
      differences=0
    elif jq -e 'length > 0 and any(.[]; . != 100)' <<<"$match_percents" >/dev/null 2>&1; then
      differences=1
    elif grep -qiE '(^|[^0-9])(0[[:space:]]*(diff|differences)|no diff|identical|perfect match)' <<<"$body"; then
      differences=0
    elif grep -qiE '[1-9][0-9]*[[:space:]]*(diff|difference|differences)' <<<"$body"; then
      differences="$(grep -oiE '[1-9][0-9]*[[:space:]]*(diff|difference|differences)' <<<"$body" | head -1 | grep -oE '^[0-9]+')"
      [[ -n "$differences" ]] || differences=1
    elif [[ -z "${body//[[:space:]]/}" ]]; then
      # Some objdiff versions/plugins emit no stdout on a clean match.
      differences=0
    fi
  fi

  if [[ "$objdiff_exit" -ne 0 ]]; then
    jq -n \
      --argjson differences "$differences" \
      --arg output "$body" \
      --argjson objdiffExit "$objdiff_exit" \
      '{
        schema: "mizuchi.verify-objdiff.v1",
        status: "error",
        differences: $differences,
        message: "objdiff exited with error",
        objdiffExit: $objdiffExit,
        output: $output
      }'
  elif [[ "$differences" -eq 0 ]]; then
    jq -n \
      --arg output "$body" \
      '{
        schema: "mizuchi.verify-objdiff.v1",
        status: "matched",
        differences: 0,
        message: "Object files match",
        output: $output
      }'
  elif [[ "$differences" -gt 0 ]]; then
    jq -n \
      --argjson differences "$differences" \
      --arg output "$body" \
      '{
        schema: "mizuchi.verify-objdiff.v1",
        status: "mismatched",
        differences: $differences,
        message: "Object files do not match",
        output: $output
      }'
  else
    jq -n \
      --arg output "$body" \
      '{
        schema: "mizuchi.verify-objdiff.v1",
        status: "error",
        differences: -1,
        message: "could not confirm objdiff result from output",
        output: $output
      }'
  fi
}

verify_objdiff_main() {
  local target="" candidate="" out_file="" raw_out=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --out) out_file="$2"; shift 2 ;;
      --raw-out) raw_out="$2"; shift 2 ;;
      -h|--help) verify_objdiff_usage; return 0 ;;
      *)
        if [[ -z "$target" ]]; then
          target="$1"
        elif [[ -z "$candidate" ]]; then
          candidate="$1"
        else
          echo "verify-objdiff: unexpected argument: $1" >&2
          verify_objdiff_usage
          return 2
        fi
        shift
        ;;
    esac
  done

  if [[ -z "$target" || -z "$candidate" ]]; then
    jq -n --arg message "target and candidate paths required" \
      '{schema:"mizuchi.verify-objdiff.v1", status:"error", differences:-1, message:$message}'
    return 1
  fi
  if [[ ! -f "$target" ]]; then
    jq -n --arg file "$target" --arg message "Target file not found: $target" \
      '{schema:"mizuchi.verify-objdiff.v1", status:"error", differences:-1, message:$message, file:$file}'
    return 1
  fi
  if [[ ! -f "$candidate" ]]; then
    jq -n --arg file "$candidate" --arg message "Candidate file not found: $candidate" \
      '{schema:"mizuchi.verify-objdiff.v1", status:"error", differences:-1, message:$message, file:$file}'
    return 1
  fi
  if ! command -v objdiff >/dev/null 2>&1; then
    jq -n --arg message "objdiff not found on PATH (install from https://github.com/encounter/objdiff)" \
      '{schema:"mizuchi.verify-objdiff.v1", status:"error", differences:-1, message:$message}'
    return 1
  fi

  local tmp_raw tmp_report objdiff_exit report_status
  tmp_raw="$(mktemp)"
  tmp_report="$(mktemp)"
  trap 'rm -f "$tmp_raw" "$tmp_report"' RETURN

  set +e
  objdiff diff -1 "$target" -2 "$candidate" -o - --format json-pretty >"$tmp_raw" 2>&1
  objdiff_exit=$?
  set -e

  verify_objdiff_parse "$tmp_raw" "$objdiff_exit" >"$tmp_report"
  report_status="$(jq -r '.status' "$tmp_report")"

  if [[ -n "$raw_out" ]]; then
    mkdir -p "$(dirname "$raw_out")"
    cp "$tmp_raw" "$raw_out"
  fi
  if [[ -n "$out_file" ]]; then
    mkdir -p "$(dirname "$out_file")"
    cp "$tmp_report" "$out_file"
  fi
  cat "$tmp_report"

  [[ "$report_status" != "error" ]]
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  verify_objdiff_main "$@"
fi
