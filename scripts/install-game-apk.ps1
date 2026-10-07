<#
.SYNOPSIS
  Installs the patched Dofus Touch APKM package onto target emulator instance(s).
#>
[CmdletBinding()]
param(
  [string] $Serial = '',
  [string] $ApkmPath = ''
)

$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$SdkRoot  = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path $RepoRoot 'sdk' }
$Adb      = Join-Path $SdkRoot 'platform-tools\adb.exe'

if (-not (Test-Path $Adb)) { Write-Error "adb not found at $Adb"; exit 1 }

if (-not $Serial) {
  $devices = & $Adb devices | Select-String '^(emulator-\d+)\s+device' | ForEach-Object { $_.Matches[0].Groups[1].Value }
  if (-not $devices -or $devices.Count -eq 0) {
    Write-Error "No online emulator devices found."
    exit 1
  }
  $Serial = $devices[0]
}

if (-not $ApkmPath) {
  $found = Get-ChildItem (Join-Path $RepoRoot 'apks') -Filter '*dofustouch*.apkm' -File | Select-Object -First 1
  if ($found) { $ApkmPath = $found.FullName }
  else { Write-Error "No dofustouch*.apkm found in apks/"; exit 1 }
}

Write-Host "Installing patched package: $(Split-Path -Leaf $ApkmPath)" -ForegroundColor Cyan
Write-Host "Target device: $Serial" -ForegroundColor Cyan

$workDir = Join-Path $env:TEMP "dofus_install_$([System.IO.Path]::GetRandomFileName())"
New-Item -ItemType Directory -Force -Path $workDir | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($ApkmPath, $workDir)

$apks = @(Get-ChildItem $workDir -Filter '*.apk' -File | ForEach-Object { $_.FullName })
Write-Host "Found $($apks.Count) split APKs. Running atomic install transaction..." -ForegroundColor Yellow

$res = & $Adb -s $Serial install-multiple -r -g @apks 2>&1 | Out-String
Write-Host $res.Trim()

Remove-Item $workDir -Recurse -Force -EA SilentlyContinue

if ($res -match 'Success') {
  Write-Host "  [OK] Patched Dofus Touch successfully installed on $Serial!" -ForegroundColor Green
  & $Adb -s $Serial shell cmd package compile -m verify -f com.ankama.dofustouch 2>&1 | Out-Null
} else {
  Write-Error "Installation failed: $res"
}
