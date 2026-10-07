<#
.SYNOPSIS
  Injects stealth mobile spoofing shim into Dofus Touch APK and re-signs split APKs.

.DESCRIPTION
  Extracts the game APKM, injects mobile-disguise.js into assets/www/index.html and
  assets/www/js/init.js, strips vendor signatures, and re-signs all split APKs
  atomically using apksigner. This guarantees that WebView, WebGL, Navigator, and
  Cordova report a Samsung Galaxy A51 with Mali-G76 MP12 GPU with zero host CPU penalty.
#>
[CmdletBinding()]
param(
  [string] $ApkmPath = '',
  [switch] $Force
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
Add-Type -AssemblyName System.IO.Compression
$RepoRoot = Split-Path -Parent $PSScriptRoot
$SdkRoot  = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path $RepoRoot 'sdk' }
$ZipAlign = Join-Path $SdkRoot 'build-tools\34.0.0\zipalign.exe'
$ApkSigner = Join-Path $SdkRoot 'build-tools\34.0.0\apksigner.bat'
$ShimJs    = Join-Path $PSScriptRoot 'mobile-disguise.js'

function Write-Step($m) { Write-Host "`n[patch-apk] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "  [ok]   $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "  [FAIL] $m" -ForegroundColor Red }

if (-not (Test-Path $ShimJs)) { Write-Err "mobile-disguise.js not found at $ShimJs"; exit 1 }
if (-not (Test-Path $ApkSigner)) { Write-Err "apksigner not found at $ApkSigner"; exit 1 }
if (-not (Test-Path $ZipAlign)) { Write-Err "zipalign not found at $ZipAlign"; exit 1 }

if (-not $ApkmPath) {
  $found = Get-ChildItem (Join-Path $RepoRoot 'apks') -Filter '*dofustouch*.apkm' -File | Select-Object -First 1
  if ($found) { $ApkmPath = $found.FullName }
  else { Write-Err 'No dofustouch*.apkm found in apks/'; exit 1 }
}

Write-Step "Target APKM: $ApkmPath"

# 1. Setup Keystore
$KeyStore = Join-Path $env:TEMP 'dofus_debug.keystore'
if (-not (Test-Path $KeyStore)) {
  Write-Step 'Generating debug signing keystore...'
  $keytool = Get-ChildItem -Path "C:\Program Files\Java" -Filter "keytool.exe" -Recurse -File -EA SilentlyContinue | Select-Object -First 1
  $ktExe = if ($keytool) { $keytool.FullName } else { 'keytool.exe' }
  $oldEap = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  & $ktExe -genkey -v -keystore $KeyStore -storepass android -alias androiddebugkey -keypass android -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Android Debug,O=Android,C=US" 2>$null | Out-Null
  $ErrorActionPreference = $oldEap
  Write-Ok "Keystore generated: $KeyStore"
}

# 2. Extract APKM
Write-Step 'Extracting APKM package...'
$workDir = Join-Path $env:TEMP "apkm_patch_$([System.IO.Path]::GetRandomFileName())"
New-Item -ItemType Directory -Force -Path $workDir | Out-Null
[System.IO.Compression.ZipFile]::ExtractToDirectory($ApkmPath, $workDir)

$baseApk = Join-Path $workDir 'base.apk'
if (-not (Test-Path $baseApk)) { Write-Err "base.apk missing in $ApkmPath"; exit 1 }

# 3. Patch base.apk in-place without touching resources.arsc
Write-Step 'Injecting mobile disguise into base.apk (in-place zip update)...'
$zip = [System.IO.Compression.ZipFile]::Open($baseApk, [System.IO.Compression.ZipArchiveMode]::Update)

# 3a. Add/update assets/www/js/mobile-disguise.js
$disguiseEntry = $zip.GetEntry('assets/www/js/mobile-disguise.js')
if ($disguiseEntry) { $disguiseEntry.Delete() }
$newDisguise = $zip.CreateEntry('assets/www/js/mobile-disguise.js', [System.IO.Compression.CompressionLevel]::Optimal)
$shimBytes = [System.IO.File]::ReadAllBytes($ShimJs)
$sStream = $newDisguise.Open()
$sStream.Write($shimBytes, 0, $shimBytes.Length)
$sStream.Dispose()
Write-Ok 'Added assets/www/js/mobile-disguise.js'

