$ErrorActionPreference = "Stop"

Write-Host "=========================================================="
Write-Host "   DYNAMIC VERSION RESOLVER & PROTOCOL TEST               "
Write-Host "=========================================================="

$repoRoot = "C:\Users\sao\Documents\dofus-emu"
$farmExe = Join-Path $repoRoot "DofusFarm.exe"
if (-not (Test-Path $farmExe)) {
    Write-Error "DofusFarm.exe not found."
    exit 1
}

[System.Reflection.Assembly]::LoadFrom($farmExe) | Out-Null

# 1. Test Version Code Calculation
$testCases = @(
    @{ Semver = "3.14.2"; Expected = "3014002" },
    @{ Semver = "3.2.7"; Expected = "3002007" },
    @{ Semver = "3.15.0"; Expected = "3015000" },
    @{ Semver = "4.0.1"; Expected = "4000001" }
)

foreach ($tc in $testCases) {
    $calc = [DofusFarm.Engine.DynamicVersionResolver]::CalculateVersionCode($tc.Semver)
    if ($calc -ne $tc.Expected) {
        Write-Error "Version code mismatch for $($tc.Semver): Expected $($tc.Expected), got $calc"
        exit 1
    }
    Write-Host "  [OK] Version Code: $($tc.Semver) -> $calc"
}

# 2. Test Live Store Query
Write-Host "`nTesting Live Store Query..."
$storeVer = [DofusFarm.Engine.DynamicVersionResolver]::QueryGooglePlayStore(4000)
Write-Host "  Live Google Play Version: $storeVer"
if ([string]::IsNullOrEmpty($storeVer)) {
    Write-Warning "Store query returned empty (offline or throttled). Verifying fallback mechanism..."
} else {
    Write-Host "  [OK] Live Store Query Success: $storeVer"
}

# 3. Test Full Multi-Tier Resolution
Write-Host "`nTesting Multi-Tier ResolveVersion()..."
$tempCacheDir = Join-Path $env:TEMP "dofus_ver_test_$([Guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $tempCacheDir -Force | Out-Null

try {
    $verInfo = [DofusFarm.Engine.DynamicVersionResolver]::ResolveVersion($tempCacheDir, $true, [Action[string]]{ param($msg) Write-Host "    $msg" })
    Write-Host "  Resolved: $verInfo"
    if ([string]::IsNullOrEmpty($verInfo.AppVersion)) {
        Write-Error "Resolved AppVersion is empty!"
        exit 1
    }
    
    # Verify cached file was created
    $cacheFile = Join-Path $tempCacheDir "app_version.json"
    if (-not (Test-Path $cacheFile)) {
        Write-Error "Cache file was not created at $cacheFile"
        exit 1
    }
    $cachedJson = Get-Content $cacheFile -Raw
    Write-Host "`n  Cached app_version.json content:"
    Write-Host $cachedJson

    # Verify cold-start read from cache
    $coldInfo = [DofusFarm.Engine.DynamicVersionResolver]::ResolveVersion($tempCacheDir, $false, $null)
    Write-Host "  Cold-Start Resolved Source: $($coldInfo.Source), Version: $($coldInfo.AppVersion)"
    if ($coldInfo.AppVersion -ne $verInfo.AppVersion) {
        Write-Error "Cold-start cache mismatch!"
        exit 1
    }
    Write-Host "  [OK] Cache persistence verified successfully!"
} finally {
    Remove-Item -Recurse -Force $tempCacheDir -ErrorAction SilentlyContinue
}

Write-Host "`n>>> [SUCCESS] All Dynamic Version Resolver tests passed! <<<`n"
