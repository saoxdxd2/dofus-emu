<#
.SYNOPSIS
  Host Laptop Hardware & Driver Diagnostics Engine.

.DESCRIPTION
  Detects host GPU, CPU, Memory, and Virtualization capabilities,
  checks display driver versions, and generates an optimal virtualization
  profile for peak performance at the lowest cost.
#>
[CmdletBinding()]
param()

function Get-HardwareDiagnostics {
  [CmdletBinding()]
  param()

  $gpus = @(Get-CimInstance Win32_VideoController)
  $discreteGpu = $gpus | Where-Object { $_.Name -match 'NVIDIA|GeForce|Radeon|AMD' } | Select-Object -First 1
  $integratedGpu = $gpus | Where-Object { $_.Name -match 'Intel|UHD|Iris' } | Select-Object -First 1
  $primaryGpu = if ($discreteGpu) { $discreteGpu } elseif ($integratedGpu) { $integratedGpu } elseif ($gpus.Count -gt 0) { $gpus[0] } else { $null }

  $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
  $cs  = Get-CimInstance Win32_ComputerSystem
  $os  = Get-CimInstance Win32_OperatingSystem

  $hostRamGB = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
  $freeRamGB = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
  $cores     = [int]$cpu.NumberOfCores
  $threads   = [int]$cpu.NumberOfLogicalProcessors
  $gpuName   = if ($primaryGpu) { $primaryGpu.Name } else { 'Generic Display' }
  $gpuDriver = if ($primaryGpu) { $primaryGpu.DriverVersion } else { 'Unknown' }
  $gpuVramMB = if ($primaryGpu -and $primaryGpu.AdapterRAM) { [math]::Round($primaryGpu.AdapterRAM / 1MB, 0) } else { 0 }
  $hasDiscrete = ($discreteGpu -ne $null)
  $whpxActive  = [bool]$cs.HypervisorPresent

  # Hardware Evaluation Logic
  $isIntelGpu = $gpuName -match 'Intel'
  $isNvidia   = $gpuName -match 'NVIDIA'
  $isAmd      = $gpuName -match 'AMD|Radeon'

  $recommendedRamMb = 768
  $recommendedCores = 1
  $recommendedVsync = 30
  $profileName = "Lightweight 1-Core Mobile Farm"
  $explanation = ""

  if ($hostRamGB -le 8) {
    $recommendedRamMb = 768
    $recommendedCores = 1
    $profileName = "Efficiency 1-Core Profile (8 GB Host Optimized)"
    $explanation = "Host RAM is 8 GB. Allocating 768 MB per instance with 512 MB zRAM prevents Windows memory swapping, while 1 vCPU eliminates thread contention on $threads logical cores."
  } elseif ($hostRamGB -le 16) {
    $recommendedRamMb = 1024
    $recommendedCores = if ($threads -ge 8) { 1 } else { 2 }
    $profileName = "Standard Dual/Single Core Balanced Profile"
    $explanation = "Host has 16 GB RAM. 1024 MB provides ample headroom for WebGL textures."
  } else {
    $recommendedRamMb = 1536
    $recommendedCores = 2
    $profileName = "High-Performance Workstation Profile"
    $explanation = "Abundant memory and CPU threads available."
  }

  return [pscustomobject]@{
    HostCpu             = $cpu.Name.Trim()
    PhysicalCores       = $cores
    LogicalThreads      = $threads
    HostRamGB           = $hostRamGB
    FreeRamGB           = $freeRamGB
    GpuName             = $gpuName
    GpuDriver           = $gpuDriver
    GpuVramMB           = $gpuVramMB
    IsIntelUhd          = $isIntelGpu
    HasDiscreteGpu      = $hasDiscrete
    WhpxActive          = $whpxActive
    RecommendedRamMb    = $recommendedRamMb
    RecommendedCores    = $recommendedCores
    RecommendedVsync    = $recommendedVsync
    ProfileName         = $profileName
    Recommendation      = $explanation
  }
}

$diag = Get-HardwareDiagnostics

if ($MyInvocation.InvocationName -ne '.') {
  Write-Host "============================================================" -ForegroundColor Cyan
  Write-Host "         HOST HARDWARE & DRIVER COMPATIBILITY REPORT         " -ForegroundColor White
  Write-Host "============================================================" -ForegroundColor Cyan
  Write-Host "  CPU                 : $($diag.HostCpu) ($($diag.PhysicalCores) Cores / $($diag.LogicalThreads) Threads)"
  Write-Host "  Host Memory         : $($diag.HostRamGB) GB Total / $($diag.FreeRamGB) GB Free"
  Write-Host "  GPU Graphics        : $($diag.GpuName) (Driver: $($diag.GpuDriver))"
  Write-Host "  Hypervisor (WHPX)   : $(if ($diag.WhpxActive) { 'Active & Hardware Accelerated' } else { 'Disabled (Enable Hyper-V)' })"
  Write-Host "  Passthrough Engine  : WHPX / ANGLE Direct3D 11 via -gpu host"
  Write-Host "  Recommended Config  : $($diag.RecommendedRamMb) MB RAM | $($diag.RecommendedCores) Core(s) per instance"
  Write-Host "  Profile             : $($diag.ProfileName)"
  Write-Host "  Architecture Reason : $($diag.Recommendation)"
  Write-Host "============================================================`n" -ForegroundColor Cyan
}

return $diag
