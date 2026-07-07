#!/usr/bin/env bash
#
# install-jackett-flaresolverr-stack.sh
#
# Installs Jackett (optional) and the hybrid FlareSolverr stack:
#   8193 = stock FlareSolverr (container)
#   8192 = Patchright proxy (headed browser for tough Cloudflare challenges)
#   8191 = hybrid router (what Jackett should use as FlareSolverrUrl)
#
# Idempotent: safe to re-run. Skips steps that are already healthy.
#
# Usage:
#   sudo ./install-jackett-flaresolverr-stack.sh
#   ./install-jackett-flaresolverr-stack.sh --stack-dir "$HOME/flaresolverr-stack" --skip-jackett
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_SRC="${SCRIPT_DIR}/stack"

STACK_DIR="${STACK_DIR:-/opt/flaresolverr-stack}"
JACKETT_DIR="${JACKETT_DIR:-/opt/Jackett}"
JACKETT_USER="${JACKETT_USER:-${SUDO_USER:-$(id -un)}}"
JACKETT_PORT="${JACKETT_PORT:-9117}"
TIMEZONE="${TIMEZONE:-$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)}"
SKIP_JACKETT=0
SKIP_MAINTENANCE=0
SERVICES_ONLY=0
DRY_RUN=0

usage() {
  cat <<'EOF'
Install Jackett + hybrid FlareSolverr stack (Linux and macOS).

Options:
  --stack-dir PATH       Install stack here (default: /opt/flaresolverr-stack)
  --jackett-dir PATH     Install Jackett here (default: /opt/Jackett)
  --jackett-user USER    User to run Jackett (default: invoking user or SUDO_USER)
  --jackett-port PORT    Jackett HTTP port (default: 9117)
  --timezone TZ          Timezone for FlareSolverr container (default: system)
  --skip-jackett         Only install the FlareSolverr stack
  --skip-maintenance     Skip systemd/launchd maintenance timers
  --services-only        Only install/start services (stack files must exist)
  --dry-run              Print actions without changing the system
  -h, --help             Show this help

Environment overrides: STACK_DIR, JACKETT_DIR, JACKETT_USER, TIMEZONE
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stack-dir) STACK_DIR="$2"; shift 2 ;;
    --jackett-dir) JACKETT_DIR="$2"; shift 2 ;;
    --jackett-user) JACKETT_USER="$2"; shift 2 ;;
    --jackett-port) JACKETT_PORT="$2"; shift 2 ;;
    --timezone) TIMEZONE="$2"; shift 2 ;;
    --skip-jackett) SKIP_JACKETT=1; shift ;;
    --skip-maintenance) SKIP_MAINTENANCE=1; shift ;;
    --services-only) SERVICES_ONLY=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "DRY-RUN: $*"
  else
    log "RUN: $*"
    "$@"
  fi
}
run_as_user() {
  local user="$1"; shift
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "DRY-RUN (as ${user}): $*"
  else
    log "RUN (as ${user}): $*"
    sudo -u "$user" -H bash -lc "$*"
  fi
}

OS="$(uname -s)"
case "$OS" in
  Linux|Darwin) ;;
  *) echo "This bash installer supports Linux and macOS only. Use Install-JackettFlareSolverrStack.ps1 on Windows." >&2; exit 1 ;;
esac

