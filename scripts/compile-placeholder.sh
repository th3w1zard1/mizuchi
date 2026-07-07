#!/usr/bin/env bash
# Placeholder compiler for ReconstructKit — replace with your decomp project's compile script.
# ReconstructKit invokes: compilerScript <source.c> <output.o>
# Exit 0 on success; non-zero on compile failure.
set -euo pipefail
if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <source.c> <output.o>" >&2
  exit 2
fi
echo "compile-placeholder: wire a real compiler (MSVC/clang) in reconkit.yaml global.compilerScript" >&2
exit 1
