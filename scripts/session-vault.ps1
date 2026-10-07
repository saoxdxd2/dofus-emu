<#
.SYNOPSIS
  Session, OAuth Token & Cache Persistence Vault for Dofus Touch Instances.
.DESCRIPTION
  Safely saves and restores authentication tokens, OAuth sessions, server preferences,
  and offline asset caches so nothing is lost across restarts, updates, or disk operations:
    - Save-Session   : Exports app_webview LocalStorage, Cookies, IndexedDB, and shared_prefs.
    - Restore-Session: Re-injects tokens and cache into guest with correct ownership.
    - Commit-Session : Flushes QCOW2 differential overlay to disk.
    - Status-Session : Reports token presence and cache size for each slot.
#>
[CmdletBinding()]
param(
  [ValidateSet('Save', 'Restore', 'Commit', 'Status', 'BackupAll', 'RestoreAll')]
  [string] $Action = 'Status',
  [string] $InstanceName = '',
  [string] $Serial = ''
)

$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$SdkRoot  = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path $RepoRoot 'sdk' }
$Adb      = Join-Path $SdkRoot 'platform-tools\adb.exe'
$QemuImg  = Join-Path $SdkRoot 'emulator\qemu-img.exe'
$AvdHome  = Join-Path $env:USERPROFILE '.android\avd'

function Get-AppUid([string]$serial) {
  $pkgInfo = (& $Adb -s $serial shell dumpsys package com.ankama.dofustouch 2>$null) | Select-String -Pattern 'userId=(\d+)' | Select-Object -First 1
  if ($pkgInfo) { return [int]$pkgInfo.Matches[0].Groups[1].Value }
  return 10116
}

function Save-SessionForInstance([string]$instName, [string]$serial) {
  Write-Host "Saving session (OAuth tokens, cookies, cache) for $instName ($serial)..." -ForegroundColor Cyan
  $avdDir = Join-Path $AvdHome "$instName.avd"
  $vaultDir = Join-Path $avdDir 'vault_session'
  if (-not (Test-Path $vaultDir)) { New-Item -ItemType Directory -Path $vaultDir -Force | Out-Null }

  # Restart adbd as root to access /data/data
  & $Adb -s $serial root 2>$null | Out-Null
  Start-Sleep -Milliseconds 600

  # Archive app_webview (Local Storage, Cookies, IndexedDB) and shared_prefs into guest /sdcard
  $archiveCmd = "cd /data/data/com.ankama.dofustouch && tar -czf /sdcard/session_vault.tar.gz app_webview shared_prefs 2>/dev/null"
  & $Adb -s $serial shell $archiveCmd 2>$null | Out-Null

  # Pull archive to host vault
  $hostArchive = Join-Path $vaultDir 'session_vault.tar.gz'
  & $Adb -s $serial pull /sdcard/session_vault.tar.gz $hostArchive 2>$null | Out-Null
  & $Adb -s $serial shell rm /sdcard/session_vault.tar.gz 2>$null | Out-Null

  if (Test-Path $hostArchive) {
    $sizeKB = [math]::Round((Get-Item $hostArchive).Length / 1KB, 1)
    $meta = @{
      InstanceName = $instName
      Serial       = $serial
      SavedAt      = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
      SizeBytes    = (Get-Item $hostArchive).Length
      Protected    = $true
    }
    $meta | ConvertTo-Json | Set-Content -Path (Join-Path $vaultDir 'vault_manifest.json') -Encoding UTF8
    Write-Host "  [OK] Session vault saved: $hostArchive ($sizeKB KB)" -ForegroundColor Green
  } else {
    Write-Host "  [WARN] Failed to export session archive from guest." -ForegroundColor Yellow
  }
}

