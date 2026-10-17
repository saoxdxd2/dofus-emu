[System.Reflection.Assembly]::LoadFrom((Resolve-Path ".\DofusFarm.exe").Path) | Out-Null
$engine = New-Object DofusFarm.Engine.FarmEngine
Write-Host "GoldenMasterPath: $($engine.GoldenMasterPath)"
Write-Host "GoldenOverlayPath: $($engine.GoldenOverlayPath)"

$dofus05Dir = Join-Path $engine.AvdHome "dofus-05.avd"
if (Test-Path $dofus05Dir) {
    Remove-Item -Path $dofus05Dir -Recurse -Force
}

Write-Host "Provisioning dofus-05..."
$engine.ProvisionInstance("dofus-05", 1024, 1, $null, "Instance #5", $null)
Write-Host "Provision finished."

Write-Host "`nContents of dofus-05.avd:"
Get-ChildItem -Path $dofus05Dir | Select-Object Name, Length
