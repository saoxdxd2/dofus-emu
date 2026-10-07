<#
.SYNOPSIS
  Freeze a validated AVD as the template that farm instances are cloned from.

.DESCRIPTION
  Why a template exists at all:

  Android 10 uses File-Based Encryption. The CE/DE keys for user 0 stored in
  /data are cryptographically paired with the key material in
  encryptionkey.img. A userdata image baked from one AVD therefore cannot be
  dropped into a different, freshly created AVD: vold fails with

      vold: Failed to prepare /data/system/users/0

  systemserver restart-loops and the guest never finishes booting.

  Seeding a blank AVD with the golden image fails for exactly this reason. The
  fix is to clone a complete, already-validated AVD - encryption keys and
  userdata together - so the pair always stays consistent.

  Workflow:
    1. scripts\make-golden-image.ps1   (builds the trimmed userdata + game)
    2. .\scripts\freeze-template.ps1     (this script - snapshot that AVD)
    3. .\scripts\cluster-manager.ps1 -Action Create -Count 4

.PARAMETER Source
  AVD name to freeze. Default 'dofus'.

.PARAMETER TemplateName
  Destination template name. Default 'dofus-template'.

.EXAMPLE
  .\scripts\freeze-template.ps1
#>
[CmdletBinding()]
param(
  [string] $Source       = 'dofus',
  [string] $TemplateName = 'dofus-template'
)

$ErrorActionPreference = 'Stop'
$AvdHome  = Join-Path $env:USERPROFILE '.android\avd'
$srcDir   = Join-Path $AvdHome "$Source.avd"
$tplDir   = Join-Path $AvdHome "$TemplateName.avd"

function Write-Step($m) { Write-Host "[tpl] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "[ok]  $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "[warn] $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "[FAIL] $m" -ForegroundColor Red }

if (-not (Test-Path $srcDir)) { Write-Err "source AVD not found: $srcDir"; exit 1 }

# Refuse to freeze a template from an AVD that is not actually in the state we
# need, otherwise every clone inherits the same defect.
$required = @(
  'config.ini',
  'encryptionkey.img',
  'userdata-golden.img'
)
$missing = $required | Where-Object { -not (Test-Path (Join-Path $srcDir $_)) }
if ($missing) {
  Write-Err "source AVD is incomplete, missing: $($missing -join ', ')"
  Write-Err 'Build it first with scripts\make-golden-image.ps1'
  exit 1
}

$golden = Join-Path $srcDir 'userdata-golden.img'
Write-Step "Freezing '$Source' -> '$TemplateName'"
Write-Host "    golden master    : $([math]::Round((Get-Item $golden).Length/1MB,0)) MB"

# Transient emulator state must not be cloned: lock files, the launch parameter
# cache and snapshot pointers all refer to the source AVD by absolute path.
$skip = '\.lock|^tmpAdbCmds|emu-launch-params|hardware-qemu\.ini$|quickbootChoice|read-snapshot|version_num\.cache|^snapshots$|^identity\.json$|^system\.prop$|userdata-qemu\.img'

Write-Step 'Cloning template directory'
Remove-Item $tplDir -Recurse -Force -EA SilentlyContinue
New-Item -ItemType Directory -Force -Path $tplDir | Out-Null

Get-ChildItem $srcDir -File |
  Where-Object { $_.Name -notmatch $skip } |
  ForEach-Object { Copy-Item $_.FullName (Join-Path $tplDir $_.Name) -Force }

# Verify the template is complete and internally consistent.
$stillMissing = $required | Where-Object { -not (Test-Path (Join-Path $tplDir $_)) }
if ($stillMissing) { Write-Err "template incomplete: $($stillMissing -join ', ')"; exit 1 }

$sz = [math]::Round((Get-ChildItem $tplDir -Recurse -File | Measure-Object Length -Sum).Sum/1MB,0)
Write-Ok "template frozen at $tplDir ($sz MB)"
Write-Host @"

    Template contains the encryptionkey.img that matches the baked /data, so
    every clone boots without the vold 'Failed to prepare /data/system/users/0'
    loop.

    Next:
        .\scripts\cluster-manager.ps1 -Action Create -Count 4 -RamMb 1024
        .\scripts\start-farm.ps1 -Count 4
"@ -ForegroundColor Cyan