function Restore-SessionForInstance([string]$instName, [string]$serial) {
  Write-Host "Restoring session (OAuth tokens, cookies, cache) for $instName ($serial)..." -ForegroundColor Cyan
  $avdDir = Join-Path $AvdHome "$instName.avd"
  $vaultDir = Join-Path $avdDir 'vault_session'
  $hostArchive = Join-Path $vaultDir 'session_vault.tar.gz'

  if (-not (Test-Path $hostArchive)) {
    Write-Host "  [WARN] No saved session vault found for $instName at: $hostArchive" -ForegroundColor Yellow
    return
  }

  & $Adb -s $serial root 2>$null | Out-Null
  Start-Sleep -Milliseconds 600

  # Stop game process before swapping database/storage
  & $Adb -s $serial shell am force-stop com.ankama.dofustouch 2>$null | Out-Null

  # Push archive to guest and extract
  & $Adb -s $serial push $hostArchive /sdcard/session_vault.tar.gz 2>$null | Out-Null
  $extractCmd = "cd /data/data/com.ankama.dofustouch && tar -xzf /sdcard/session_vault.tar.gz 2>/dev/null"
  & $Adb -s $serial shell $extractCmd 2>$null | Out-Null
  & $Adb -s $serial shell rm /sdcard/session_vault.tar.gz 2>$null | Out-Null

  # Fix Linux permissions for Dofus Touch user
  $uid = Get-AppUid $serial
  & $Adb -s $serial shell "chown -R $uid`:$uid /data/data/com.ankama.dofustouch/app_webview /data/data/com.ankama.dofustouch/shared_prefs 2>/dev/null" | Out-Null
  & $Adb -s $serial shell "chmod -R 770 /data/data/com.ankama.dofustouch/app_webview /data/data/com.ankama.dofustouch/shared_prefs 2>/dev/null" | Out-Null

  Write-Host "  [OK] Session, OAuth tokens, and asset cache restored with UID $uid ownership!" -ForegroundColor Green
}

function Commit-OverlayForInstance([string]$instName) {
  $avdDir = Join-Path $AvdHome "$instName.avd"
  $qcow2 = Join-Path $avdDir 'userdata-qemu.img.qcow2'
  if (Test-Path $qcow2) {
    Write-Host "Flushing differential writes to backing disk for $instName..." -ForegroundColor Cyan
    & $QemuImg commit -f qcow2 $qcow2 | Out-Null
    Write-Host "  [OK] Committed QCOW2 overlay changes into backing disk." -ForegroundColor Green
  }
}

function Get-VaultStatus {
  $insts = (1..4) | ForEach-Object { "dofus-0$_" }
  $rows = foreach ($name in $insts) {
    $avdDir = Join-Path $AvdHome "$name.avd"
    $vaultDir = Join-Path $avdDir 'vault_session'
    $archive = Join-Path $vaultDir 'session_vault.tar.gz'
    $manifest = Join-Path $vaultDir 'vault_manifest.json'

    $hasVault = Test-Path $archive
    $size = if ($hasVault) { "$([math]::Round((Get-Item $archive).Length/1KB, 1)) KB" } else { "None" }
    $savedAt = "Never"
    if (Test-Path $manifest) {
      try {
        $mObj = Get-Content $manifest -Raw | ConvertFrom-Json
        $savedAt = $mObj.SavedAt
      } catch {}
    }

    $qcow2 = Join-Path $avdDir 'userdata-qemu.img.qcow2'
    $deltaSize = if (Test-Path $qcow2) { "$([math]::Round((Get-Item $qcow2).Length/1MB, 1)) MB" } else { "N/A" }

    [pscustomobject]@{
      Instance     = $name
      VaultStatus  = if ($hasVault) { "Saved (Protected)" } else { "No Vault" }
      Tokens_Cache = $size
      LastBackup   = $savedAt
      Qcow2Delta   = $deltaSize
    }
  }
  $rows | Format-Table -AutoSize
}

switch ($Action) {
  'Status' {
    Get-VaultStatus
  }
  'Save' {
    if (-not $InstanceName) { $InstanceName = 'dofus-01' }
    if (-not $Serial) {
      $idx = 1
      if ($InstanceName -match '(\d+)$') { $idx = [int]$Matches[1] }
      $Serial = "emulator-$(5554 + 2*($idx - 1))"
    }
    Save-SessionForInstance $InstanceName $Serial
  }
  'Restore' {
    if (-not $InstanceName) { $InstanceName = 'dofus-01' }
    if (-not $Serial) {
      $idx = 1
      if ($InstanceName -match '(\d+)$') { $idx = [int]$Matches[1] }
      $Serial = "emulator-$(5554 + 2*($idx - 1))"
    }
    Restore-SessionForInstance $InstanceName $Serial
  }
  'Commit' {
    if (-not $InstanceName) { $InstanceName = 'dofus-01' }
    Commit-OverlayForInstance $InstanceName
  }
  'BackupAll' {
    1..4 | ForEach-Object {
      $name = "dofus-0$_"
      $s = "emulator-$(5554 + 2*($_ - 1))"
      Save-SessionForInstance $name $s
    }
  }
  'RestoreAll' {
    1..4 | ForEach-Object {
      $name = "dofus-0$_"
      $s = "emulator-$(5554 + 2*($_ - 1))"
      Restore-SessionForInstance $name $s
    }
  }
}
