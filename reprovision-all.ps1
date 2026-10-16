[System.Reflection.Assembly]::LoadFrom((Resolve-Path ".\DofusFarm.exe").Path) | Out-Null
$engine = New-Object DofusFarm.Engine.FarmEngine

foreach ($slot in @("dofus-02", "dofus-03", "dofus-04")) {
    $avdDir = Join-Path $engine.AvdHome "$slot.avd"
    Write-Host "Reprovisioning $slot..."
    # Clean broken qcow2.qcow2 or empty overlays
    $broken = Join-Path $avdDir "userdata-qemu.img.qcow2.qcow2"
    if (Test-Path $broken) { Remove-Item $broken -Force }
    $qcow2 = Join-Path $avdDir "userdata-qemu.img.qcow2"
    if (Test-Path $qcow2) {
        $fi = Get-Item $qcow2
        if ($fi.Length -lt 1048576) { Remove-Item $qcow2 -Force }
    }
    $engine.ProvisionInstance($slot, 1024, 1, $null, "Instance #" + $slot.Substring(6), $null)
}

Write-Host "`nUpdated all instances. Checking qcow2 sizes across all AVDs:"
Get-ChildItem -Path (Join-Path $engine.AvdHome "*\userdata*.qcow2") | Select-Object FullName, Length
