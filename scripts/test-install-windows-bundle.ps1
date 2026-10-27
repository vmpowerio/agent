param([Parameter(Mandatory=$true)][string]$Bundle,
      [Parameter(Mandatory=$true)][string]$ExpectedManifestSha256,
      [Parameter(Mandatory=$true)][string]$NssmExe)
# Native Windows filesystem/ACL/quoting tests; all service calls are replaced below.
# This is not a service lifecycle test. Run only on a disposable Windows runner.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Native Windows required' }
. (Join-Path $PSScriptRoot 'install-windows-bundle.ps1') -Bundle $Bundle -ExpectedManifestSha256 $ExpectedManifestSha256 -NssmExe $NssmExe
function Assert-True($Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Assert-Throws([scriptblock]$Work) { $failed=$false;try { & $Work } catch {$failed=$true};Assert-True $failed 'Expected rejection' }
function Assert-Restricted([string]$Path,[bool]$PublicRead) {
    $acl=Get-Acl -LiteralPath $Path
    Assert-True $acl.AreAccessRulesProtected 'ACL inheritance must be disabled'
    Assert-True ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -eq 'S-1-5-32-544') 'ACL owner is not Administrators'
    $rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    Assert-True ($rules.Count -eq $(if ($PublicRead) {3} else {2})) 'Missing or extra ACL rule'
    foreach ($rule in $rules) {
        $sid=$rule.IdentityReference.Value
        Assert-True ($sid -in @('S-1-5-18','S-1-5-32-544','S-1-5-32-545')) 'Unexpected ACL principal'
        if ($sid -eq 'S-1-5-32-545') {
            Assert-True $PublicRead 'Configuration readable by ordinary users'
            $writes=[Security.AccessControl.FileSystemRights]'Write, Delete, ChangePermissions, TakeOwnership'
            Assert-True (($rule.FileSystemRights -band $writes) -eq 0) 'Executable writable by ordinary users'
        }
    }
}
$temporary=Join-Path ([IO.Path]::GetTempPath()) ('vmpower installer fixture '+[Guid]::NewGuid().ToString())
$script:events=New-Object Collections.Generic.List[object]
$script:previous=$null;$script:failStart=$false
# Exact substitutions prevent any SCM/NSSM service operations in this suite.
function Get-ServiceSnapshot { return $script:previous }
function Get-ManagedPaths { return @{Prefix=(Join-Path $temporary 'Program Files\VMPower\Agent');Config=(Join-Path $temporary 'ProgramData\vmpower\vmpower.conf')} }
function Get-Service { return @{Status='Stopped'} }
function Start-Service { param($Name);$script:events.Add(@('restore-start',$Name)) }
$realNative=${function:Invoke-CheckedNative}
function Invoke-CheckedNative([string]$Exe,[string[]]$Arguments) {
    $script:events.Add(@($Exe)+$Arguments)
    if ($Arguments[0] -eq 'start' -and $script:failStart) { $script:failStart=$false;throw 'Synthetic startup failure' }
    return ''
}
function Test-FinalizationRollback([string]$Kind) {
    # Run the actual installer transaction; substitute only SCM/NSSM operations.
    # A post-activation callback must restore state, not merely preserve a file.
    $script:transactionState=@{ImagePath='"C:\prior wrapper\nssm.exe"';Active=($Kind -ne 'denied');
        Start=$(if ($Kind -eq 'denied') {'SERVICE_DISABLED'} else {'SERVICE_DEMAND_START'});
        Application='C:\prior runtime\node.exe';AppDirectory='C:\prior runtime';AppParameters='"prior app.js"'}
    $expectedState=$script:transactionState.Clone()
    function Get-ServiceSnapshot { return $script:transactionState.Clone() }
    function Get-Service { return @{Status=$(if ($script:transactionState.Active) {'Running'} else {'Stopped'})} }
    function Start-Service { $script:transactionState.Active=$true }
    function Wait-StableServiceChild { }
    function Invoke-CheckedNative([string]$Exe,[string[]]$Arguments) {
        if ($Exe -eq 'sc.exe') { $script:transactionState.ImagePath=$Arguments[3] }
        elseif ($Arguments[0] -eq 'set') { $script:transactionState[$Arguments[2]]=$Arguments[3] }
        elseif ($Arguments[0] -eq 'stop') { $script:transactionState.Active=$false }
        elseif ($Arguments[0] -eq 'start') { $script:transactionState.Active=$true }
        else { throw 'Unexpected native operation in finalization control' }
        return ''
    }
    $sentinel=Join-Path $temporary ('finalization-'+$Kind+'.exe')
    [IO.File]::WriteAllText($sentinel,'retained shim')
    $lock=$null;$script:finalizationCallbackReached=$false;$failed=$false
    try {
        if ($Kind -eq 'locked') { $lock=[IO.File]::Open($sentinel,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read) }
        try {
            $null=Install-AgentBundle $Bundle $ExpectedManifestSha256 $NssmExe $null {
                Assert-True ($script:transactionState.Application -ne $expectedState.Application) 'Callback did not run after new runtime activation'
                $script:finalizationCallbackReached=$true
                if ($Kind -eq 'changed') {
                    [IO.File]::WriteAllText($sentinel,'replacement owned by another actor')
                    throw 'Synthetic changed shim refusal'
                }
                if ($Kind -eq 'denied') { throw (New-Object UnauthorizedAccessException 'Synthetic shim deletion denial') }
                if ($Kind -eq 'marker') { throw (New-Object IO.IOException 'Synthetic marker restoration failure') }
                Remove-Item -LiteralPath $sentinel -Force -ErrorAction Stop
            }
        } catch { $failed=$true }
        Assert-True ($failed -and $script:finalizationCallbackReached) 'Post-activation failure control did not reach rollback'
        foreach ($setting in $expectedState.Keys) {
            Assert-True ($script:transactionState[$setting] -ceq $expectedState[$setting]) ('Finalization failed to restore '+$setting)
        }
        Assert-True ([Convert]::ToBase64String([IO.File]::ReadAllBytes($config)) -eq [Convert]::ToBase64String($before)) 'Finalization failure changed configuration'
        Assert-True (Test-Path -LiteralPath $sentinel) 'Finalization compensation deleted shim'
        if ($Kind -eq 'changed') { Assert-True ([IO.File]::ReadAllText($sentinel) -ceq 'replacement owned by another actor') 'Compensation overwrote unrelated replacement' }
    } finally {
        if ($lock) { $lock.Dispose() }
        $script:finalizationCallbackReached=$false
    }
}
try {
    $null=New-Item -ItemType Directory -Path $temporary
    $null=New-Item -ItemType Directory -Path (Join-Path $temporary 'Program Files'),(Join-Path $temporary 'ProgramData')
    $node=Join-Path $Bundle 'runtime\node.exe'
    # Execute a harmless native argv echo, not the application or a shell.
    $argv=@('space value','inner"quote','trailing\','','$(not-a-command); & |','unicode é')
    $echo=& $realNative $node (@('-e','process.stdout.write(JSON.stringify(process.argv.slice(1)))','--')+$argv)
    # PS5.1 sends a decoded array as one pipeline object; @() nests that array.
    # Direct assignment preserves its six entries, including the empty string.
    $actual=ConvertFrom-Json -InputObject $echo
    $actualType=if ($null -eq $actual) {'null'} else {$actual.GetType().FullName}
    $actualCount=if ($null -eq $actual) {0} else {$actual.Count}
    Assert-True ($actualCount -eq $argv.Count) ('Native argument count changed; expected='+$argv.Count+' actual='+$actualCount+' type='+$actualType)
    for ($i=0;$i -lt $argv.Count;$i++) { Assert-True ($actual[$i] -ceq $argv[$i]) ('Native argument changed; index='+$i+' type='+$actualType) }
    Assert-Throws { Quote-NativeArgument "invalid`nargument" }
    Assert-Throws { Assert-ConfigJson '{}' }
    Assert-Throws { Assert-ConfigJson '' }
    $json='{"key":"fixture \"key\"","secret":"$(`not-a-command`)\\","vm_id":"fixture","host":"https://invalid.example"}'
    $config=(Get-ManagedPaths).Config
    $null=Ensure-Config $config $json
    $before=[IO.File]::ReadAllBytes($config)
    $null=Ensure-Config $config '{}'
    Assert-True ([Convert]::ToBase64String([IO.File]::ReadAllBytes($config)) -eq [Convert]::ToBase64String($before)) 'Configuration overwritten'
    Assert-Restricted $config $false
    $badAcl=Get-Acl -LiteralPath $config
    $ordinaryUsers=New-Object Security.Principal.SecurityIdentifier 'S-1-5-32-545'
    $badAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($ordinaryUsers,'Read','Allow')))
    Set-Acl -LiteralPath $config -AclObject $badAcl
    Assert-Throws { Assert-Restricted $config $false }
    $null=Ensure-Config $config $null
    Assert-Restricted $config $false
    $hardlink=Join-Path (Split-Path $config) 'linked.conf'
    $null=New-Item -ItemType HardLink -Path $hardlink -Target $config
    Assert-Throws { Ensure-Config $config $json }
    Remove-Item -LiteralPath $hardlink
    $script:previous=@{ImagePath='"C:\legacy wrapper\nssm.exe"';Active=$true;Start='SERVICE_DEMAND_START';Application='C:\legacy agent\vmpower.exe';AppDirectory='C:\legacy agent';AppParameters='--fixture "two words"'}
    $script:failStart=$true
    Assert-Throws { Install-AgentBundle $Bundle $ExpectedManifestSha256 $NssmExe $null }
    foreach ($setting in @('Application','AppDirectory','AppParameters','Start')) {
        $restored=@($script:events | Where-Object { $_.Count -eq 5 -and $_[1] -eq 'set' -and $_[3] -eq $setting -and $_[4] -ceq $script:previous[$setting] })
        Assert-True ($restored.Count -eq 1) ('Previous '+$setting+' not restored')
    }
    Assert-True (@($script:events | Where-Object { $_[0] -eq 'restore-start' }).Count -eq 1) 'Previous active service not restored'
    Assert-True ([Convert]::ToBase64String([IO.File]::ReadAllBytes($config)) -eq [Convert]::ToBase64String($before)) 'Rollback changed config'
    $installedNode=Join-Path (Get-ManagedPaths).Prefix ('releases\'+$ExpectedManifestSha256+'\runtime\node.exe')
    Assert-Restricted $installedNode $true
    Assert-Restricted (Split-Path (Split-Path $installedNode)) $true
    $script:previous.Active=$false;$script:events.Clear()
    $result=Install-AgentBundle $Bundle $ExpectedManifestSha256 $NssmExe '{}'
    Assert-True ($result.configuration -eq 'retained') 'Upgrade config not retained'
    Assert-True (@($script:events | Where-Object { $_.Count -gt 1 -and $_[1] -eq 'start' }).Count -eq 0) 'Stopped service was started'
    $script:previous=$null;$script:failStart=$true;$script:events.Clear()
    Assert-Throws { Install-AgentBundle $Bundle $ExpectedManifestSha256 $NssmExe $null }
    Assert-True (@($script:events | Where-Object { $_.Count -eq 4 -and $_[1] -eq 'remove' }).Count -eq 1) 'Failed first install did not remove created service'
    Assert-True (Test-Path -LiteralPath $config) 'Failed first install removed config'
    $script:events.Clear()
    # The fixed metadata mock permits testing uninstall without touching SCM.
    $pins=Get-Content -Raw (Join-Path $PSScriptRoot '../distribution/nssm-pins.json') | ConvertFrom-Json
    $script:installedWrapper=Join-Path (Get-ManagedPaths).Prefix ('wrappers\'+$pins.x64Sha256+'\nssm.exe')
    function Get-CimInstance { return @{PathName=('"'+$script:installedWrapper+'"');State='Stopped'} }
    $null=Uninstall-AgentBundle
    Assert-True (@($script:events | Where-Object { $_.Count -eq 4 -and $_[1] -eq 'remove' -and $_[2] -eq 'vmpoweragent' -and $_[3] -eq 'confirm' }).Count -eq 1) 'Uninstall did not remove the mocked service'
    Assert-True (Test-Path -LiteralPath $config) 'Uninstall removed config'
    Assert-True (Test-Path -LiteralPath $installedNode) 'Uninstall removed retained release'
    foreach ($fault in @('changed','locked','denied','marker')) { Test-FinalizationRollback $fault }
    @{status='PASS';nativeArgv='PASS';configuration='PASS';ACL='PASS';mockedServiceRollback='PASS';postActivationFailureStateControls=4;serviceLifecycle='NOT_TESTED'} | ConvertTo-Json
} finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force }
}
