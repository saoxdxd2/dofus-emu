$ErrorActionPreference = 'Stop'
$repo = 'c:\Users\sao\Documents\dofus-emu'
Set-Location $repo
$out = Join-Path $repo 'tests\_diff_out.txt'
$old = git --no-pager show d661b25~1:desktop/client/www/build/script.js | Out-String
$new = git --no-pager show d661b25:desktop/client/www/build/script.js | Out-String
$sb = New-Object System.Text.StringBuilder
[void]$sb.AppendLine("old len=$($old.Length) new len=$($new.Length)")
$i = 0
$max = [Math]::Min($old.Length, $new.Length)
while ($i -lt $max -and $old[$i] -eq $new[$i]) { $i++ }
[void]$sb.AppendLine("first diff at: $i")
$start = [Math]::Max(0, $i - 800)
[void]$sb.AppendLine('=== OLD context ===')
[void]$sb.AppendLine($old.Substring($start, [Math]::Min(1800, $old.Length - $start)))
[void]$sb.AppendLine('=== NEW context ===')
[void]$sb.AppendLine($new.Substring($start, [Math]::Min(1800, $new.Length - $start)))
# Also find diff from the end
$j = 0
while ($j -lt $max -and $old[$old.Length - 1 - $j] -eq $new[$new.Length - 1 - $j]) { $j++ }
[void]$sb.AppendLine("last diff from end at: $j")
[System.IO.File]::WriteAllText($out, $sb.ToString())
Write-Output 'DONE'
