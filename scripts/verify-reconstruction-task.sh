#!/usr/bin/env bash
# Verify a candidate.c against a one-shot-source function reconstruction task.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage:
	  verify-reconstruction-task.sh --task-dir <function-reconstruction-tasks/name>
	                                [--verifier <path/inside/task-dir>]
	                                --candidate <candidate.c>
	                                --candidate-output <candidate.bin>

Runs the task-local verifier in a temporary copy, then copies the produced
candidate.bin to --candidate-output for build-and-verify hashing.
EOF
}

task_dir=""
verifier="VERIFY_CANDIDATE.sh"
candidate=""
candidate_output=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --task-dir) task_dir="$2"; shift 2 ;;
    --verifier) verifier="$2"; shift 2 ;;
    --candidate) candidate="$2"; shift 2 ;;
    --candidate-output) candidate_output="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "verify-reconstruction-task: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$task_dir" || -z "$candidate" || -z "$candidate_output" ]]; then
  usage
  exit 2
fi
if [[ ! -d "$task_dir" ]]; then
  echo "verify-reconstruction-task: task dir not found: $task_dir" >&2
  exit 1
fi
case "$verifier" in
  ""|/*|*..*)
    echo "verify-reconstruction-task: unsafe verifier path: $verifier" >&2
    exit 1
    ;;
esac
if [[ ! -f "$task_dir/$verifier" ]]; then
  echo "verify-reconstruction-task: missing task verifier: $task_dir/$verifier" >&2
  exit 1
fi
if [[ ! -f "$candidate" ]]; then
  echo "verify-reconstruction-task: candidate source not found: $candidate" >&2
  exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cp -a "$task_dir"/. "$tmp"/
cp "$candidate" "$tmp/candidate.c"
(cd "$tmp" && bash "$verifier")

if [[ ! -f "$tmp/candidate.bin" ]]; then
  echo "verify-reconstruction-task: verifier did not produce candidate.bin" >&2
  exit 1
fi
mkdir -p "$(dirname "$candidate_output")"
cp "$tmp/candidate.bin" "$candidate_output"
