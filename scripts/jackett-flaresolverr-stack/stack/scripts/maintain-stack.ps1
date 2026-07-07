param(
  [string] $StackDir = $env:STACK_DIR
)

if (-not $StackDir) {
  $StackDir = if ($IsWindows) {
    Join-Path $env:ProgramData 'flaresolverr-stack'
  } else {
    '/opt/flaresolverr-stack'
  }
}

$ErrorActionPreference = 'Continue'

function Test-FlareSolverrEndpoint {
  param([string] $BaseUrl, [string] $Name)
  try {
    $response = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1" -ContentType 'application/json' -Body '{"cmd":"sessions.list"}' -TimeoutSec 15
    if ($response.status -eq 'ok') {
      Write-Host "OK $Name"
      return $true
    }
  } catch {}
  Write-Host "FAIL $Name"
  return $false
}

$failed = $false
if (-not (Test-FlareSolverrEndpoint 'http://127.0.0.1:8193' 'flaresolverr-backend')) {
  $failed = $true
  if (Get-Command docker -ErrorAction SilentlyContinue) { docker restart flaresolverr 2>$null }
  elseif (Get-Command podman -ErrorAction SilentlyContinue) { podman restart flaresolverr 2>$null }
}
if (-not (Test-FlareSolverrEndpoint 'http://127.0.0.1:8192' 'patchright-proxy')) {
  $failed = $true
  Start-ScheduledTask -TaskName 'FlareSolverr-Patchright-Proxy' -ErrorAction SilentlyContinue
}
if (-not (Test-FlareSolverrEndpoint 'http://127.0.0.1:8191' 'hybrid-router')) {
  $failed = $true
  Start-ScheduledTask -TaskName 'FlareSolverr-Hybrid-Router' -ErrorAction SilentlyContinue
}

if ($failed) {
  Start-Sleep -Seconds 8
  Test-FlareSolverrEndpoint 'http://127.0.0.1:8191' 'hybrid-router-post-restart' | Out-Null
}

Write-Host 'Maintenance cycle complete'
