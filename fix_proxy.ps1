$path = "c:/Users/sao/Documents/dofus-emu/desktop/proxysupervisor.cs"
$lines = Get-Content $path
$newLines = New-Object System.Collections.Generic.List[string]
$lineNum = 0
$currentMethod = "unknown"
foreach ($line in $lines) {
    $lineNum++
    if ($line -match 'private void (\w+)\s*\(') { $currentMethod = $Matches[1] }
    if ($line -match 'public void (\w+)\s*\(') { $currentMethod = $Matches[1] }
    if ($line -match 'private bool (\w+)\s*\(') { $currentMethod = $Matches[1] }
    if ($line -match 'private string (\w+)\s*\(') { $currentMethod = $Matches[1] }
    if ($line -match 'private void Log\(string msg\)') { $currentMethod = "Log" }
    if ($line -match '^\s*catch \{\s*\}$') {
        $indent = ''
        if ($line -match '^(\s*)') { $indent = $Matches[1] }
        $newLine = $indent + 'catch (Exception ex) { Log("[WARN] ProxySupervisor.' + $currentMethod + ': " + ex.Message); }'
        $newLines.Add($newLine)
    } else {
        $newLines.Add($line)
    }
}
Set-Content $path $newLines
Write-Output "proxysupervisor.cs done"