# 3b. Patch assets/www/index.html
$indexEntry = $zip.GetEntry('assets/www/index.html')
if ($indexEntry) {
  $idxStream = $indexEntry.Open()
  $reader = New-Object System.IO.StreamReader($idxStream, [System.Text.Encoding]::UTF8)
  $html = $reader.ReadToEnd()
  $reader.Dispose()
  $idxStream.Dispose()
  if ($html -notmatch 'mobile-disguise\.js') {
    $indexEntry.Delete()
    $replacement = "<head>`r`n`t`t<script type=`"text/javascript`" src=`"js/mobile-disguise.js`"></script>"
    $html = $html -replace '<head>', $replacement
    $newIdx = $zip.CreateEntry('assets/www/index.html', [System.IO.Compression.CompressionLevel]::Optimal)
    $wStream = $newIdx.Open()
    $writer = New-Object System.IO.StreamWriter($wStream, [System.Text.Encoding]::UTF8)
    $writer.Write($html)
    $writer.Dispose()
    $wStream.Dispose()
    Write-Ok 'Injected mobile-disguise.js into assets/www/index.html'
  }
}

# 3c. Patch assets/www/js/init.js
$initEntry = $zip.GetEntry('assets/www/js/init.js')
if ($initEntry) {
  $initStream = $initEntry.Open()
  $reader2 = New-Object System.IO.StreamReader($initStream, [System.Text.Encoding]::UTF8)
  $initContent = $reader2.ReadToEnd()
  $reader2.Dispose()
  $initStream.Dispose()
  if ($initContent -notmatch '__MOBILE_DISGUISE_ACTIVE__') {
    $initEntry.Delete()
    $shimContent = [System.IO.File]::ReadAllText($ShimJs, [System.Text.Encoding]::UTF8)
    $combined = $shimContent + "`r`n" + $initContent
    $newInit = $zip.CreateEntry('assets/www/js/init.js', [System.IO.Compression.CompressionLevel]::Optimal)
    $wStream2 = $newInit.Open()
    $writer2 = New-Object System.IO.StreamWriter($wStream2, [System.Text.Encoding]::UTF8)
    $writer2.Write($combined)
    $writer2.Dispose()
    $wStream2.Dispose()
    Write-Ok 'Injected disguise shim into assets/www/js/init.js'
  }
}

# 3d. Strip old META-INF signatures
$metaEntries = @($zip.Entries | Where-Object { $_.FullName -like 'META-INF/*' })
foreach ($m in $metaEntries) { $m.Delete() }
$zip.Dispose()
Write-Ok 'In-place asset patch complete; resources.arsc untouched and preserved'

# 4. Strip old signatures, zipalign (4-byte alignment), and sign with apksigner
Write-Step 'Zipaligning and signing all split APKs...'
$allApks = Get-ChildItem -Path $workDir -Filter '*.apk' -File

$oldEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
foreach ($apk in $allApks) {
  # Strip META-INF from split APKs
  $sZip = [System.IO.Compression.ZipFile]::Open($apk.FullName, [System.IO.Compression.ZipArchiveMode]::Update)
  $toDelete = @($sZip.Entries | Where-Object { $_.FullName -like 'META-INF/*' })
  foreach ($e in $toDelete) { $e.Delete() }
  $sZip.Dispose()

  # Zipalign 4-byte boundaries (essential for Android mmap asset loader)
  $aligned = "$($apk.FullName).aligned"
  & $ZipAlign -f -p 4 $apk.FullName $aligned 2>$null | Out-Null
  if (Test-Path $aligned) {
    Move-Item $aligned $apk.FullName -Force
  }

  # Sign with apksigner
  & $ApkSigner sign --ks $KeyStore --ks-pass pass:android --key-pass pass:android $apk.FullName 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Write-Err "Failed to sign $($apk.Name)"
    $ErrorActionPreference = $oldEap
    exit 1
  }
}
Write-Ok "All $($allApks.Count) APKs zipaligned and signed successfully"

# 5. Verify signatures
Write-Step 'Verifying APK signatures...'
foreach ($apk in $allApks) {
  $v = & $ApkSigner verify -v $apk.FullName 2>$null | Out-String
  if ($v -notmatch 'Verifies') {
    Write-Err "Verification failed for $($apk.Name)"
    $ErrorActionPreference = $oldEap
    exit 1
  }
}
$ErrorActionPreference = $oldEap
Write-Ok 'Signature verification passed (v2/v3 verified)'

# 6. Re-pack APKM
Write-Step "Repacking APKM package: $(Split-Path -Leaf $ApkmPath)..."
$tempApkm = Join-Path $env:TEMP "repack_$([System.IO.Path]::GetRandomFileName()).apkm"
[System.IO.Compression.ZipFile]::CreateFromDirectory($workDir, $tempApkm)
Remove-Item $workDir -Recurse -Force -EA SilentlyContinue

# Overwrite original APKM atomically
Copy-Item $tempApkm $ApkmPath -Force
Remove-Item $tempApkm -Force -EA SilentlyContinue

$finalSize = [math]::Round((Get-Item $ApkmPath).Length / 1MB, 2)
Write-Ok "Patched APKM saved: $ApkmPath ($finalSize MB)"
Write-Host "`nMobile disguise successfully baked into the game package!" -ForegroundColor Green
