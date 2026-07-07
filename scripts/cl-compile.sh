#!/usr/bin/env bash
# mizuchi compilerScript backend: compile a candidate .c with the real MSVC
# cl.exe (portable VC8, run under wine) to a COFF .obj. This is what makes the
# article's objdiff gate meaningful for x86/MSVC targets. gcc/clang cannot
# reproduce MSVC codegen; this wrapper has produced byte-exact output for small
# swkotor accessors/wrappers, but it is not proof of the whole compiler profile.
#
# Usage: cl-compile.sh <in.c> <out.obj> [extra cl flags...]
# Env:
#   VC_ROOT     portable MSVC dir (contains bin/cl.exe)   default: /tmp/msvc80/msvc8.0-main
#   WINEPREFIX  wine prefix                                default: /tmp/vctk2003/wineprefix
#   CL_OPT      optimization flag                          default: /O2
set -euo pipefail

VC_ROOT="${VC_ROOT:-/run/media/brunner56/MyBook/MizuchiSource/toolchains/msvc8.0-main}"
export WINEPREFIX="${WINEPREFIX:-/tmp/vctk2003/wineprefix}"
export WINEDEBUG="${WINEDEBUG:--all}"
CL_OPT="${CL_OPT:-/O2}"

in_c="${1:?usage: cl-compile.sh <in.c> <out.obj> [flags]}"
out_obj="${2:?missing out.obj}"
shift 2 || true

[[ -f "$VC_ROOT/bin/cl.exe" ]] || { echo "cl.exe not found at $VC_ROOT/bin (set VC_ROOT)" >&2; exit 3; }

# Map UNIX include dirs to wine Z: paths.
winepath() { printf 'Z:%s' "$(printf '%s' "$1" | sed 's:/:\\:g')"; }
export INCLUDE="$(winepath "$VC_ROOT/include");$(winepath "$VC_ROOT/PlatformSDK/include")"
export WINEPATH="$VC_ROOT/bin"

# Compile in a temp dir so cl's cwd output files don't collide.
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cp "$in_c" "$tmp/in.c"
( cd "$tmp" && wine "$VC_ROOT/bin/cl.exe" /nologo /c $CL_OPT "$@" /Foout.obj in.c >cl.log 2>&1 ) || {
  echo "cl.exe failed:" >&2; cat "$tmp/cl.log" >&2; exit 1;
}
cp "$tmp/out.obj" "$out_obj"
echo "compiled $in_c -> $out_obj (MSVC cl.exe $CL_OPT)" >&2
