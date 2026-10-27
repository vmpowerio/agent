# Filesystem-only controls for the actual shim reconciliation helper. No package,
# service, legacy executable, network request or broad cleanup is invoked.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Windows PowerShell required' }
. (Join-Path $PSScriptRoot 'install-windows-bundle.ps1')
. (Join-Path $PSScriptRoot 'qualify-windows-package-ownership.ps1')
function Assert-True($Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Assert-Rejected([scriptblock]$Action,[string]$Name) {
    $failed=$false
    try { $null=& $Action } catch { $failed=$true }
    Assert-True $failed ('Unsafe cleanup accepted: '+$Name)
}
function Test-FailurePreservation([bool]$PrimaryFails,[bool]$CleanupFails) {
    # Execute the actual qualifier catch/finally blocks with a controlled try
    # body. This detects the original masking bug without running any lifecycle.
    $tokens=$null;$errors=$null
    $ast=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'qualify-native-windows.ps1'),[ref]$tokens,[ref]$errors)
    Assert-True ($errors.Count -eq 0) 'Qualifier parser failure'
    $main=@($ast.EndBlock.Statements | Where-Object { $_ -is [System.Management.Automation.Language.TryStatementAst] -and $_.Extent.Text.Contains('NATIVE_QUALIFICATION_FAILED') })
    Assert-True ($main.Count -eq 1 -and $main[0].CatchClauses.Count -eq 1) 'Primary failure capture block missing'
    $body=if ($PrimaryFails) { "throw 'synthetic-primary-do-not-persist'" } else { '$result.status="PASS"' }
    $control=[ScriptBlock]::Create('try {'+$body+'} catch '+$main[0].CatchClauses[0].Body.Extent.Text+' finally '+$main[0].Finally.Extent.Text)
    $result=@{status='PASS';phases=@(@{phase='known-completed-phase'});packageOwnership=@{phase='known-package-phase'}}
    $script:lastPackageFailure=$null;$script:failureReceipt=$null
    $Output=$fixture
    function Recorded-Cleanup { return @{status=$(if ($CleanupFails) {'FAIL'} else {'PASS'});classification='controlled-cleanup'} }
    function Save-Json([string]$Path,$Value) { $script:failureReceipt=$Value | ConvertTo-Json -Depth 20 }
    & $control
    $saved=$script:failureReceipt | ConvertFrom-Json
    Assert-True ($saved.status -eq 'FAIL') 'Qualification failure was hidden'
    Assert-True ($saved.cleanup.status -eq $(if ($CleanupFails) {'FAIL'} else {'PASS'})) 'Cleanup classification lost'
    Assert-True (-not $script:failureReceipt.Contains('synthetic-primary-do-not-persist')) 'Raw exception persisted'
    if ($PrimaryFails) {
        Assert-True ($saved.primaryFailure.phase -eq 'known-package-phase' -and $saved.primaryFailure.classification -eq 'NATIVE_QUALIFICATION_FAILED') 'Primary failure masked by cleanup'
    } else { Assert-True (-not $saved.PSObject.Properties['primaryFailure']) 'Cleanup failure invented a primary failure' }
}
$originalRoot=$env:ChocolateyInstall
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('vmpower shim controls '+[Guid]::NewGuid().ToString())
$cases=0
try {
    $env:ChocolateyInstall=$fixture
    $bin=Join-Path $fixture 'bin';$null=New-Item -ItemType Directory -Path $bin -Force
    Set-ProtectedAcl $fixture $true $true;Set-ProtectedAcl $bin $true $true
    $shim=Join-Path $bin 'vmpower-agent.amd64.exe'
    $neighbor=Join-Path $fixture 'unrelated-retained.txt';[IO.File]::WriteAllText($neighbor,'keep')
    $owned=@{chocolatey=(Join-Path $bin 'choco.exe');chocolateyShim=$shim;chocolateyShimAbsent=$true}
    [IO.File]::WriteAllText($shim,'synthetic nonexecutable shim')
    $expected=(Get-FileHash -LiteralPath $shim -Algorithm SHA256).Hash.ToLowerInvariant()
    $result=Remove-OwnedLegacyShim $owned $true
    Assert-True ($result.status -eq 'REMOVED_OWNED_ORPHAN' -and $result.sha256 -ceq $expected -and -not (Test-Path -LiteralPath $shim)) 'Exact orphan was not reconciled'
    Assert-True ([IO.File]::ReadAllText($neighbor) -ceq 'keep') 'Unrelated file changed';$cases++
    Assert-True ((Remove-OwnedLegacyShim $owned $true).status -eq 'ALREADY_ABSENT') 'Repeated cleanup not idempotent';$cases++

    [IO.File]::WriteAllText($shim,'keep')
    Assert-Rejected { Remove-OwnedLegacyShim $owned $false } 'registered package'
    Assert-True (Test-Path -LiteralPath $shim) 'Registered shim removed';$cases++
    $unowned=$owned.Clone();$unowned.Remove('chocolateyShimAbsent')
    Assert-Rejected { Remove-OwnedLegacyShim $unowned $true } 'missing absence journal'
    Assert-True (Test-Path -LiteralPath $shim) 'Unjournaled shim removed';$cases++
    $foreign=$owned.Clone();$foreign.chocolateyShim=$neighbor
    Assert-Rejected { Remove-OwnedLegacyShim $foreign $true } 'foreign path'
    Assert-True ([IO.File]::ReadAllText($neighbor) -ceq 'keep') 'Foreign file removed';$cases++
    $foreign=$owned.Clone();$foreign.chocolatey=Join-Path $fixture 'other-choco.exe'
    Assert-Rejected { Remove-OwnedLegacyShim $foreign $true } 'foreign package-manager path';$cases++

    $acl=Get-Acl -LiteralPath $bin
    $users=New-Object Security.Principal.SecurityIdentifier 'S-1-5-32-545'
    $rule=New-Object Security.AccessControl.FileSystemAccessRule($users,'Write','Allow')
    $acl.AddAccessRule($rule);Set-Acl -LiteralPath $bin -AclObject $acl
    Assert-Rejected { Remove-OwnedLegacyShim $owned $true } 'writable parent'
    Assert-True (Test-Path -LiteralPath $shim) 'Shim removed under unsafe parent'
    Set-ProtectedAcl $bin $true $true;$cases++

    Remove-Item -LiteralPath $shim
    $null=New-Item -ItemType Directory -Path $shim
    Assert-Rejected { Remove-OwnedLegacyShim $owned $true } 'directory alias'
    Assert-True (Test-Path -LiteralPath $shim -PathType Container) 'Directory removed'
    Remove-Item -LiteralPath $shim;$cases++
    $null=New-Item -ItemType SymbolicLink -Path $shim -Target $neighbor
    Assert-Rejected { Remove-OwnedLegacyShim $owned $true } 'reparse alias'
    Assert-True ([IO.File]::ReadAllText($neighbor) -ceq 'keep') 'Linked target modified'
    Remove-Item -LiteralPath $shim;$cases++
    $null=New-Item -ItemType HardLink -Path $shim -Target $neighbor
    Assert-Rejected { Remove-OwnedLegacyShim $owned $true } 'hardlink alias'
    Assert-True ([IO.File]::ReadAllText($neighbor) -ceq 'keep') 'Hardlinked target modified'
    Remove-Item -LiteralPath $shim;$cases++
    Test-FailurePreservation $true $true;$cases++
    Test-FailurePreservation $true $false;$cases++
    Test-FailurePreservation $false $true;$cases++
    . (Join-Path $PSScriptRoot 'test-windows-product-shim.ps1')
    $productCases=Test-WindowsProductShim $fixture
    @{status='PASS';cases=($cases+$productCases);originalControls=$cases;productHookControls=$productCases;productHookOwnershipProof='synthetic in wiring controls; actual proof in native package phases';packageOperations=0;serviceOperations=0;legacyExecutableRuns=0} | ConvertTo-Json -Compress
} finally {
    $env:ChocolateyInstall=$originalRoot
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
}
