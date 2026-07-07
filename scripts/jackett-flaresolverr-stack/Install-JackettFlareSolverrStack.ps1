#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Install Jackett and the hybrid FlareSolverr stack on Windows, Linux, or macOS.

.DESCRIPTION
  Sets up:
    8193 - stock FlareSolverr (Docker/Podman container)
    8192 - Patchright proxy (FlareSolverr-compatible API, headed browser)
    8191 - hybrid router (point Jackett FlareSolverrUrl here)

  Idempotent: safe to re-run.

.PARAMETER StackDir
  Where to install stack files. Default: /opt/flaresolverr-stack (Linux),
  /usr/local/flaresolverr-stack (macOS), $env:ProgramData\flaresolverr-stack (Windows).

.PARAMETER JackettDir
  Jackett install directory.

.PARAMETER SkipJackett
  Only install the FlareSolverr stack.

.PARAMETER SkipMaintenance
  Skip scheduled maintenance tasks / systemd timers.

.EXAMPLE
  pwsh ./Install-JackettFlareSolverrStack.ps1

.EXAMPLE
  sudo pwsh ./Install-JackettFlareSolverrStack.ps1 -StackDir /opt/flaresolverr-stack
#>
#Requires -Version 7.0

[CmdletBinding()]
param(
  [string] $StackDir = '',
  [string] $JackettDir = '',
  [string] $JackettUser = '',
  [int] $JackettPort = 9117,
  [string] $TimeZone = '',
  [switch] $SkipJackett,
  [switch] $SkipMaintenance,
  [switch] $DryRun
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$StackSrc = Join-Path $ScriptDir 'stack'

function Write-Step([string]$Message) {
  $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
  Write-Host "[$ts] $Message"
}

function Invoke-Step([scriptblock]$Action, [string]$Label) {
  if ($DryRun) {
    Write-Step "DRY-RUN: $Label"
    return
  }
  Write-Step "RUN: $Label"
  & $Action
}

function Test-CommandExists([string]$Name) {
  return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-PlatformName {
  if ($IsWindows) { return 'Windows' }
  if ($IsMacOS) { return 'macOS' }
  if ($IsLinux) { return 'Linux' }
  return 'Unknown'
}

function Get-DefaultStackDir {
  switch (Get-PlatformName) {
    'Windows' { return Join-Path $env:ProgramData 'flaresolverr-stack' }
    'macOS' { return '/usr/local/flaresolverr-stack' }
    default { return '/opt/flaresolverr-stack' }
  }
}

function Get-DefaultJackettDir {
  switch (Get-PlatformName) {
    'Windows' { return Join-Path $env:ProgramData 'Jackett' }
    'macOS' { return '/Applications/Jackett' }
    default { return '/opt/Jackett' }
  }
}

function Get-JackettConfigPath {
  switch (Get-PlatformName) {
    'Windows' { return Join-Path (Join-Path $env:ProgramData 'Jackett') 'ServerConfig.json' }
    default {
      $home = if ($JackettUser) {
        (Get-EntUserHome $JackettUser)
      } else {
        $env:HOME
      }
      return Join-Path (Join-Path $home '.config/Jackett') 'ServerConfig.json'
    }
  }
}

function Get-EntUserHome([string]$User) {
  if ($IsLinux) {
    $line = getent passwd $User 2>$null
    if ($line) { return ($line -split ':')[5] }
  }
  return $env:HOME
}

function Get-ContainerCommand {
  if (Test-CommandExists 'podman') { return 'podman' }
  if (Test-CommandExists 'docker') { return 'docker' }
  throw 'Install Podman or Docker, then re-run this script.'
}

function Find-ChromiumPath {
  $candidates = @(
    '/usr/bin/chromium-browser',
    '/usr/bin/chromium',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/google-chrome',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    (Join-Path ${env:ProgramFiles} 'Google/Chrome/Application/chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Google/Chrome/Application/chrome.exe'),
    (Join-Path $env:LOCALAPPDATA 'Google/Chrome/Application/chrome.exe')
  )
  foreach ($c in $candidates) {
    if ($c -and (Test-Path -LiteralPath $c)) { return $c }
  }
  return $null
}

function Install-Prerequisites {
  Write-Step 'Checking prerequisites...'

  if (-not (Test-CommandExists 'node')) {
    throw 'Node.js 18+ is required. Install from https://nodejs.org/ or your package manager.'
  }
  if (-not (Test-CommandExists 'curl')) {
    throw 'curl is required.'
  }

  if ($IsLinux -or $IsMacOS) {
    if (-not (Test-CommandExists 'jq')) {
      Write-Step 'Installing jq via package manager if possible...'
      if (Test-CommandExists 'dnf') { Invoke-Step { dnf install -y jq } 'dnf install jq' }
      elseif (Test-CommandExists 'apt-get') { Invoke-Step { apt-get update -qq; apt-get install -y jq } 'apt install jq' }
      elseif (Test-CommandExists 'brew') { Invoke-Step { brew install jq } 'brew install jq' }
    }
    if ($IsLinux) {
      if (Test-CommandExists 'dnf') {
        try { Invoke-Step { dnf install -y curl jq nodejs npm podman xvfb chromium } 'dnf packages' } catch { Write-Step 'Note: some dnf packages may already be installed' }
      } elseif (Test-CommandExists 'apt-get') {
        try { Invoke-Step { apt-get update -qq; apt-get install -y curl jq nodejs npm podman xvfb chromium-browser } 'apt packages' } catch { Write-Step 'Note: some apt packages may already be installed' }
      }
    }
  }

  $null = Get-ContainerCommand
}

function Copy-StackFiles {
  Write-Step "Deploying stack to $StackDir"
  Invoke-Step {
    New-Item -ItemType Directory -Force -Path $StackDir, (Join-Path $StackDir 'logs'), (Join-Path $StackDir 'scripts') | Out-Null
    Copy-Item -Recurse -Force (Join-Path $StackSrc 'patchright-proxy') (Join-Path $StackDir 'patchright-proxy')
    Copy-Item -Recurse -Force (Join-Path $StackSrc 'hybrid-router') (Join-Path $StackDir 'hybrid-router')
    Copy-Item -Force (Join-Path $StackSrc 'scripts/*') (Join-Path $StackDir 'scripts/')
    if ($IsLinux -or $IsMacOS) {
      Get-ChildItem (Join-Path $StackDir 'scripts/*.sh') | ForEach-Object { chmod +x $_.FullName }
    }
  } 'copy stack files'

  Write-Step 'Installing npm dependencies (patchright)...'
  Invoke-Step {
    Push-Location (Join-Path $StackDir 'patchright-proxy')
    npm install --omit=dev
    npx patchright install chromium 2>$null
    Pop-Location
  } 'npm install patchright'
}

function Install-JackettApp {
  if ($SkipJackett) {
    Write-Step 'Skipping Jackett (-SkipJackett)'
    return
  }

  $launcher = Join-Path $JackettDir 'jackett_launcher.sh'
  $exe = Join-Path $JackettDir 'JackettConsole.exe'

  if ((Test-Path $launcher) -or (Test-Path $exe)) {
    Write-Step "Jackett already present at $JackettDir"
    return
  }

  Write-Step 'Downloading latest Jackett release...'
  $tmp = New-TemporaryFile | ForEach-Object { Remove-Item $_; New-Item -ItemType Directory -Path $_.FullName }

  switch (Get-PlatformName) {
    'Windows' {
      $asset = 'Jackett.Installer.Windows.exe'
      $url = "https://github.com/Jackett/Jackett/releases/latest/download/$asset"
      $installer = Join-Path $tmp $asset
      Invoke-Step { curl -fsSL $url -o $installer } "download $asset"
      Invoke-Step {
        Start-Process -FilePath $installer -ArgumentList '/SILENT' -Wait
      } 'silent Jackett install'
    }
    'macOS' {
      $asset = 'Jackett.Bin.macOS.tar.gz'
      $url = "https://github.com/Jackett/Jackett/releases/latest/download/$asset"
      $tar = Join-Path $tmp $asset
      Invoke-Step { curl -fsSL $url -o $tar } "download $asset"
      Invoke-Step {
        New-Item -ItemType Directory -Force -Path (Split-Path $JackettDir) | Out-Null
        tar -xzf $tar -C (Split-Path $JackettDir)
      } 'extract Jackett'
    }
    default {
      $asset = 'Jackett.Bin.LinuxAMDx64.tar.gz'
      $url = "https://github.com/Jackett/Jackett/releases/latest/download/$asset"
      $tar = Join-Path $tmp $asset
      Invoke-Step { curl -fsSL $url -o $tar } "download $asset"
      Invoke-Step {
        New-Item -ItemType Directory -Force -Path (Split-Path $JackettDir) | Out-Null
        tar -xzf $tar -C (Split-Path $JackettDir)
        if ($JackettUser -and (Get-Command id -ErrorAction SilentlyContinue)) {
          chown -R "${JackettUser}:${JackettUser}" $JackettDir 2>$null
        }
      } 'extract Jackett'
    }
  }
}

function Set-JackettServiceLinux {
  if ($SkipJackett -or -not $IsLinux) { return }

  $unit = '/etc/systemd/system/jackett.service'
  if (-not (Test-Path $unit)) {
    $content = @"
[Unit]
Description=Jackett Daemon
After=network.target

[Service]
SyslogIdentifier=jackett
Restart=always
RestartSec=5
Type=simple
User=$JackettUser
Group=$JackettUser
WorkingDirectory=$JackettDir
Environment="DOTNET_EnableDiagnostics=0"
ExecStart=/bin/sh "$JackettDir/jackett_launcher.sh"
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
"@
    Invoke-Step {
      $content | Out-File -FilePath $unit -Encoding utf8
      systemctl daemon-reload
      systemctl enable jackett.service
      systemctl restart jackett.service
    } 'systemd jackett.service'
  } else {
    Invoke-Step { systemctl restart jackett.service } 'restart jackett'
  }
}

function Install-SystemdUserStack {
  if (-not $IsLinux) { return }
  if (-not (Test-CommandExists 'systemctl')) { return }

  $user = if ($JackettUser) { $JackettUser } else { $env:USER }
  $userHome = Get-EntUserHome $user
  $unitDir = Join-Path $userHome '.config/systemd/user'
  $node = (Get-Command node).Source
  $chromium = Find-ChromiumPath
  if (-not $chromium) { $chromium = '/usr/bin/chromium-browser' }
  $ctr = Get-ContainerCommand
  $xvfb = if (Test-CommandExists 'xvfb-run') { (Get-Command xvfb-run).Source } else { $null }

  Invoke-Step {
    New-Item -ItemType Directory -Force -Path $unitDir | Out-Null
  } 'mkdir systemd user units'

  $flaresolverrUnit = @"
[Unit]
Description=FlareSolverr backend ($ctr, port 8193)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Restart=on-failure
RestartSec=10
ExecStartPre=-$ctr rm -f flaresolverr
ExecStart=$ctr run --rm --name flaresolverr -p 127.0.0.1:8193:8191 --shm-size=256m --memory=1g --cpus=1.0 -e LOG_LEVEL=info -e HOST=0.0.0.0 -e PORT=8191 -e DISABLE_MEDIA=true -e TZ=$TimeZone ghcr.io/flaresolverr/flaresolverr:latest
ExecStop=$ctr stop -t 10 flaresolverr

[Install]
WantedBy=default.target
"@

  if ($xvfb) {
    $patchStart = "$xvfb -a -s `"-screen 0 1280x720x24`" $node server.js"
  } else {
    $patchStart = "$node server.js"
  }

  $patchrightUnit = @"
[Unit]
Description=Patchright FlareSolverr-compatible proxy (port 8192)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$StackDir/patchright-proxy
Environment=PORT=8192
Environment=HOST=127.0.0.1
Environment=HEADLESS=false
Environment=EXECUTABLE_PATH=$chromium
Environment=MAX_TIMEOUT_MS=120000
Environment=SESSION_TTL_MS=600000
Restart=on-failure
RestartSec=15
ExecStart=/bin/bash -lc '$patchStart'

[Install]
WantedBy=default.target
"@

  $hybridUnit = @"
[Unit]
Description=Hybrid FlareSolverr router for Jackett (port 8191)
After=network-online.target flaresolverr.service patchright-proxy.service
Wants=network-online.target
Requires=flaresolverr.service patchright-proxy.service

[Service]
Type=simple
WorkingDirectory=$StackDir/hybrid-router
Environment=PORT=8191
Environment=HOST=127.0.0.1
Environment=FLARESOLVERR_URL=http://127.0.0.1:8193/v1
Environment=PATCHRIGHT_URL=http://127.0.0.1:8192/v1
Environment=UPSTREAM_TIMEOUT_MS=130000
Restart=on-failure
RestartSec=10
ExecStart=$node router.js

[Install]
WantedBy=default.target
"@

  Invoke-Step {
    & $ctr pull ghcr.io/flaresolverr/flaresolverr:latest
  } 'pull flaresolverr image'

  Invoke-Step {
    $flaresolverrUnit | Out-File (Join-Path $unitDir 'flaresolverr.service') -Encoding utf8
    $patchrightUnit | Out-File (Join-Path $unitDir 'patchright-proxy.service') -Encoding utf8
    $hybridUnit | Out-File (Join-Path $unitDir 'hybrid-router.service') -Encoding utf8

    if (-not $SkipMaintenance) {
      @"
[Unit]
Description=FlareSolverr stack health maintenance
[Service]
Type=oneshot
Environment=STACK_DIR=$StackDir
ExecStart=$StackDir/scripts/maintain-stack.sh
"@ | Out-File (Join-Path $unitDir 'flaresolverr-maintain.service') -Encoding utf8

      @"
[Unit]
Description=Periodic FlareSolverr stack health check
[Timer]
OnBootSec=3min
OnUnitActiveSec=10min
AccuracySec=1min
Persistent=true
[Install]
WantedBy=timers.target
"@ | Out-File (Join-Path $unitDir 'flaresolverr-maintain.timer') -Encoding utf8

      @"
[Unit]
Description=FlareSolverr CF probe triage agent
[Service]
Type=oneshot
Environment=STACK_DIR=$StackDir
ExecStart=$StackDir/scripts/agent-triage.sh
"@ | Out-File (Join-Path $unitDir 'flaresolverr-triage.service') -Encoding utf8

      @"
[Unit]
Description=Periodic Cloudflare indexer probe
[Timer]
OnBootSec=8min
OnUnitActiveSec=45min
AccuracySec=5min
Persistent=true
[Install]
WantedBy=timers.target
"@ | Out-File (Join-Path $unitDir 'flaresolverr-triage.timer') -Encoding utf8
    }

    loginctl enable-linger $user 2>$null
    sudo -u $user systemctl --user daemon-reload
    sudo -u $user systemctl --user enable --now flaresolverr.service patchright-proxy.service hybrid-router.service
    if (-not $SkipMaintenance) {
      sudo -u $user systemctl --user enable --now flaresolverr-maintain.timer flaresolverr-triage.timer
    }
  } 'enable systemd user stack'
}

function Install-WindowsStack {
  if (-not $IsWindows) { return }

  $ctr = Get-ContainerCommand
  $node = (Get-Command node).Source
  $chromium = Find-ChromiumPath

  Write-Step 'Pulling FlareSolverr container image...'
  Invoke-Step { & $ctr pull ghcr.io/flaresolverr/flaresolverr:latest } 'pull flaresolverr image'

  Write-Step 'Starting FlareSolverr container (port 8193)...'
  Invoke-Step {
    & $ctr rm -f flaresolverr 2>$null
    & $ctr run -d --name flaresolverr --restart unless-stopped `
      -p 127.0.0.1:8193:8191 `
      --shm-size=256m --memory=1g --cpus=1.0 `
      -e LOG_LEVEL=info -e HOST=0.0.0.0 -e PORT=8191 -e DISABLE_MEDIA=true -e "TZ=$TimeZone" `
      ghcr.io/flaresolverr/flaresolverr:latest | Out-Null
  } 'docker flaresolverr'

  $patchrightPs1 = Join-Path $StackDir 'scripts/run-patchright-proxy.ps1'
  $hybridPs1 = Join-Path $StackDir 'scripts/run-hybrid-router.ps1'

  $patchrightScript = @"
`$env:PORT='8192'
`$env:HOST='127.0.0.1'
`$env:HEADLESS='false'
`$env:MAX_TIMEOUT_MS='120000'
`$env:SESSION_TTL_MS='600000'
$(if ($chromium) { "`$env:EXECUTABLE_PATH='$($chromium -replace "'","''")'" })
Set-Location '$($StackDir -replace "'","''")/patchright-proxy'
while (`$true) {
  try { & '$($node -replace "'","''")' server.js; exit `$LASTEXITCODE } catch {}
  Start-Sleep -Seconds 5
}
"@
  $hybridScript = @"
`$env:PORT='8191'
`$env:HOST='127.0.0.1'
`$env:FLARESOLVERR_URL='http://127.0.0.1:8193/v1'
`$env:PATCHRIGHT_URL='http://127.0.0.1:8192/v1'
`$env:UPSTREAM_TIMEOUT_MS='130000'
Set-Location '$($StackDir -replace "'","''")/hybrid-router'
while (`$true) {
  try { & '$($node -replace "'","''")' router.js; exit `$LASTEXITCODE } catch {}
  Start-Sleep -Seconds 5
}
"@

  Invoke-Step {
    New-Item -ItemType Directory -Force -Path (Join-Path $StackDir 'scripts') | Out-Null
    $patchrightScript | Out-File $patchrightPs1 -Encoding utf8
    $hybridScript | Out-File $hybridPs1 -Encoding utf8
  } 'write Windows runner scripts'

  function Register-StackTask([string]$Name, [string]$ScriptPath) {
    $action = New-ScheduledTaskAction -Execute 'pwsh.exe' -Argument "-NoProfile -WindowStyle Hidden -File `"$ScriptPath`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $Name
  }

  Invoke-Step {
    Register-StackTask 'FlareSolverr-Patchright-Proxy' $patchrightPs1
    Register-StackTask 'FlareSolverr-Hybrid-Router' $hybridPs1
  } 'register scheduled tasks'

  if (-not $SkipMaintenance) {
    $maintainPs1 = Join-Path $StackDir 'scripts/maintain-stack.ps1'
    Invoke-Step {
      $action = New-ScheduledTaskAction -Execute 'pwsh.exe' -Argument "-NoProfile -File `"$maintainPs1`" -StackDir `"$StackDir`""
      $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration ([TimeSpan]::MaxValue)
      Register-ScheduledTask -TaskName 'FlareSolverr-Stack-Maintain' -Action $action -Trigger $trigger -Force | Out-Null
    } 'maintenance scheduled task'
  }
}

function Install-MacOSStack {
  if (-not $IsMacOS) { return }
  $bashScript = Join-Path $ScriptDir 'install-jackett-flaresolverr-stack.sh'
  if (-not (Test-Path $bashScript)) { return }

  Write-Step 'Configuring macOS launchd agents via bash helper...'
  $bashArgs = @(
    $bashScript,
    '--stack-dir', $StackDir,
    '--jackett-dir', $JackettDir,
    '--jackett-user', $JackettUser,
    '--timezone', $TimeZone,
    '--services-only'
  )
  if ($SkipMaintenance) { $bashArgs += '--skip-maintenance' }

  Invoke-Step { & bash @bashArgs } 'bash stack services'
}

function Update-JackettConfig {
  if ($SkipJackett) { return }

  $cfg = Get-JackettConfigPath
  if (-not (Test-Path $cfg)) {
    Write-Step "Jackett config not found at $cfg — start Jackett once, then set FlareSolverrUrl to http://127.0.0.1:8191"
    return
  }

  Write-Step 'Updating Jackett FlareSolverrUrl -> http://127.0.0.1:8191'
  Invoke-Step {
    if ($IsLinux -or $IsMacOS) {
      $tmp = New-TemporaryFile
      jq '.FlareSolverrUrl = "http://127.0.0.1:8191" | .FlareSolverrMaxTimeout = 120000' $cfg > $tmp
      Move-Item -Force $tmp $cfg
    } else {
      $json = Get-Content $cfg -Raw | ConvertFrom-Json
      $json.FlareSolverrUrl = 'http://127.0.0.1:8191'
      $json.FlareSolverrMaxTimeout = 120000
      $json | ConvertTo-Json -Depth 20 | Set-Content $cfg -Encoding utf8
    }
    if ($IsLinux) { systemctl restart jackett.service 2>$null }
  } 'patch ServerConfig.json'
}

function Wait-ForHybridHealth {
  Write-Step 'Waiting for hybrid router on http://127.0.0.1:8191 ...'
  for ($i = 1; $i -le 30; $i++) {
    try {
      $r = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8191/v1' -ContentType 'application/json' -Body '{"cmd":"sessions.list"}' -TimeoutSec 5
      if ($r.status -eq 'ok') {
        Write-Step 'Hybrid router is healthy.'
        return
      }
    } catch {}
    Start-Sleep -Seconds 2
  }
  Write-Step 'WARN: hybrid router not healthy yet. Check service logs.'
}

# --- main ---
if (-not $StackDir) { $StackDir = Get-DefaultStackDir }
if (-not $JackettDir) { $JackettDir = Get-DefaultJackettDir }
if (-not $JackettUser) {
  $JackettUser = if ($IsWindows) { $env:USERNAME } else { if ($env:SUDO_USER) { $env:SUDO_USER } else { $env:USER } }
}
if (-not $TimeZone) {
  $TimeZone = if ($IsWindows) { [TimeZoneInfo]::Local.Id } else { (timedatectl show -p Timezone --value 2>$null) }
  if (-not $TimeZone) { $TimeZone = 'UTC' }
}

Write-Step "=== Jackett + hybrid FlareSolverr installer (PowerShell) ==="
Write-Step "Platform: $(Get-PlatformName)  Stack: $StackDir  Jackett: $JackettDir  User: $JackettUser"

if ($IsLinux -and $StackDir -like '/opt/*' -and (id -u) -ne 0) {
  throw "Installing under $StackDir requires root. Re-run with sudo or choose a user-writable -StackDir."
}

Install-Prerequisites
Copy-StackFiles
Install-JackettApp
Set-JackettServiceLinux
Install-SystemdUserStack
Install-WindowsStack
Install-MacOSStack
Update-JackettConfig
Wait-ForHybridHealth

Write-Step ''
Write-Step 'Done.'
Write-Step "  Jackett UI:           http://127.0.0.1:$JackettPort"
Write-Step '  FlareSolverr (Jackett): http://127.0.0.1:8191'
Write-Step '  Backend FlareSolverr:   http://127.0.0.1:8193'
Write-Step '  Patchright proxy:       http://127.0.0.1:8192'
Write-Step 'Re-run this script anytime to repair or upgrade the stack.'
