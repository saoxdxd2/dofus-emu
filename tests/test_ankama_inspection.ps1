$ErrorActionPreference = "Stop"

$repoRoot = "C:\Users\sao\Documents\dofus-emu"
$farmExe = Join-Path $repoRoot "DofusFarm.exe"
[System.Reflection.Assembly]::LoadFrom($farmExe) | Out-Null
Add-Type -AssemblyName System.Windows.Forms

$backend = New-Object DofusFarm.Engine.WebView2Backend
$backend.Initialize()

$instId = "inspect-test"
$profileDir = Join-Path $env:TEMP "dofus_inspect_profile"
if (Test-Path $profileDir) { Remove-Item -Recurse -Force $profileDir -ErrorAction SilentlyContinue }

$options = New-Object DofusFarm.Engine.ClientLaunchOptions
$options.InstanceId = $instId
$options.DisplayName = "Ankama Inspection Test"
$options.UserDataDirectory = $profileDir
$options.EntryUrl = "https://dofus.local/standalone.html"
$options.Profile = [DofusFarm.Engine.DeviceCatalog]::GetProfile("galaxy-a51")
$options.DisguiseScript = [System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\mobile-disguise.js"))

$backend.Start($options)

$sw = [System.Diagnostics.Stopwatch]::StartNew()
Write-Host "Waiting for page to initialize..."
while ($sw.Elapsed.TotalSeconds -lt 4) {
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 50
}

# Fetch the form and execute script to get Ankama's exact inspection telemetry
$formField = $backend.GetType().GetField("_activeForms", [System.Reflection.BindingFlags]"NonPublic,Instance")
$dict = $formField.GetValue($backend)
$form = $dict[$instId]
$wvField = $form.GetType().GetField("_webView", [System.Reflection.BindingFlags]"NonPublic,Instance")
$wv = $wvField.GetValue($form)

$probeScript = @"
(function() {
    var info = {};
    var nav = window.navigator;
    if (nav) {
        info.platform = nav.platform;
        info.userAgent = nav.userAgent;
        info.appVersion = nav.appVersion;
        info.vendor = nav.vendor;
        info.deviceMemory = nav.deviceMemory;
        info.hardwareConcurrency = nav.hardwareConcurrency;
        info.maxTouchPoints = nav.maxTouchPoints;
        info.languages = nav.languages;
        info.pdfViewerEnabled = nav.pdfViewerEnabled;
        info.webdriver = nav.webdriver;
        info.build = nav.appInfo ? nav.appInfo.build : (window.appInfo ? window.appInfo.build : null);
    }
    if (nav && nav.userAgentData) {
        info.uaData = {
            platform: nav.userAgentData.platform,
            mobile: nav.userAgentData.mobile,
            brands: nav.userAgentData.brands
        };
    }
    var canvas = document.createElement('canvas');
    var gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
    if (gl) {
        var ext = gl.getExtension('WEBGL_debug_renderer_info');
        info.gpu = {
            vendor: gl.getParameter(gl.VENDOR),
            renderer: gl.getParameter(gl.RENDERER),
            version: gl.getParameter(gl.VERSION),
            unmaskedVendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : null,
            unmaskedRenderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null,
            maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE)
        };
    }
    if (window.device) {
        info.device = {
            platform: window.device.platform,
            model: window.device.model,
            version: window.device.version,
            manufacturer: window.device.manufacturer,
            isVirtual: window.device.isVirtual
        };
    }
    return JSON.stringify(info);
})();
"@

$task = $wv.CoreWebView2.ExecuteScriptAsync($probeScript)
while (-not $task.IsCompleted) {
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 20
}

$rawJson = $task.Result
Write-Host "`nRaw Inspection Telemetry Result:"
Write-Host $rawJson

$backend.Stop($instId)
$backend.Dispose()
try { Remove-Item -Recurse -Force $profileDir -ErrorAction SilentlyContinue } catch {}
