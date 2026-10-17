# Standalone Diagnostic for Asset & Map Loading inside WebView2
$ErrorActionPreference = "Stop"

$repoRoot = "C:\Users\sao\Documents\dofus-emu"
$farmExe = Join-Path $repoRoot "DofusFarm.exe"
[System.Reflection.Assembly]::LoadFrom($farmExe) | Out-Null
Add-Type -AssemblyName System.Windows.Forms

$backend = New-Object DofusFarm.Engine.WebView2Backend
$backend.Initialize()

$instId = "map-diag-test"
$profileDir = Join-Path $env:TEMP "dofus_map_diag_profile"
if (Test-Path $profileDir) { Remove-Item -Recurse -Force $profileDir -ErrorAction SilentlyContinue }

$options = New-Object DofusFarm.Engine.ClientLaunchOptions
$options.InstanceId = $instId
$options.DisplayName = "Map Diagnostic"
$options.UserDataDirectory = $profileDir
$options.EntryUrl = "https://dofus.local/standalone.html"
$options.Profile = [DofusFarm.Engine.DeviceCatalog]::GetProfile("galaxy-a51")

$logs = [System.Collections.Generic.List[string]]::new()
$backend.add_OnLog({
    param($msg)
    $logs.Add($msg)
    Write-Host $msg
})

$backend.Start($options)

# Wait 8 seconds for game client to boot
$sw = [System.Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalSeconds -lt 8) {
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 50
}

# Now inspect active form
$form = $backend.GetType().GetField("_activeForms", [System.Reflection.BindingFlags]"NonPublic,Instance").GetValue($backend)[$instId]
$wv = $form.GetType().GetField("_webView", [System.Reflection.BindingFlags]"NonPublic,Instance").GetValue($form)

$jsCode = @'
JSON.stringify((function(){
    try {
        var res = {
            hasGui: !!window.gui,
            hasIsoEngine: !!window.isoEngine,
            hasMapScene: !!(window.isoEngine && window.isoEngine.mapScene),
            bodyClass: document.body ? document.body.className : null,
            bodyW: document.body ? document.body.clientWidth : null,
            bodyH: document.body ? document.body.clientHeight : null,
            dofusBodyEl: !!document.querySelector('.dofusBody'),
            canvases: []
        };
        if (window.gui && typeof window.gui._resizeUi === "function") {
            try { window.gui._resizeUi(); } catch(e) { res.resizeErr = e.message; res.resizeStack = e.stack; }
        }
        var cvs = document.querySelectorAll('canvas');
        for (var i = 0; i < cvs.length; i++) {
            var c = cvs[i];
            res.canvases.push({
                id: c.id,
                className: c.className,
                w: c.width,
                h: c.height,
                style: c.getAttribute('style')
            });
        }
        if (window.isoEngine && window.isoEngine.mapScene && window.isoEngine.mapScene.canvas) {
            res.isoCanvasW = window.isoEngine.mapScene.canvas.width;
            res.isoCanvasH = window.isoEngine.mapScene.canvas.height;
            res.isoStyle = window.isoEngine.mapScene.canvas.getAttribute("style");
        }
        return res;
    } catch(err) {
        return { error: err.toString(), stack: err.stack };
    }
})())
'@

$task = $wv.CoreWebView2.ExecuteScriptAsync($jsCode)
while (-not $task.IsCompleted) {
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 50
}

Write-Host "`n=== IN-BROWSER ASSET TEST RESULT ==="
Write-Host $task.Result

$backend.Stop($instId)
[System.Windows.Forms.Application]::DoEvents()
