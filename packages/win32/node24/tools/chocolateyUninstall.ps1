$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\install-windows-bundle.ps1')
. (Join-Path $PSScriptRoot 'package-shim.ps1')
$previousShim=Get-VmpowerPackageShimState $PSScriptRoot
# A changed/locked/denied shim must fail before the service can be removed.
Remove-VmpowerPackageShim $PSScriptRoot $previousShim
# Repeated removal is harmless; never call the legacy uninstall script.
if (Get-Service -Name vmpoweragent -ErrorAction SilentlyContinue) {
    & (Join-Path $PSScriptRoot 'scripts\install-windows-bundle.ps1') -Apply -Action Uninstall
}
# Configuration, content-addressed releases, wrapper and old executable remain.
