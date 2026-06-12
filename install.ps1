param(
  [switch]$DryRun,
  [string]$InstallDir = "$HOME/.local/bin",
  [string]$MetadataUrl,
  [string]$Version
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Get-RepoSlug {
  try {
    $remote = git -C $Root config --get remote.origin.url 2>$null
  } catch {
    return $null
  }

  if ($remote -match 'github\.com[:/](.+?)(\.git)?$') {
    return $Matches[1]
  }

  return $null
}

function Get-MetadataUrl {
  if ($MetadataUrl) {
    return $MetadataUrl
  }

  $slug = Get-RepoSlug
  if (-not $slug) {
    return $null
  }

  if ($Version) {
    return "https://github.com/$slug/releases/download/$Version/decomp-release.json"
  }

  return "https://github.com/$slug/releases/latest/download/decomp-release.json"
}

function Get-PlatformName {
  if ($IsWindows) { return "windows" }
  if ($IsLinux) { return "linux" }
  if ($IsMacOS) { return "macos" }
  return "unsupported"
}

function Get-ArchName {
  switch ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()) {
    "X64" { return "x86_64" }
    "Arm64" { return "aarch64" }
    default { return "unknown" }
  }
}

function Get-OptionalToolStatus([string]$Name) {
  if (Get-Command $Name -ErrorAction SilentlyContinue) { return "present" }
  return "missing"
}

$ResolvedMetadataUrl = Get-MetadataUrl
$ResolvedPlatform = Get-PlatformName
$ResolvedArch = Get-ArchName

if ($DryRun) {
  $DisplayMetadataUrl = $ResolvedMetadataUrl
  if (-not $DisplayMetadataUrl) {
    $DisplayMetadataUrl = "unset"
  }
  Write-Output "dry-run: would install decomp"
  Write-Output "install-dir: $InstallDir"
  Write-Output "metadata-url: $DisplayMetadataUrl"
  Write-Output "platform: $ResolvedPlatform"
  Write-Output "arch: $ResolvedArch"
  exit 0
}

if (-not $ResolvedMetadataUrl) {
  throw "install.ps1: unable to derive release metadata URL; pass -MetadataUrl"
}

if ($ResolvedPlatform -eq "unsupported" -or $ResolvedArch -eq "unknown") {
  throw "install.ps1: unsupported platform or architecture ($ResolvedPlatform/$ResolvedArch)"
}

$metadata = Invoke-RestMethod -Uri $ResolvedMetadataUrl
$asset = $metadata.assets | Where-Object { $_.os -eq $ResolvedPlatform -and $_.arch -eq $ResolvedArch } | Select-Object -First 1

if (-not $asset) {
  throw "install.ps1: no release asset found for $ResolvedPlatform/$ResolvedArch"
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$temp = Join-Path ([System.IO.Path]::GetTempPath()) ("decomp-" + [System.Guid]::NewGuid().ToString("N"))
Invoke-WebRequest -Uri $asset.url -OutFile $temp
Move-Item -Force $temp (Join-Path $InstallDir "decomp")

Write-Output "installed: $(Join-Path $InstallDir 'decomp')"
Write-Output ("optional-tools: objdiff={0} analyzeHeadless={1}" -f (Get-OptionalToolStatus "objdiff"), (Get-OptionalToolStatus "analyzeHeadless"))
