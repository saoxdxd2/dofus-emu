$farmExe = 'C:\Users\sao\Documents\dofus-emu\DofusFarm.exe'
[System.Reflection.Assembly]::LoadFrom($farmExe) | Out-Null
$backend = New-Object DofusFarm.Engine.WebView2Backend
$backend.Initialize()

$opt = New-Object DofusFarm.Engine.ClientLaunchOptions
$opt.InstanceId = 'diag-test'
$opt.EntryUrl = 'https://dofus.local/standalone.html'
$opt.DisguiseScript = @"
window.__logs = [];
window.__errors = [];
var origErr = console.error;
console.error = function() {
    window.__errors.push(Array.prototype.slice.call(arguments).join(' '));
    origErr.apply(console, arguments);
};
window.addEventListener('error', function(e) {
    window.__errors.push(e.message + ' at ' + e.filename + ':' + e.lineno);
});
"@
$backend.Start($opt)

$sw = [System.Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalSeconds -lt 4) {
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 50
}

$form = $backend.GetForm('diag-test')
$t = $form.ExecuteScriptAsync(@"
JSON.stringify({
    errors: window.__errors || [],
    hasGui: !!window.gui,
    scriptsLoaded: document.getElementsByTagName('script').length,
    bodyLength: document.body ? document.body.innerHTML.length : 0
})
"@)

while (-not $t.IsCompleted) {
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 25
}

Write-Host "JS State: $($t.Result)"
$backend.Stop('diag-test')
Start-Sleep -Seconds 1
$backend.Dispose()
