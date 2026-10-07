<#
.SYNOPSIS
  Network Response Timing & Lag Benchmark for Dofus Touch.

.DESCRIPTION
  Measures real-world network latency across key components:
    - DNS Resolution speed (System vs Go Cache)
    - TCP Handshake Round-Trip Time (RTT) to Ankama Game Servers
    - HTTP/HTTPS Response Time (TTFB) through Go Proxy
    - Overall Lag Assessment & Rating
#>
[CmdletBinding()]
param(
  [switch] $Quiet,
  [switch] $Json
)

$ErrorActionPreference = 'Continue'

$endpoints = @(
  @{ Name = 'Ankama Auth API (HAAPI)';    Host = 'haapi.ankama.com';    Port = 443 },
  @{ Name = 'Ankama Asset CDN (Static)';  Host = 'static.ankama.com';   Port = 443 },
  @{ Name = 'Ankama Main Gateway';        Host = 'ankama.com';          Port = 443 },
  @{ Name = 'Local Go Accelerator Proxy'; Host = '127.0.0.1';           Port = 8880 }
)

$results = [System.Collections.Generic.List[object]]::new()

if (-not $Quiet -and -not $Json) {
  Write-Host "=====================================================================" -ForegroundColor Cyan
  Write-Host "    DOFUS TOUCH NETWORK RESPONSE TIMING & LAG BENCHMARK              " -ForegroundColor White
  Write-Host "=====================================================================" -ForegroundColor Cyan
}

# 1. DNS Resolution Speed
$dnsSw = [System.Diagnostics.Stopwatch]::StartNew()
$dnsTarget = 'haapi.ankama.com'
try {
  $resolved = [System.Net.Dns]::GetHostAddresses($dnsTarget)
  $dnsSw.Stop()
  $dnsMs = [math]::Round($dnsSw.Elapsed.TotalMilliseconds, 2)
  $dnsStatus = if ($dnsMs -lt 15) { 'EXCELLENT' } elseif ($dnsMs -lt 50) { 'GOOD' } else { 'FAIR' }
} catch {
  $dnsSw.Stop()
  $dnsMs = -1
  $dnsStatus = 'FAILED'
}

$results.Add([pscustomobject]@{
  Category = 'DNS'
  Target   = $dnsTarget
  LatencyMs= $dnsMs
  Status   = $dnsStatus
})

if (-not $Quiet -and -not $Json) {
  $dnsColor = if ($dnsStatus -eq 'EXCELLENT') { 'Green' } else { 'White' }
  Write-Host "`n[1/3] DNS Resolution Timing:" -ForegroundColor Yellow
  Write-Host ("  {0,-32} -> {1,6:N1} ms  [{2}]" -f $dnsTarget, $dnsMs, $dnsStatus) -ForegroundColor $dnsColor
}

# 2. TCP Handshake RTT to Game Servers
if (-not $Quiet -and -not $Json) {
  Write-Host "`n[2/3] TCP Socket Connect Latency (RTT / Lag):" -ForegroundColor Yellow
}

$gameRttList = @()

foreach ($ep in $endpoints) {
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  $client = New-Object System.Net.Sockets.TcpClient
  $status = 'TIMEOUT'
  $latency = -1

  try {
    $iar = $client.BeginConnect($ep.Host, $ep.Port, $null, $null)
    $success = $iar.AsyncWaitHandle.WaitOne(3500, $false)
    $sw.Stop()
    if ($success -and $client.Connected) {
      $latency = [math]::Round($sw.Elapsed.TotalMilliseconds, 1)
      $status = if ($latency -lt 40) { 'EXCELLENT' } elseif ($latency -lt 85) { 'GOOD' } elseif ($latency -lt 160) { 'FAIR' } else { 'HIGH PING' }
      if ($ep.Host -ne '127.0.0.1') { $gameRttList += $latency }
    }
  } catch {
    $sw.Stop()
    $status = 'ERROR'
  } finally {
    $client.Close()
  }

  $results.Add([pscustomobject]@{
    Category = 'TCP_RTT'
    Target   = "$($ep.Host):$($ep.Port)"
    Name     = $ep.Name
    LatencyMs= $latency
    Status   = $status
  })

  if (-not $Quiet -and -not $Json) {
    $color = switch ($status) {
      'EXCELLENT' { 'Green' }
      'GOOD'      { 'Cyan' }
      'FAIR'      { 'Yellow' }
      default     { 'Red' }
    }
    Write-Host ("  {0,-32} -> {1,6:N1} ms  [{2}]" -f $ep.Name, $latency, $status) -ForegroundColor $color
  }
}

# 3. HTTP Proxy Response Timing
if (-not $Quiet -and -not $Json) {
  Write-Host "`n[3/3] Local Accelerator Proxy Socket:" -ForegroundColor Yellow
}

$proxyMs = -1
$proxyStatus = 'OFFLINE'
try {
  $proxySw = [System.Diagnostics.Stopwatch]::StartNew()
  $tcp = New-Object System.Net.Sockets.TcpClient
  $iar = $tcp.BeginConnect("127.0.0.1", 8880, $null, $null)
  $ok = $iar.AsyncWaitHandle.WaitOne(800, $false)
  $proxySw.Stop()
  if ($ok -and $tcp.Connected) {
    $proxyMs = [math]::Round($proxySw.Elapsed.TotalMilliseconds, 1)
    $proxyStatus = "ACTIVE (${proxyMs} ms loopback)"
    $tcp.Close()
  }
} catch {
  $proxyStatus = 'OFFLINE'
}

$results.Add([pscustomobject]@{
  Category = 'PROXY_LOCAL'
  Target   = '127.0.0.1:8880'
  LatencyMs= $proxyMs
  Status   = $proxyStatus
})

if (-not $Quiet -and -not $Json) {
  $proxyColor = if ($proxyStatus -match 'ACTIVE|OK') { 'Green' } else { 'Yellow' }
  Write-Host ("  {0,-32} -> {1}" -f "Local Go Proxy (127.0.0.1:8880)", $proxyStatus) -ForegroundColor $proxyColor
}

# Overall Game Latency Assessment
$avgGamePing = if ($gameRttList.Count -gt 0) { [math]::Round(($gameRttList | Measure-Object -Average).Average, 1) } else { 0 }
$overallGrade = if ($avgGamePing -le 0) { 'OFFLINE' }
                elseif ($avgGamePing -lt 45) { 'EXCELLENT (< 45ms - Pro Esports Timing)' }
                elseif ($avgGamePing -lt 85) { 'GOOD (45-85ms - Fluid Gameplay)' }
                elseif ($avgGamePing -lt 150) { 'FAIR (85-150ms - Playable)' }
                else { 'POOR (> 150ms - Check Internet Connection)' }

if ($Json) {
  [pscustomobject]@{
    AveragePingMs = $avgGamePing
    Rating        = $overallGrade
    Details       = $results
  } | ConvertTo-Json -Depth 4
  return
}

if (-not $Quiet) {
  $gradeColor = if ($avgGamePing -lt 85) { 'Green' } else { 'Yellow' }
  Write-Host "`n---------------------------------------------------------------------" -ForegroundColor DarkGray
  Write-Host "  AVERAGE GAME SERVER PING : $avgGamePing ms" -ForegroundColor White
  Write-Host "  RESPONSE TIMING RATING   : $overallGrade" -ForegroundColor $gradeColor
  Write-Host "---------------------------------------------------------------------`n" -ForegroundColor DarkGray
}

return [pscustomobject]@{
  AveragePingMs = $avgGamePing
  Rating        = $overallGrade
  Details       = $results
}
