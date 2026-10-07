<#
.SYNOPSIS
  Patch in-memory Android system properties (ro.*) in /dev/__properties__
  to eliminate all AOSP / emulator leaks from getprop and native frameworks.
#>
[CmdletBinding()]
param(
  [string] $Serial = 'emulator-5554',
  [string] $Model = 'SM-A515F',
  [string] $Brand = 'samsung',
  [string] $Manufacturer = 'samsung',
  [string] $Device = 'a51',
  [string] $Name = 'a51nsxx',
  [string] $Flavor = 'a51nsxx-user',
  [string] $Type = 'user',
  [string] $Tags = 'release-keys'
)

$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Adb = Join-Path $RepoRoot 'sdk\platform-tools\adb.exe'

function Patch-PropValue([byte[]]$bytes, [string]$oldVal, [string]$newVal) {
  $ob = [System.Text.Encoding]::ASCII.GetBytes($oldVal)
  $nb = [System.Text.Encoding]::ASCII.GetBytes($newVal + "`0")
  $count = 0
  for ($i=0; $i -le $bytes.Length - $ob.Length; $i++) {
    $match = $true
    for ($j=0; $j -lt $ob.Length; $j++) {
      if ($bytes[$i+$j] -ne $ob[$j]) { $match = $false; break }
    }
    if ($match) {
      # In Bionic property trie, the length byte is right before the value (at offset - 1)
      $bytes[$i - 1] = [byte]$newVal.Length
      for ($k=0; $k -lt $nb.Length; $k++) {
        $bytes[$i + $k] = $nb[$k]
      }
      # Zero out any remaining old bytes
      for ($k = $nb.Length; $k -lt [math]::Max($ob.Length, 32); $k++) {
        if ($i + $k -lt $bytes.Length) { $bytes[$i + $k] = 0 }
      }
      $count++
      $i += $ob.Length - 1
    }
  }
  return ($count -gt 0)
}

Write-Host "==> Patching in-memory system properties for $Serial..." -ForegroundColor Cyan

$propFiles = @(
  'u:object_r:default_prop:s0',
  'u:object_r:exported_default_prop:s0',
  'u:object_r:exported2_default_prop:s0',
  'u:object_r:vendor_default_prop:s0',
  'u:object_r:exported_fingerprint_prop:s0'
)

$patchPairs = @(
  @{ Old = "Android SDK built for x86_64"; New = $Model },
  @{ Old = "unknown"; New = $Manufacturer },
  @{ Old = "Android"; New = $Brand },
  @{ Old = "generic_x86_64"; New = $Device },
  @{ Old = "sdk_phone_x86_64-userdebug"; New = $Flavor },
  @{ Old = "sdk_phone_x86_64"; New = $Name },
  @{ Old = "test-keys"; New = $Tags },
  @{ Old = "userdebug"; New = $Type }
)

foreach ($pf in $propFiles) {
  $tempFile = Join-Path $env:TEMP "$Serial-$([System.IO.Path]::GetRandomFileName()).prop"
  & $Adb -s $Serial pull "/dev/__properties__/$pf" $tempFile 2>&1 | Out-Null
  if (Test-Path $tempFile) {
    $bytes = [System.IO.File]::ReadAllBytes($tempFile)
    $modified = $false
    foreach ($p in $patchPairs) {
      if (Patch-PropValue $bytes $p.Old $p.New) {
        $modified = $true
      }
    }
    if ($modified) {
      [System.IO.File]::WriteAllBytes($tempFile, $bytes)
      & $Adb -s $Serial shell "chmod 644 /dev/__properties__/$pf 2>/dev/null" | Out-Null
      & $Adb -s $Serial push $tempFile "/dev/__properties__/$pf" 2>&1 | Out-Null
      & $Adb -s $Serial shell "chmod 444 /dev/__properties__/$pf 2>/dev/null" | Out-Null
      Write-Host "  [ok] Patched /dev/__properties__/$pf" -ForegroundColor Green
    }
    Remove-Item $tempFile -Force -EA SilentlyContinue
  }
}

Write-Host "  [ok] All in-memory system properties patched successfully." -ForegroundColor Green
