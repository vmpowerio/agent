# Same package ID upgrades 0.0.1 without invoking its destructive uninstaller.
# No before-modify hook: the installer must observe the previous active state.
$ErrorActionPreference = 'Stop'
$inputRecord = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'package-input.json') | ConvertFrom-Json
$bundle = Join-Path $PSScriptRoot 'bundle'
# Chocolatey shim suppression is package metadata, not a runtime bundle member.
$ignore = Join-Path $bundle 'runtime\node.exe.ignore'
if (Test-Path -LiteralPath $ignore) { Remove-Item -LiteralPath $ignore }
# This upgrade package accepts no credentials in package parameters or logs.
# Existing configuration is required; fresh enrollment uses the stdin installer.
try {
    & (Join-Path $PSScriptRoot 'scripts\install-windows-bundle.ps1') -Apply `
        -Bundle $bundle -ExpectedManifestSha256 $inputRecord.bundleManifestSha256 `
        -NssmExe (Join-Path $PSScriptRoot 'nssm.exe')
} finally {
    # Chocolatey scans executables after this script, including failed installs.
    [IO.File]::WriteAllBytes($ignore, [byte[]]@())
}
