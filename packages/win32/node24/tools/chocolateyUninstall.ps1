$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\install-windows-bundle.ps1')
. (Join-Path $PSScriptRoot 'package-shim.ps1')
$previousShim=Get-VmpowerPackageShimState $PSScriptRoot
# Repeated removal is harmless; never call the legacy uninstall script.
if (Get-Service -Name vmpoweragent -ErrorAction SilentlyContinue) {
    & (Join-Path $PSScriptRoot 'scripts\install-windows-bundle.ps1') -Apply -Action Uninstall
}
Remove-VmpowerPackageShim $PSScriptRoot $previousShim
# Configuration, content-addressed releases, wrapper and old executable remain.
