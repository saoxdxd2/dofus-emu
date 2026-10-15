$files = @(
  'c:\Users\sao\Documents\dofus-emu\desktop\FarmEngine.cs',
  'c:\Users\sao\Documents\dofus-emu\desktop\hardware.cs',
  'c:\Users\sao\Documents\dofus-emu\desktop\mainform.cs',
  'c:\Users\sao\Documents\dofus-emu\desktop\program.cs',
  'c:\Users\sao\Documents\dofus-emu\desktop\installer.cs'
)
foreach ($f in $files) {
  Write-Output "=== $f ==="
  $lines = Get-Content $f
  Write-Output ("TOTAL LINES: " + $lines.Count)
  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match 'emulator|-gpu|gpu\.mode|avd|Process\.Start|Arguments|snapshot|config\.ini') {
      Write-Output ("{0}: {1}" -f ($i+1), $lines[$i].Trim())
    }
  }
}
