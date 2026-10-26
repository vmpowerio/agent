$ErrorActionPreference = 'Stop'
# Repeated removal is harmless; never call the legacy uninstall script.
if (Get-Service -Name vmpoweragent -ErrorAction SilentlyContinue) {
    & (Join-Path $PSScriptRoot 'scripts\install-windows-bundle.ps1') -Apply -Action Uninstall
}
# Configuration, content-addressed releases, wrapper and old executable remain.
