<#
.SYNOPSIS
  Generates Cryptographic SHA-256 Code Integrity Manifest & HMAC Signature.
.DESCRIPTION
  Computes SHA-256 checksums across all critical runtime scripts, JS behavioral models,
  and native binaries to detect tampering, modification, or reverse engineering attempts.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $PSScriptRoot 'app.integrity'
$Salt = 'DOFUS_FARM_INTEGRITY_SALT_2026_K7X9Q'

$CriticalFiles = @(
  'scripts\gui-manager.ps1',
  'scripts\start-farm.ps1',
  'scripts\instances.ps1',
  'scripts\cluster-manager.ps1',
  'scripts\boot-instance.ps1',
  'scripts\optimize-guest-deep.ps1',
  'scripts\patch-system-props.ps1',
  'scripts\mobile-disguise.js',
  'scripts\human-behavior-model.js',
  'scripts\session-vault.ps1',
  'scripts\fps-governor.ps1',
  'scripts\layout.ps1',
  'scripts\dofus-net-proxy.exe',
  'scripts\test-response-timing.ps1',
  'scripts\bench-input-latency.ps1',
  'scripts\run-spoof-audit.ps1',
  'scripts\uninstall.ps1',
  'install.ps1'
)

$sha = [System.Security.Cryptography.SHA256]::Create()
$lines = [System.Collections.Generic.List[string]]::new()
$allHashes = [System.Text.StringBuilder]::new()

foreach ($rel in $CriticalFiles) {
  $fullPath = Join-Path $RepoRoot $rel
  if (Test-Path $fullPath) {
    $bytes = [System.IO.File]::ReadAllBytes($fullPath)
    $hashBytes = $sha.ComputeHash($bytes)
    $hex = [BitConverter]::ToString($hashBytes) -replace '-',''
    $line = "$rel|$hex"
    $lines.Add($line)
    $allHashes.Append($hex) | Out-Null
  }
}

# Compute HMAC/Salted digest of all file hashes
$saltedData = [System.Text.Encoding]::UTF8.GetBytes($allHashes.ToString() + $Salt)
$sigBytes = $sha.ComputeHash($saltedData)
$signature = [BitConverter]::ToString($sigBytes) -replace '-',''

$lines.Add("SIGNATURE|$signature")

[System.IO.File]::WriteAllLines($ManifestPath, $lines)
Write-Host "Generated Code Integrity Manifest ($($lines.Count - 1) files signed): $ManifestPath" -ForegroundColor Green
