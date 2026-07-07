#!/usr/bin/env bash
# Sweep real swkotor function slices across MSVC toolchains/flags and record
# objdiff evidence. This is compiler forensics, not a semantic match claim.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: swkotor-compiler-profile.sh [--case FUN_0086d201]... [--out DIR]

Environment:
  VC71_ROOT   Visual C++ Toolkit 2003 root
  VC80_ROOT   MSVC 8 portable root
  WINEPREFIX  Wine prefix for cl.exe

Outputs:
  <out>/summary.jsonl  one JSON object per attempted profile
  <out>/summary.tsv    compact sortable view
  <out>/runs/...       compiled objects, compiler logs, verifier reports
EOF
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/target/swkotor-compiler-profile"
VC71_ROOT="${VC71_ROOT:-$ROOT/target/toolchain-acquire/vctoolkit2003/msitools-extract/Program Files/Microsoft Visual C++ Toolkit 2003}"
VC80_ROOT="${VC80_ROOT:-/run/media/brunner56/MyBook/ReconstructKitSource/toolchains/msvc8.0-main}"
WINEPREFIX="${WINEPREFIX:-$ROOT/target/toolchain-acquire/vctoolkit2003/wineprefix}"

cases=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --case)
      cases+=("${2:?missing --case value}")
      shift 2
      ;;
    --out)
      OUT="${2:?missing --out value}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unexpected argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ "${#cases[@]}" -eq 0 ]]; then
  cases=(FUN_0086d201 FUN_0086d266)
fi

require_tool() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "required tool not found on PATH: $1" >&2
    exit 1
  }
}

require_tool jq
require_tool wine
require_tool objdiff

mkdir -p "$OUT/runs"
: >"$OUT/summary.jsonl"

profiles=()
if [[ -f "$VC71_ROOT/bin/cl.exe" ]]; then
  profiles+=("vc71|$VC71_ROOT")
else
  echo "warning: skipping vc71; cl.exe not found at $VC71_ROOT/bin/cl.exe" >&2
fi
if [[ -f "$VC80_ROOT/bin/cl.exe" ]]; then
  profiles+=("vc80|$VC80_ROOT")
else
  echo "warning: skipping vc80; cl.exe not found at $VC80_ROOT/bin/cl.exe" >&2
fi

if [[ "${#profiles[@]}" -eq 0 ]]; then
  echo "no compiler profiles available" >&2
  exit 1
fi

# Format: id|CL_OPT|extra flags. Keep this small and evidence-driven.
flag_specs=(
  "od_oyminus_gsminus|/Od|/Oy- /GS-"
  "od_gz_oyminus_gsminus|/Od|/GZ /Oy- /GS-"
  "od_rtc1_oyminus_gsminus|/Od|/RTC1 /Oy- /GS-"
  "od_g7_oyminus_gsminus|/Od|/G7 /Oy- /GS-"
  "od_oi_oyminus_gsminus|/Od|/Oi /Oy- /GS-"
  "o1_oyminus_gsminus|/O1|/Oy- /GS-"
  "o2_oyminus_gsminus|/O2|/Oy- /GS-"
  "o2_oy_gsminus|/O2|/Oy /GS-"
)

json_tail() {
  local file="$1"
  if [[ -f "$file" ]]; then
    tail -40 "$file" | jq -Rs .
  else
    jq -n '""'
  fi
}

emit_json() {
  jq -c -n "$@"
}

