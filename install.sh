#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install_dir="${HOME}/.local/bin"
metadata_url=""
version=""
dry_run=0

usage() {
  cat <<'EOF'
Usage: install.sh [--dry-run] [--install-dir DIR] [--metadata-url URL] [--version TAG]

Installs the `decomp` CLI from a release artifact. Heavyweight reverse-engineering
tools are never installed automatically; the script reports missing optional tools
after placing the binary.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) dry_run=1; shift ;;
    --install-dir) install_dir="${2:?missing install dir}"; shift 2 ;;
    --metadata-url) metadata_url="${2:?missing metadata url}"; shift 2 ;;
    --version) version="${2:?missing version}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "install.sh: unexpected argument: $1" >&2; exit 2 ;;
  esac
done

derive_repo_slug() {
  local remote
  remote="$(git -C "$ROOT" config --get remote.origin.url 2>/dev/null || true)"
  if [[ "$remote" =~ github\.com[:/]([^/]+/[^/.]+)(\.git)?$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

derive_metadata_url() {
  if [[ -n "$metadata_url" ]]; then
    printf '%s\n' "$metadata_url"
    return 0
  fi

  local slug
  slug="$(derive_repo_slug)" || return 1
  if [[ -n "$version" ]]; then
    printf 'https://github.com/%s/releases/download/%s/decomp-release.json\n' "$slug" "$version"
  else
    printf 'https://github.com/%s/releases/latest/download/decomp-release.json\n' "$slug"
  fi
}

platform() {
  case "$(uname -s)" in
    Linux) printf 'linux\n' ;;
    Darwin) printf 'macos\n' ;;
    *) printf 'unsupported\n' ;;
  esac
}

arch() {
  case "$(uname -m)" in
    x86_64|amd64) printf 'x86_64\n' ;;
    arm64|aarch64) printf 'aarch64\n' ;;
    *) printf 'unknown\n' ;;
  esac
}

optional_tool_status() {
  local name="$1"
  if command -v "$name" >/dev/null 2>&1; then
    printf 'present'
  else
    printf 'missing'
  fi
}

resolved_metadata_url="$(derive_metadata_url || true)"
resolved_platform="$(platform)"
resolved_arch="$(arch)"

if [[ "$dry_run" -eq 1 ]]; then
  printf 'dry-run: would install decomp\n'
  printf 'install-dir: %s\n' "$install_dir"
  printf 'metadata-url: %s\n' "${resolved_metadata_url:-unset}"
  printf 'platform: %s\n' "$resolved_platform"
  printf 'arch: %s\n' "$resolved_arch"
  exit 0
fi

if [[ -z "$resolved_metadata_url" ]]; then
  echo "install.sh: unable to derive release metadata URL; pass --metadata-url" >&2
  exit 1
fi

if [[ "$resolved_platform" == "unsupported" || "$resolved_arch" == "unknown" ]]; then
  echo "install.sh: unsupported platform or architecture ($resolved_platform/$resolved_arch)" >&2
  exit 1
fi

metadata_json="$(curl -fsSL "$resolved_metadata_url")"
asset_url="$(jq -r --arg os "$resolved_platform" --arg arch "$resolved_arch" '
  .assets[] | select(.os == $os and .arch == $arch) | .url
' <<<"$metadata_json" | head -n 1)"

if [[ -z "$asset_url" ]]; then
  echo "install.sh: no release asset found for $resolved_platform/$resolved_arch" >&2
  exit 1
fi

mkdir -p "$install_dir"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
tmpbin="$tmpdir/decomp"

curl -fsSL "$asset_url" -o "$tmpbin"
chmod +x "$tmpbin"
mv "$tmpbin" "$install_dir/decomp"

printf 'installed: %s\n' "$install_dir/decomp"
printf 'optional-tools: objdiff=%s analyzeHeadless=%s\n' \
  "$(optional_tool_status objdiff)" \
  "$(optional_tool_status analyzeHeadless)"
