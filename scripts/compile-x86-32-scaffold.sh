#!/usr/bin/env bash
# Deterministic local x86 compiler for asm-derived prompt scaffolds.
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <source.c|source.S> <output.o>" >&2
  exit 2
fi

source_file="$1"
output_o="$2"

if [[ ! -f "$source_file" ]]; then
  echo "compile-x86-32-scaffold: missing source: $source_file" >&2
  exit 1
fi

mkdir -p "$(dirname "$output_o")"
cc="${CC:-gcc}"

case "$source_file" in
  *.S|*.s)
    "$cc" -m32 -x assembler -c "$source_file" -o "$output_o"
    ;;
  *)
    "$cc" \
      -m32 \
      -x c \
      -std=gnu99 \
      -O2 \
      -ffreestanding \
      -fno-pic \
      -fno-pie \
      -fno-asynchronous-unwind-tables \
      -fno-stack-protector \
      -fno-ident \
      -c "$source_file" \
      -o "$output_o"
    ;;
esac
