<#
.SYNOPSIS
  Validates Code Integrity & Anti-Tamper Signatures.
#>
[CmdletBinding()]
param(
  [switch] $ThrowOnMismatch
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $PSScriptRoot 'app.integrity'
$Salt = 'DOFUS_FARM_INTEGRITY_SALT_2026_K7X9Q'

if (-not (Test-Path $ManifestPath)) {
  if ($ThrowOnMismatch) { throw "Integrity manifest missing: $ManifestPath" }
  return @{ Valid = $false; Reason = "Manifest missing" }
}

$lines = [System.IO.File]::ReadAllLines($ManifestPath)
$sha = [System.Security.Cryptography.SHA256]::Create()
$fileMap = @{}
$expectedSig = ''
$allHashes = [System.Text.StringBuilder]::new()

foreach ($line in $lines) {
  if ($line -match '^SIGNATURE\|([A-Fa-f0-9]+)') {
    $expectedSig = $matches[1]
  } elseif ($line -match '^([^|]+)\|([A-Fa-f0-9]+)') {
    $fileMap[$matches[1]] = $matches[2]
    $allHashes.Append($matches[2]) | Out-Null
  }
}

# Verify Signature
$saltedData = [System.Text.Encoding]::UTF8.GetBytes($allHashes.ToString() + $Salt)
$sigBytes = $sha.ComputeHash($saltedData)
$computedSig = [BitConverter]::ToString($sigBytes) -replace '-',''

if ($computedSig -ne $expectedSig) {
  if ($ThrowOnMismatch) { throw "Integrity signature altered or forged!" }
  return @{ Valid = $false; Reason = "Signature mismatch" }
}

$tamperedFiles = @()

foreach ($rel in $fileMap.Keys) {
  $fullPath = Join-Path $RepoRoot $rel
  if (-not (Test-Path $fullPath)) {
    $tamperedFiles += "$rel (Missing)"
    continue
  }
  $bytes = [System.IO.File]::ReadAllBytes($fullPath)
  $hashBytes = $sha.ComputeHash($bytes)
  $hex = [BitConverter]::ToString($hashBytes) -replace '-',''
  if ($hex -ne $fileMap[$rel]) {
    $tamperedFiles += "$rel (Hash mismatch - modified)"
  }
}

if ($tamperedFiles.Count -gt 0) {
  if ($ThrowOnMismatch) {
    throw "Application integrity violation! Tampered files: $($tamperedFiles -join ', ')"
  }
  return @{ Valid = $false; Tampered = $tamperedFiles }
}

return @{ Valid = $true; CheckedCount = $fileMap.Count }
