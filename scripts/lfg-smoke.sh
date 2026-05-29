#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 --name <label>" >&2
  exit 2
}

name=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)
      shift
      [[ $# -gt 0 ]] || usage
      name="$1"
      shift
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "unexpected argument: $1" >&2
      usage
      ;;
  esac
done

[[ -n "$name" ]] || usage
printf 'LFG_SMOKE_OK name=%s\n' "$name"
