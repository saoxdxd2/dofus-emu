<#
.SYNOPSIS
  Provisioning pass to build the golden userdata image.

.DESCRIPTION
  Wrapper around make-golden-image.ps1 ensuring standard parameters:
  1024 MB RAM, host GPU, package disables, and clean shutdown state.
#>
[CmdletBinding()]
param(
  [string] $AvdName    = 'dofus',
  [string] $GoldenName = 'userdata-golden.img',
  [int]    $RamMb      = 1024,
  [switch] $SkipGame,
  [switch] $Force
)

$target = Join-Path $PSScriptRoot 'make-golden-image.ps1'
& $target -AvdName $AvdName -GoldenName $GoldenName -RamMb $RamMb -SkipGame:$SkipGame -Force:$Force
exit $LASTEXITCODE
