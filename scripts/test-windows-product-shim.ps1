# Run from the existing filesystem-only preflight. Actual package ownership proof
# is exercised separately by both real Chocolatey families in the native fixture.
function Test-WindowsProductShim([string]$FixtureRoot) {
    $product=Join-Path $FixtureRoot 'product-hook-controls'
    $tools=Join-Path $product 'lib\vmpower-agent\tools'
    $bin=Join-Path $product 'bin';$shim=Join-Path $bin 'vmpower-agent.amd64.exe'
    $null=New-Item -ItemType Directory -Path (Join-Path $tools 'scripts'),(Join-Path $tools 'bundle\runtime'),$bin -Force
    $source=Join-Path $PSScriptRoot '../packages/win32/node24/tools'
    foreach ($name in @('chocolateyInstall.ps1','chocolateyUninstall.ps1')) {
        Copy-Item -LiteralPath (Join-Path $source $name) -Destination (Join-Path $tools $name)
    }
    [IO.File]::WriteAllText((Join-Path $tools 'package-input.json'),'{"bundleManifestSha256":"fixture"}')
    # Stub only the service installer; real product hook order/removal is tested.
    [IO.File]::WriteAllText((Join-Path $tools 'scripts\install-windows-bundle.ps1'),@'
param([switch]$Apply,[string]$Action,[string]$Bundle,[string]$ExpectedManifestSha256,[string]$NssmExe)
if ($MyInvocation.InvocationName -ne '.') {
    $global:productShimServiceCalls++
    if ($global:productShimInstallerFails) { throw 'synthetic activation failure' }
    if ($global:productShimChanges) { [IO.File]::WriteAllText($global:productShimFile,'changed by another owner') }
}
'@)
    # The positive wiring control uses generated nonexecutables, not a forged real
    # ownership claim. Replace only the proof producer; keep actual removal code.
    $helper=[IO.File]::ReadAllText((Join-Path $source 'package-shim.ps1'))
    $helper=$helper.Replace('function Get-VmpowerPackageShimState(', 'function Get-UnusedRealPackageShimState(')
    $helper+=@'

function Get-VmpowerPackageShimState([string]$ToolsDirectory) {
    $file=$global:productShimFile
    if (-not (Test-Path -LiteralPath $file)) { return @{status='ABSENT';path=$file} }
    if ([IO.File]::ReadAllText($file) -cne 'owned synthetic shim') { throw 'unowned fixture shim' }
    return @{status='QUALIFIED_LEGACY';path=$file;sha256=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash;bytes=(Get-Item $file).Length}
}
'@
    [IO.File]::WriteAllText((Join-Path $tools 'package-shim.ps1'),$helper)
    $originalRoot=$env:ChocolateyInstall
    try {
        $env:ChocolateyInstall=$product
        $global:productShimFile=$shim;$global:productShimServiceCalls=0
        $global:productShimInstallerFails=$false;$global:productShimChanges=$false
        $neighbor=Join-Path $bin 'unrelated.exe';[IO.File]::WriteAllText($neighbor,'keep')
        function Get-Service { return $true } # No service-manager call in these controls.
        [IO.File]::WriteAllText($shim,'owned synthetic shim')
        & (Join-Path $tools 'chocolateyInstall.ps1')
        Assert-True (-not (Test-Path -LiteralPath $shim) -and $global:productShimServiceCalls -eq 1) 'Product install did not remove shim after activation'
        [IO.File]::WriteAllText($shim,'owned synthetic shim');$global:productShimInstallerFails=$true
        Assert-Rejected { & (Join-Path $tools 'chocolateyInstall.ps1') } 'activation failure'
        Assert-True ([IO.File]::ReadAllText($shim) -ceq 'owned synthetic shim') 'Failed activation lost predecessor shim'
        $global:productShimInstallerFails=$false
        [IO.File]::WriteAllText($shim,'custom unrelated shim');$before=$global:productShimServiceCalls
        Assert-Rejected { & (Join-Path $tools 'chocolateyInstall.ps1') } 'unowned product install shim'
        Assert-True ($global:productShimServiceCalls -eq $before -and [IO.File]::ReadAllText($shim) -ceq 'custom unrelated shim') 'Ambiguous install mutated service or shim'
        Assert-Rejected { & (Join-Path $tools 'chocolateyUninstall.ps1') } 'unowned product uninstall shim'
        Assert-True ($global:productShimServiceCalls -eq $before -and [IO.File]::ReadAllText($shim) -ceq 'custom unrelated shim') 'Ambiguous uninstall mutated service or shim'
        [IO.File]::WriteAllText($shim,'owned synthetic shim')
        & (Join-Path $tools 'chocolateyUninstall.ps1')
        Assert-True (-not (Test-Path -LiteralPath $shim)) 'Product uninstall left shim for fixture rescue'
        & (Join-Path $tools 'chocolateyInstall.ps1')
        Assert-True (-not (Test-Path -LiteralPath $shim)) 'Absent shim recreated'
        [IO.File]::WriteAllText($shim,'owned synthetic shim');$global:productShimChanges=$true
        Assert-Rejected { & (Join-Path $tools 'chocolateyInstall.ps1') } 'changed during activation'
        Assert-True ([IO.File]::ReadAllText($shim) -ceq 'changed by another owner') 'Intervening shim replacement deleted'
        Assert-True ([IO.File]::ReadAllText($neighbor) -ceq 'keep') 'Neighbor modified'
        # Actual proof helper, without stubbing: custom roots/locations must fail
        # closed, and an absent shim needs no inferred ownership.
        . (Join-Path $source 'package-shim.ps1')
        Assert-Rejected { Get-VmpowerPackageShimState $tools } 'unqualified existing shim root'
        Assert-Rejected { Get-VmpowerPackageShimState $bin } 'foreign tools location'
        Remove-Item -LiteralPath $shim
        Assert-True ((Get-VmpowerPackageShimState $tools).status -ceq 'ABSENT') 'Actual absent-shim proof failed'
        return 10
    } finally {
        $env:ChocolateyInstall=$originalRoot
        foreach ($name in @('productShimFile','productShimServiceCalls','productShimInstallerFails','productShimChanges')) {
            Remove-Variable -Name $name -Scope Global -ErrorAction SilentlyContinue
        }
    }
}
