#!/usr/bin/env bash
set -euo pipefail

build_compile_defensive() {
  local src="${1:?missing source}"
  local out="${2:?missing output object}"
  local max_bytes="${MIZUCHI_BUILD_MAX_BYTES:-5120}"
  local tmp_err tmp_out
  tmp_err="$(mktemp)"
  tmp_out="$(mktemp)"
  trap 'rm -f "$tmp_err" "$tmp_out"' RETURN

  set +e
  gcc -c "$src" -o "$out" >"$tmp_out" 2>"$tmp_err"
  rc=$?
  set -e

  if [[ "$rc" -eq 0 ]]; then
    echo "BUILD_OK"
    return 0
  fi

  first_err="$(head -n 1 "$tmp_err" || true)"
  tail_err="$(tail -c "$max_bytes" "$tmp_err" 2>/dev/null || true)"
  {
    echo "BUILD_FAILED"
    echo "First error: $first_err"
    echo "$tail_err"
  } >&2
  return "$rc"
}
