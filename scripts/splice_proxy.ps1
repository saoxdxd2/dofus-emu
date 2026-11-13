$p = (Resolve-Path 'dofus-net-proxy.go').Path
$lines = [System.IO.File]::ReadAllLines($p)
$rem = @{}
for ($i = 2420; $i -le 2520; $i++) { $rem[$i] = $true }
$out = New-Object System.Collections.Generic.List[string]
for ($i = 0; $i -lt $lines.Length; $i++) {
  if ($rem[$i + 1]) { continue }
  $out.Add($lines[$i])
}
[System.IO.File]::WriteAllText($p, ([string]::Join([environment]::NewLine, $out.ToArray()) + [environment]::NewLine))
"wrote $($out.Count) lines"
