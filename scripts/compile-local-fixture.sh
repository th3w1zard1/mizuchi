#!/usr/bin/env bash
# Deterministic local compiler for Mizuchi proof fixtures.
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <source.c> <output.o>" >&2
  exit 2
fi

source_c="$1"
output_o="$2"

if [[ ! -f "$source_c" ]]; then
  echo "compile-local-fixture: missing source: $source_c" >&2
  exit 1
fi

mkdir -p "$(dirname "$output_o")"

cc="${CC:-gcc}"
"$cc" \
  -x c \
  -std=c99 \
  -O2 \
  -fno-asynchronous-unwind-tables \
  -fno-stack-protector \
  -fno-ident \
  -ffunction-sections \
  -fdata-sections \
  -c - \
  -o "$output_o" <"$source_c"