for case_name in "${cases[@]}"; do
  case_dir="$ROOT/target/swkotor-match/$case_name"
  candidate="$case_dir/candidate.c"
  target="$case_dir/target.obj"

  if [[ ! -f "$candidate" || ! -f "$target" ]]; then
    emit_json \
      --arg schema "reconkit.swkotor-compiler-profile.v1" \
      --arg case "$case_name" \
      --arg status "missing-input" \
      --arg candidate "$candidate" \
      --arg target "$target" \
      '{schema:$schema, case:$case, status:$status, candidate:$candidate, target:$target}' \
      >>"$OUT/summary.jsonl"
    continue
  fi

  for profile in "${profiles[@]}"; do
    IFS='|' read -r profile_id vc_root <<<"$profile"

    for spec in "${flag_specs[@]}"; do
      IFS='|' read -r flag_id cl_opt extra_flags <<<"$spec"
      run_dir="$OUT/runs/$case_name/$profile_id/$flag_id"
      mkdir -p "$run_dir"

      obj="$run_dir/candidate.obj"
      compile_stdout="$run_dir/compile.stdout"
      compile_stderr="$run_dir/compile.stderr"
      verify_json="$run_dir/verify.json"
      verify_raw="$run_dir/verify.raw.json"
      banner_file="$run_dir/cl-banner.txt"

      set +e
      WINEPREFIX="$WINEPREFIX" wine "$vc_root/bin/cl.exe" >"$banner_file" 2>&1
      banner_exit=$?
      set -e
      if [[ "$banner_exit" -ne 0 && ! -s "$banner_file" ]]; then
        printf 'cl.exe banner unavailable, exit=%s\n' "$banner_exit" >"$banner_file"
      fi

      read -r -a extra_args <<<"$extra_flags"
      set +e
      VC_ROOT="$vc_root" \
      WINEPREFIX="$WINEPREFIX" \
      CL_OPT="$cl_opt" \
        timeout 90 bash "$ROOT/scripts/cl-compile.sh" \
          "$candidate" "$obj" "${extra_args[@]}" \
          >"$compile_stdout" 2>"$compile_stderr"
      compile_exit=$?
      set -e

      if [[ "$compile_exit" -ne 0 ]]; then
        emit_json \
          --arg schema "reconkit.swkotor-compiler-profile.v1" \
          --arg case "$case_name" \
          --arg profile "$profile_id" \
          --arg flagSet "$flag_id" \
          --arg clOpt "$cl_opt" \
          --arg extraFlags "$extra_flags" \
          --arg status "compile-failed" \
          --argjson compileExit "$compile_exit" \
          --arg compilerRoot "$vc_root" \
          --arg banner "$(tr '\r' '\n' <"$banner_file" | head -5)" \
          --argjson compileStderrTail "$(json_tail "$compile_stderr")" \
          '{schema:$schema, case:$case, profile:$profile, flagSet:$flagSet, clOpt:$clOpt, extraFlags:$extraFlags, status:$status, compileExit:$compileExit, compilerRoot:$compilerRoot, banner:$banner, compileStderrTail:$compileStderrTail}' \
          >>"$OUT/summary.jsonl"
        continue
      fi

      set +e
      bash "$ROOT/scripts/lib/verify-objdiff.sh" "$target" "$obj" \
        --out "$verify_json" --raw-out "$verify_raw" >"$run_dir/verify.stdout" 2>"$run_dir/verify.stderr"
      verify_exit=$?
      set -e

      status="$(jq -r '.status // "error"' "$verify_json" 2>/dev/null || printf 'error')"
      differences="$(jq -r '.differences // -1' "$verify_json" 2>/dev/null || printf -- '-1')"
      match_percent="$(
        jq -r '
          try (
            .output
            | fromjson
            | [
                .. | objects
                | select(has("match_percent"))
                | select((.kind == "SECTION_CODE") or (.kind == "SYMBOL_FUNCTION") or has("instructions"))
                | .match_percent
              ]
            | max // null
          ) catch null
        ' "$verify_json" 2>/dev/null
      )"
      [[ "$match_percent" != "null" ]] || match_percent=""

      emit_json \
        --arg schema "reconkit.swkotor-compiler-profile.v1" \
        --arg case "$case_name" \
        --arg profile "$profile_id" \
        --arg flagSet "$flag_id" \
        --arg clOpt "$cl_opt" \
        --arg extraFlags "$extra_flags" \
        --arg status "$status" \
        --argjson differences "$differences" \
        --arg bestMatchPercent "$match_percent" \
        --argjson verifyExit "$verify_exit" \
        --arg compilerRoot "$vc_root" \
        --arg object "$obj" \
        --arg verify "$verify_json" \
        --arg banner "$(tr '\r' '\n' <"$banner_file" | head -5)" \
        '{
          schema:$schema,
          case:$case,
          profile:$profile,
          flagSet:$flagSet,
          clOpt:$clOpt,
          extraFlags:$extraFlags,
          status:$status,
          differences:$differences,
          bestMatchPercent: (if $bestMatchPercent == "" then null else ($bestMatchPercent | tonumber) end),
          verifyExit:$verifyExit,
          compilerRoot:$compilerRoot,
          object:$object,
          verify:$verify,
          banner:$banner
        }' \
        >>"$OUT/summary.jsonl"
    done
  done
done

jq -sr '
  [
    ["case","profile","flagSet","status","match_percent","differences","clOpt","extraFlags"]
  ] + (
    . | map([.case,.profile,.flagSet,.status,(.bestMatchPercent // ""),(.differences // ""),.clOpt,.extraFlags])
  )
  | .[]
  | @tsv
' "$OUT/summary.jsonl" >"$OUT/summary.tsv"

echo "wrote $OUT/summary.jsonl" >&2
echo "wrote $OUT/summary.tsv" >&2