if [[ "$OS" == "Linux" && "$STACK_DIR" == /opt/* && "$(id -u)" -ne 0 ]]; then
  echo "Installing under ${STACK_DIR} requires root. Re-run with sudo or set --stack-dir to a user-writable path." >&2
  exit 1
fi

command_exists() { command -v "$1" >/dev/null 2>&1; }

detect_pkg_manager() {
  if command_exists dnf; then echo dnf
  elif command_exists apt-get; then echo apt
  elif command_exists brew; then echo brew
  else echo unknown
  fi
}

find_chromium() {
  local candidates=(
    /usr/bin/chromium-browser
    /usr/bin/chromium
    /usr/bin/google-chrome-stable
    /usr/bin/google-chrome
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    "/Applications/Chromium.app/Contents/MacOS/Chromium"
  )
  for c in "${candidates[@]}"; do
    if [[ -x "$c" ]]; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

container_cmd() {
  if command_exists podman; then echo podman
  elif command_exists docker; then echo docker
  else return 1
  fi
}

install_prerequisites() {
  log "Checking prerequisites..."
  local pm
  pm="$(detect_pkg_manager)"

  if [[ "$pm" == "dnf" ]]; then
    run dnf install -y curl jq nodejs npm podman xvfb chromium 2>/dev/null || \
      run dnf install -y curl jq nodejs npm podman xorg-x11-server-Xvfb chromium 2>/dev/null || true
  elif [[ "$pm" == "apt" ]]; then
    run apt-get update -qq
    run apt-get install -y curl jq nodejs npm podman xvfb chromium-browser 2>/dev/null || \
      run apt-get install -y curl jq nodejs npm docker.io xvfb chromium 2>/dev/null || true
  elif [[ "$pm" == "brew" ]]; then
    run brew install node jq curl podman chromium 2>/dev/null || true
  fi

  if ! command_exists node; then
    echo "Node.js is required but was not installed. Install Node 18+ and re-run." >&2
    exit 1
  fi
  if ! command_exists curl || ! command_exists jq; then
    echo "curl and jq are required." >&2
    exit 1
  fi
  if ! container_cmd >/dev/null; then
    echo "Install podman or docker, then re-run." >&2
    exit 1
  fi
}

install_jackett() {
  [[ "$SKIP_JACKETT" -eq 1 ]] && { log "Skipping Jackett (--skip-jackett)"; return 0; }

  if [[ -x "${JACKETT_DIR}/jackett_launcher.sh" ]]; then
    log "Jackett already present at ${JACKETT_DIR}"
  else
    log "Downloading latest Jackett release..."
    local arch asset tmp
    tmp="$(mktemp -d)"
    case "$OS" in
      Linux) arch="LinuxAMDx64" ;;
      Darwin) arch="macOS" ;;
    esac
    asset="Jackett.Bin.${arch}.tar.gz"
    run curl -fsSL "https://github.com/Jackett/Jackett/releases/latest/download/${asset}" -o "${tmp}/${asset}"
    run mkdir -p "$(dirname "$JACKETT_DIR")"
    run tar -xzf "${tmp}/${asset}" -C "$(dirname "$JACKETT_DIR")"
    run rm -rf "$tmp"
    if [[ "$OS" == "Linux" ]]; then
      run chown -R "${JACKETT_USER}:${JACKETT_USER}" "$JACKETT_DIR"
    fi
  fi

  if [[ "$OS" == "Linux" ]]; then
    local unit="/etc/systemd/system/jackett.service"
    if [[ ! -f "$unit" ]]; then
      log "Creating systemd service for Jackett..."
      run tee "$unit" >/dev/null <<EOF
[Unit]
Description=Jackett Daemon
After=network.target

[Service]
SyslogIdentifier=jackett
Restart=always
RestartSec=5
Type=simple
User=${JACKETT_USER}
Group=${JACKETT_USER}
WorkingDirectory=${JACKETT_DIR}
Environment="DOTNET_EnableDiagnostics=0"
ExecStart=/bin/sh "${JACKETT_DIR}/jackett_launcher.sh"
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF
      run systemctl daemon-reload
      run systemctl enable jackett.service
    fi
    run systemctl restart jackett.service || run systemctl start jackett.service
  elif [[ "$OS" == "Darwin" ]]; then
    local plist="${HOME}/Library/LaunchAgents/com.jackett.daemon.plist"
    if [[ ! -f "$plist" ]]; then
      log "Creating launchd agent for Jackett..."
      run mkdir -p "${HOME}/Library/LaunchAgents"
      run tee "$plist" >/dev/null <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.jackett.daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>${JACKETT_DIR}/jackett_launcher.sh</string>
  </array>
  <key>WorkingDirectory</key><string>${JACKETT_DIR}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
EOF
      run launchctl load "$plist"
    fi
  fi
}

deploy_stack_files() {
  log "Deploying stack files to ${STACK_DIR}..."
  run mkdir -p "${STACK_DIR}/logs" "${STACK_DIR}/scripts"
  run cp -a "${STACK_SRC}/patchright-proxy" "${STACK_DIR}/"
  run cp -a "${STACK_SRC}/hybrid-router" "${STACK_DIR}/"
  run cp -a "${STACK_SRC}/scripts/"* "${STACK_DIR}/scripts/" 2>/dev/null || true
  run chmod +x "${STACK_DIR}/scripts/"*.sh 2>/dev/null || true

  if [[ "$OS" == "Linux" && "$(id -u)" -eq 0 ]]; then
    run chown -R "${JACKETT_USER}:${JACKETT_USER}" "$STACK_DIR"
  fi

  log "Installing Node dependencies (patchright)..."
  run_as_user "$JACKETT_USER" "cd '${STACK_DIR}/patchright-proxy' && npm install --omit=dev"
  run_as_user "$JACKETT_USER" "cd '${STACK_DIR}/patchright-proxy' && npx patchright install chromium 2>/dev/null || true"
}

pull_container_image() {
  local ctr
  ctr="$(container_cmd)"
  log "Pulling FlareSolverr container image (may take a minute)..."
  run_as_user "$JACKETT_USER" "${ctr} pull ghcr.io/flaresolverr/flaresolverr:latest" || \
    run "${ctr} pull ghcr.io/flaresolverr/flaresolverr:latest" || true
}

write_systemd_user_units() {
  pull_container_image
  local user_home chromium xvfb_wrapper node_bin
  user_home="$(getent passwd "$JACKETT_USER" | cut -d: -f6)"
  node_bin="$(command -v node)"
  chromium="$(find_chromium || echo /usr/bin/chromium-browser)"
  xvfb_wrapper="/usr/bin/xvfb-run"

  if [[ ! -x "$xvfb_wrapper" ]]; then
    xvfb_wrapper="$(command -v xvfb-run || echo /usr/bin/xvfb-run)"
  fi

  local unit_dir="${user_home}/.config/systemd/user"
  run mkdir -p "$unit_dir"
  run chown -R "${JACKETT_USER}:${JACKETT_USER}" "${user_home}/.config/systemd"

  local ctr
  ctr="$(container_cmd)"

  run tee "${unit_dir}/flaresolverr.service" >/dev/null <<EOF
[Unit]
Description=FlareSolverr backend (${ctr}, port 8193)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Restart=on-failure
RestartSec=10
ExecStartPre=-${ctr} rm -f flaresolverr
ExecStart=${ctr} run --rm --name flaresolverr \\
  -p 127.0.0.1:8193:8191 \\
  --shm-size=256m \\
  --memory=1g \\
  --cpus=1.0 \\
  -e LOG_LEVEL=info \\
  -e HOST=0.0.0.0 \\
  -e PORT=8191 \\
  -e DISABLE_MEDIA=true \\
  -e TZ=${TIMEZONE} \\
  ghcr.io/flaresolverr/flaresolverr:latest
ExecStop=${ctr} stop -t 10 flaresolverr

[Install]
WantedBy=default.target
EOF

  local patchright_exec
  if [[ "$OS" == "Linux" && -x "$xvfb_wrapper" ]]; then
    patchright_exec="${xvfb_wrapper} -a -s \"-screen 0 1280x720x24\" ${node_bin} server.js"
  else
    patchright_exec="${node_bin} server.js"
  fi

  run tee "${unit_dir}/patchright-proxy.service" >/dev/null <<EOF
[Unit]
Description=Patchright FlareSolverr-compatible proxy (port 8192)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${STACK_DIR}/patchright-proxy
Environment=PORT=8192
Environment=HOST=127.0.0.1
Environment=HEADLESS=false
Environment=EXECUTABLE_PATH=${chromium}
Environment=MAX_TIMEOUT_MS=120000
Environment=SESSION_TTL_MS=600000
Restart=on-failure
RestartSec=15
ExecStart=/bin/bash -lc '${patchright_exec}'

[Install]
WantedBy=default.target
EOF

  run tee "${unit_dir}/hybrid-router.service" >/dev/null <<EOF
[Unit]
Description=Hybrid FlareSolverr router for Jackett (port 8191)
After=network-online.target flaresolverr.service patchright-proxy.service
Wants=network-online.target
Requires=flaresolverr.service patchright-proxy.service

[Service]
Type=simple
WorkingDirectory=${STACK_DIR}/hybrid-router
Environment=PORT=8191
Environment=HOST=127.0.0.1
Environment=FLARESOLVERR_URL=http://127.0.0.1:8193/v1
Environment=PATCHRIGHT_URL=http://127.0.0.1:8192/v1
Environment=UPSTREAM_TIMEOUT_MS=130000
Restart=on-failure
RestartSec=10
ExecStart=${node_bin} router.js

[Install]
WantedBy=default.target
EOF

  if [[ "$SKIP_MAINTENANCE" -eq 0 ]]; then
    run tee "${unit_dir}/flaresolverr-maintain.service" >/dev/null <<EOF
[Unit]
Description=FlareSolverr stack health maintenance

[Service]
Type=oneshot
Environment=STACK_DIR=${STACK_DIR}
ExecStart=${STACK_DIR}/scripts/maintain-stack.sh
EOF

    run tee "${unit_dir}/flaresolverr-maintain.timer" >/dev/null <<EOF
[Unit]
Description=Periodic FlareSolverr stack health check

[Timer]
OnBootSec=3min
OnUnitActiveSec=10min
AccuracySec=1min
Persistent=true

[Install]
WantedBy=timers.target
EOF

    run tee "${unit_dir}/flaresolverr-triage.service" >/dev/null <<EOF
[Unit]
Description=FlareSolverr CF probe triage agent

[Service]
Type=oneshot
Environment=STACK_DIR=${STACK_DIR}
ExecStart=${STACK_DIR}/scripts/agent-triage.sh
EOF

    run tee "${unit_dir}/flaresolverr-triage.timer" >/dev/null <<EOF
[Unit]
Description=Periodic Cloudflare indexer probe via hybrid FlareSolverr

[Timer]
OnBootSec=8min
OnUnitActiveSec=45min
AccuracySec=5min
Persistent=true

[Install]
WantedBy=timers.target
EOF
  fi

  run chown -R "${JACKETT_USER}:${JACKETT_USER}" "${user_home}/.config/systemd"

  log "Enabling user systemd units and linger..."
  run loginctl enable-linger "$JACKETT_USER" 2>/dev/null || true
  run_as_user "$JACKETT_USER" "systemctl --user daemon-reload"
  run_as_user "$JACKETT_USER" "systemctl --user enable --now flaresolverr.service patchright-proxy.service hybrid-router.service"
  if [[ "$SKIP_MAINTENANCE" -eq 0 ]]; then
    run_as_user "$JACKETT_USER" "systemctl --user enable --now flaresolverr-maintain.timer flaresolverr-triage.timer"
  fi
}

write_launchd_agents() {
  pull_container_image
  local user_home chromium node_bin ctr
  user_home="$(eval echo "~${JACKETT_USER}")"
  node_bin="$(command -v node)"
  chromium="$(find_chromium || echo "")"
  ctr="$(container_cmd)"

  local agents="${user_home}/Library/LaunchAgents"
  run mkdir -p "$agents"

  run tee "${agents}/com.flaresolverr.backend.plist" >/dev/null <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.flaresolverr.backend</string>
  <key>ProgramArguments</key>
  <array>
    <string>${ctr}</string><string>run</string><string>--rm</string><string>--name</string><string>flaresolverr</string>
    <string>-p</string><string>127.0.0.1:8193:8191</string>
    <string>--shm-size=256m</string><string>--memory=1g</string><string>--cpus=1.0</string>
    <string>-e</string><string>LOG_LEVEL=info</string>
    <string>-e</string><string>HOST=0.0.0.0</string>
    <string>-e</string><string>PORT=8191</string>
    <string>-e</string><string>DISABLE_MEDIA=true</string>
    <string>-e</string><string>TZ=${TIMEZONE}</string>
    <string>ghcr.io/flaresolverr/flaresolverr:latest</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
EOF

  run tee "${agents}/com.flaresolverr.patchright.plist" >/dev/null <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.flaresolverr.patchright</string>
  <key>WorkingDirectory</key><string>${STACK_DIR}/patchright-proxy</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PORT</key><string>8192</string>
    <key>HOST</key><string>127.0.0.1</string>
    <key>HEADLESS</key><string>false</string>
    <key>EXECUTABLE_PATH</key><string>${chromium}</string>
    <key>MAX_TIMEOUT_MS</key><string>120000</string>
  </dict>
  <key>ProgramArguments</key>
  <array><string>${node_bin}</string><string>server.js</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
EOF

  run tee "${agents}/com.flaresolverr.hybrid.plist" >/dev/null <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.flaresolverr.hybrid</string>
  <key>WorkingDirectory</key><string>${STACK_DIR}/hybrid-router</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PORT</key><string>8191</string>
    <key>FLARESOLVERR_URL</key><string>http://127.0.0.1:8193/v1</string>
    <key>PATCHRIGHT_URL</key><string>http://127.0.0.1:8192/v1</string>
  </dict>
  <key>ProgramArguments</key>
  <array><string>${node_bin}</string><string>router.js</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
EOF

  run_as_user "$JACKETT_USER" "launchctl load -w '${agents}/com.flaresolverr.backend.plist' 2>/dev/null || launchctl bootstrap gui/\$(id -u) '${agents}/com.flaresolverr.backend.plist'"
  run_as_user "$JACKETT_USER" "launchctl load -w '${agents}/com.flaresolverr.patchright.plist' 2>/dev/null || launchctl bootstrap gui/\$(id -u) '${agents}/com.flaresolverr.patchright.plist'"
  run_as_user "$JACKETT_USER" "launchctl load -w '${agents}/com.flaresolverr.hybrid.plist' 2>/dev/null || launchctl bootstrap gui/\$(id -u) '${agents}/com.flaresolverr.hybrid.plist'"
}

configure_jackett_flaresolverr() {
  [[ "$SKIP_JACKETT" -eq 1 ]] && return 0

  local cfg
  if [[ "$OS" == "Darwin" ]]; then
    cfg="${HOME}/.config/Jackett/ServerConfig.json"
  else
    cfg="$(getent passwd "$JACKETT_USER" | cut -d: -f6)/.config/Jackett/ServerConfig.json"
  fi

  if [[ ! -f "$cfg" ]]; then
    log "Jackett config not found yet at ${cfg}; start Jackett once, then re-run or set FlareSolverrUrl manually to http://127.0.0.1:8191"
    return 0
  fi

  log "Updating Jackett FlareSolverrUrl -> http://127.0.0.1:8191"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
  fi

  local tmp
  tmp="$(mktemp)"
  jq '.FlareSolverrUrl = "http://127.0.0.1:8191" | .FlareSolverrMaxTimeout = 120000' "$cfg" > "$tmp"
  mv "$tmp" "$cfg"
  chown "${JACKETT_USER}:${JACKETT_USER}" "$cfg" 2>/dev/null || true

  if [[ "$OS" == "Linux" ]]; then
    systemctl restart jackett.service 2>/dev/null || true
  fi
}

wait_for_health() {
  log "Waiting for hybrid stack to respond..."
  local i body
  for i in $(seq 1 30); do
    body='{"cmd":"sessions.list"}'
    if curl -sf --max-time 5 -X POST http://127.0.0.1:8191/v1 \
      -H 'Content-Type: application/json' -d "$body" | jq -e '.status == "ok"' >/dev/null 2>&1; then
      log "Hybrid router is healthy on http://127.0.0.1:8191"
      return 0
    fi
    sleep 2
  done
  log "WARN: hybrid router did not become healthy within 60s. Check logs with: systemctl --user status hybrid-router.service"
  return 1
}

main() {
  log "=== Jackett + hybrid FlareSolverr installer (bash) ==="
  log "OS=${OS} stack=${STACK_DIR} jackett=${JACKETT_DIR} user=${JACKETT_USER}"

  if [[ "$SERVICES_ONLY" -eq 0 ]]; then
    install_prerequisites
    deploy_stack_files
    install_jackett
  else
    log "Services-only mode: skipping prerequisites, file deploy, and Jackett install"
  fi

  if [[ "$OS" == "Linux" ]] && command_exists systemctl; then
    write_systemd_user_units
  elif [[ "$OS" == "Darwin" ]]; then
    write_launchd_agents
  fi

  configure_jackett_flaresolverr
  wait_for_health || true

  log ""
  log "Done."
  log "  Jackett UI:        http://127.0.0.1:${JACKETT_PORT}"
  log "  FlareSolverr (use): http://127.0.0.1:8191  (set as FlareSolverrUrl in Jackett)"
  log "  Backend FlareSolverr: http://127.0.0.1:8193"
  log "  Patchright proxy:     http://127.0.0.1:8192"
  log "Re-run this script anytime to repair or upgrade the stack."
}

main "$@"
