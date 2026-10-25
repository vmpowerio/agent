param([Parameter(Mandatory=$true)][string]$Output,[switch]$CleanupOnly)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
function Assert-Hosted {
    if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or $env:ImageOS -notin @('win25','win25-vs2026') -or $env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitProcess) { throw 'Admitted ephemeral Windows2025 x64 runner required' }
    $who=New-Object Security.Principal.WindowsPrincipal ([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $who.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Elevated runner required' }
}
Assert-Hosted
. (Join-Path $PSScriptRoot 'install-windows-bundle.ps1')
$Output=[IO.Path]::GetFullPath($Output)
if ($Output -ine [IO.Path]::GetFullPath((Join-Path $env:RUNNER_TEMP 'vmpower-native'))) { throw 'Fixed runner fixture directory required' }
$product=Join-Path $env:ProgramFiles 'VMPower';$configDir='C:\ProgramData\vmpower';$config=Join-Path $configDir 'vmpower.conf'
$journal=Join-Path $Output 'owned-windows.json'
function Save-Json([string]$Path,$Value) { [IO.File]::WriteAllText($Path,($Value | ConvertTo-Json -Depth 20),(New-Object Text.UTF8Encoding $false)) }
function Assert-True($Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Wait-Condition([scriptblock]$Check,[int]$Seconds=20) {
    $end=[DateTime]::UtcNow.AddSeconds($Seconds)
    while ([DateTime]::UtcNow -lt $end) { $value=& $Check;if ($value) {return $value};Start-Sleep -Milliseconds 250 }
    throw 'Native acceptance condition timed out'
}
function Receiver-State {
    $file=Join-Path $Output 'receiver.json'
    if (Test-Path -LiteralPath $file) { try { return (Get-Content -Raw -LiteralPath $file | ConvertFrom-Json) } catch {} }
    return @{port=$null;count=0;rejected=0}
}
function Service-Child([string]$Expected) {
    $service=Get-CimInstance Win32_Service -Filter "Name='vmpoweragent'"
    if (-not $service) { throw 'Service is absent' }
    $children=@(Get-CimInstance Win32_Process -Filter ('ParentProcessId='+$service.ProcessId) | Where-Object { $_.ExecutablePath -ieq $Expected })
    Assert-True ($children.Count -eq 1) 'Expected service Node child not observed'
    return $children[0].ProcessId
}
function Assert-Acl([string]$Path,[bool]$PublicRead) {
    $acl=Get-Acl -LiteralPath $Path
    Assert-True $acl.AreAccessRulesProtected 'Inherited ACL'
    $rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    Assert-True ($rules.Count -eq $(if ($PublicRead) {3} else {2})) 'Unexpected ACL rules'
    foreach ($rule in $rules) {
        $sid=$rule.IdentityReference.Value
        Assert-True ($sid -in @('S-1-5-18','S-1-5-32-544','S-1-5-32-545')) 'Unexpected ACL principal'
        if ($sid -eq 'S-1-5-32-545') {
            Assert-True $PublicRead 'Configuration readable by ordinary users'
            Assert-True (($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]'Write, Delete, ChangePermissions, TakeOwnership') -eq 0) 'Executable writable by ordinary users'
        }
    }
}
function Cleanup-Owned {
    if (-not (Test-Path -LiteralPath $journal)) { return @{status='NO_OWNED_RESOURCES'} }
    $owned=Get-Content -Raw -LiteralPath $journal | ConvertFrom-Json
    Assert-True ($owned.run -eq $env:GITHUB_RUN_ID -and $owned.product -eq $product -and $owned.configDirectory -eq $configDir) 'Cleanup ownership mismatch'
    $service=Get-CimInstance Win32_Service -Filter "Name='vmpoweragent'"
    if ($service) {
        if ($service.State -ne 'Stopped') { Stop-Service -Name vmpoweragent -Force }
        $null=Invoke-CheckedNative 'sc.exe' @('delete','vmpoweragent')
    }
    $sourceNode=Join-Path $Output 'bundle\runtime\node.exe'
    $survivors=@(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and ($_.ExecutablePath.StartsWith($product+'\',[StringComparison]::OrdinalIgnoreCase) -or ($_.ExecutablePath -ieq $sourceNode -and $_.ProcessId -ne $owned.receiverPid)) })
    foreach ($process in $survivors) { Stop-Process -Id $process.ProcessId -Force }
    if ($owned.receiverPid) {
        $receiver=Get-CimInstance Win32_Process -Filter ('ProcessId='+$owned.receiverPid)
        if ($receiver) {
            $expected=(Quote-NativeArgument $sourceNode)+' '+(Quote-NativeArgument (Join-Path $PSScriptRoot 'native-report-fixture.cjs'))
            Assert-True ($receiver.ExecutablePath -ieq $sourceNode -and $receiver.CommandLine -ceq $expected) 'Receiver identity mismatch'
            Stop-Process -Id $owned.receiverPid -Force
        }
        $null=Wait-Condition { -not (Get-Process -Id $owned.receiverPid -ErrorAction SilentlyContinue) }
    }
    foreach ($name in $owned.firewallNames) {
        if (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue) { Remove-NetFirewallRule -Name $name }
        Assert-True (-not (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue)) 'Firewall cleanup failed'
    }
    foreach ($directory in @($product,$configDir)) { if (Test-Path -LiteralPath $directory) { Remove-Item -LiteralPath $directory -Recurse -Force } }
    $null=Wait-Condition { -not (Get-CimInstance Win32_Service -Filter "Name='vmpoweragent'") }
    Assert-True (-not (Test-Path -LiteralPath $product) -and -not (Test-Path -LiteralPath $configDir)) 'Managed path cleanup failed'
    Assert-True (-not $owned.receiverPid -or -not (Get-Process -Id $owned.receiverPid -ErrorAction SilentlyContinue)) 'Receiver cleanup failed'
    foreach ($entry in Get-ChildItem -LiteralPath $Output -Force) {
        if ($entry.Name -notin @('evidence','owned-windows.json')) { Remove-Item -LiteralPath $entry.FullName -Recurse -Force }
    }
    $result=@{status='PASS';serviceAbsent=$true;childrenAbsent=($survivors.Count -eq 0);managedFilesAbsent=$true;fixtureInputsRemoved=$true;firewallRulesRemoved=$true;receiverStopped=$true}
    Save-Json (Join-Path $Output 'evidence\cleanup-windows.json') $result
    Assert-True ($survivors.Count -eq 0) 'Service left children; forced cleanup performed'
    return $result
}
if ($CleanupOnly) { Cleanup-Owned | ConvertTo-Json;return }
# Reject occupied destinations before reading config or changing ACLs/services.
Assert-True (-not (Test-Path -LiteralPath $product) -and -not (Test-Path -LiteralPath $configDir)) 'Managed destination already exists'
Assert-True (-not (Get-CimInstance Win32_Service -Filter "Name='vmpoweragent'")) 'Service already exists'
$profiles=@(Get-NetFirewallProfile)
Assert-True ($profiles.Count -eq 3 -and @($profiles | Where-Object { $_.Enabled.ToString() -ne 'True' }).Count -eq 0) 'Firewall is not enabled; native isolation unqualified'
$prepared=Get-Content -Raw (Join-Path $Output 'evidence\prepared.json') | ConvertFrom-Json
$owned=@{run=$env:GITHUB_RUN_ID;product=$product;configDirectory=$configDir;firewallNames=@();receiverPid=$null};Save-Json $journal $owned
$result=@{status='FAIL';head=$prepared.head;platform=[Environment]::OSVersion.VersionString;image=$env:ImageOS;imageVersion=$env:ImageVersion;powerShell=$PSVersionTable.PSVersion.ToString();phases=@();customerOSCompatibility='Unknown'}
$node=Join-Path $Output 'bundle\runtime\node.exe';$wrapper=Join-Path $Output 'nssm.exe'
try {
    # Block nonloopback addresses only for the four exact Node executable paths.
    # Runner/control-plane binaries and global firewall profiles remain unchanged.
    $programs=@($node)
    foreach ($entry in $prepared.bundles.PSObject.Properties) { $programs+=Join-Path $product ('Agent\releases\'+$entry.Value+'\runtime\node.exe') }
    for ($i=0;$i -lt $programs.Count;$i++) {
        $name='VMPowerNativeFixture-'+$env:GITHUB_RUN_ID+'-'+$i
        Assert-True (-not (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue)) 'Firewall rule already exists'
        $owned.firewallNames+=@($name);Save-Json $journal $owned
        $null=New-NetFirewallRule -Name $name -DisplayName $name -Direction Outbound -Action Block -Profile Any -Program $programs[$i] -RemoteAddress @('0.0.0.0-127.0.0.0','127.0.0.2-255.255.255.255','::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff')
        Assert-True ((Get-NetFirewallRule -Name $name).Enabled -eq 'True') 'Firewall rule inactive'
    }
    $receiver=New-Object Diagnostics.Process
    $receiver.StartInfo.FileName=$node
    $receiver.StartInfo.Arguments=Quote-NativeArgument (Join-Path $PSScriptRoot 'native-report-fixture.cjs')
    $receiver.StartInfo.UseShellExecute=$false;$receiver.StartInfo.CreateNoWindow=$true
    Assert-True ($receiver.Start()) 'Receiver did not start';$owned.receiverPid=$receiver.Id;Save-Json $journal $owned
    $port=Wait-Condition { (Receiver-State).port }
    $proof=Invoke-CheckedNative $node @((Join-Path $PSScriptRoot 'native-egress-probe.cjs'),[string]$port)
    $result.phases+=@(@{phase='outbound-denial';status='PASS';probe=($proof | ConvertFrom-Json);scopedRules=$programs.Count})
    # Real CLI without a machine-config preload, while fixed config is proven absent.
    $cli=New-Object Diagnostics.Process
    $cli.StartInfo.FileName=$node;$cli.StartInfo.Arguments=Quote-NativeArgument (Join-Path $Output 'bundle\app\index.js')
    $cli.StartInfo.WorkingDirectory='C:\'
    $cli.StartInfo.UseShellExecute=$false;$cli.StartInfo.RedirectStandardError=$true;$cli.StartInfo.RedirectStandardOutput=$true
    try {
        $null=$cli.Start();$cliError=$cli.StandardError.ReadToEndAsync();$cliOut=$cli.StandardOutput.ReadToEndAsync()
        if (-not $cli.WaitForExit(5000)) { $cli.Kill();throw 'No-config CLI did not exit' }
        Assert-True ($cli.ExitCode -ne 0 -and $cliError.GetAwaiter().GetResult().Contains('required option')) 'Real no-config CLI accepted'
    } finally { $cli.Dispose() }
    $result.phases+=@(@{phase='real-cli-no-config';status='PASS'})
    $json=@{key='fixture "key"';secret='fixture\secret$(literal)';vm_id='fixture-native';host=('localhost:'+$port)} | ConvertTo-Json
    $firstInstall=New-Object Diagnostics.Process
    $firstInstall.StartInfo.FileName=(Get-Process -Id $PID).Path
    $installArgs=@('-NoProfile','-NonInteractive','-File',(Join-Path $PSScriptRoot 'install-windows-bundle.ps1'),'-Apply','-ConfigStdin','-Bundle',(Join-Path $Output 'bundle'),'-ExpectedManifestSha256',$prepared.bundles.bundle,'-NssmExe',$wrapper)
    $firstInstall.StartInfo.Arguments=($installArgs | ForEach-Object { Quote-NativeArgument $_ }) -join ' '
    $firstInstall.StartInfo.UseShellExecute=$false;$firstInstall.StartInfo.RedirectStandardInput=$true
    $firstInstall.StartInfo.RedirectStandardOutput=$true;$firstInstall.StartInfo.RedirectStandardError=$true
    try {
        $null=$firstInstall.Start();$installOut=$firstInstall.StandardOutput.ReadToEndAsync();$installError=$firstInstall.StandardError.ReadToEndAsync()
        $firstInstall.StandardInput.WriteLine($json);$firstInstall.StandardInput.Close()
        if (-not $firstInstall.WaitForExit(90000)) { $firstInstall.Kill();throw 'First installer timed out' }
        Assert-True ($firstInstall.ExitCode -eq 0) 'First installer CLI failed'
    } finally { $firstInstall.Dispose() }
    $digest=(Get-FileHash -LiteralPath $config -Algorithm SHA256).Hash
    $runtime=Join-Path $product ('Agent\releases\'+$prepared.bundles.bundle+'\runtime\node.exe');$childId=Service-Child $runtime
    $null=Wait-Condition { (Receiver-State).count -ge 2 } 85
    Assert-True ((Service-Child $runtime) -eq $childId -and (Receiver-State).rejected -eq 0) 'Child/progress not stable'
    $interval=(Receiver-State).lastReportAt-(Receiver-State).firstReportAt
    Assert-True ($interval -ge 59000) 'Scheduled report interval was not observed'
    Assert-Acl $config $false;Assert-Acl $runtime $true;Assert-Acl (Split-Path $runtime) $true
    $result.phases+=@(@{phase='fresh-install-stable-child-periodic-report';status='PASS';reports=(Receiver-State).count;observedIntervalMs=$interval;configuredPeriodSeconds=60})
    Stop-Service -Name vmpoweragent;$null=Wait-Condition { -not (Get-Process -Id $childId -ErrorAction SilentlyContinue) }
    $count=(Receiver-State).count;Start-Service -Name vmpoweragent;$null=Wait-Condition { (Receiver-State).count -gt $count }
    $childId=Service-Child $runtime;$result.phases+=@(@{phase='stop-start';status='PASS'})
    $count=(Receiver-State).count
    $null=Install-AgentBundle (Join-Path $Output 'upgrade') $prepared.bundles.upgrade $wrapper $null
    $null=Wait-Condition { (Receiver-State).count -gt $count }
    Assert-True (-not (Get-Process -Id $childId -ErrorAction SilentlyContinue)) 'Old child survived upgrade'
    Assert-True ((Get-FileHash $config -Algorithm SHA256).Hash -eq $digest) 'Upgrade changed config'
    $runtime=Join-Path $product ('Agent\releases\'+$prepared.bundles.upgrade+'\runtime\node.exe');$null=Service-Child $runtime
    $result.phases+=@(@{phase='upgrade-preserves-config';status='PASS'})
    $count=(Receiver-State).count;$failed=$false
    try { $null=Install-AgentBundle (Join-Path $Output 'crashing') $prepared.bundles.crashing $wrapper $null } catch { $failed=$true }
    Assert-True $failed 'Crashing child incorrectly accepted'
    $null=Wait-Condition { (Receiver-State).count -gt $count };$null=Service-Child $runtime
    Assert-True ((Get-FileHash $config -Algorithm SHA256).Hash -eq $digest) 'Rollback changed config'
    $result.phases+=@(@{phase='real-crashing-child-rollback';status='PASS'})
    Stop-Service -Name vmpoweragent;Set-Service -Name vmpoweragent -StartupType Disabled
    $null=Install-AgentBundle (Join-Path $Output 'bundle') $prepared.bundles.bundle $wrapper '{}'
    $service=Get-CimInstance Win32_Service -Filter "Name='vmpoweragent'"
    Assert-True ($service.State -eq 'Stopped' -and $service.StartMode -eq 'Disabled') 'Stopped/disabled state changed'
    $result.phases+=@(@{phase='stopped-disabled-upgrade';status='PASS'})
    $null=Uninstall-AgentBundle
    $null=Wait-Condition { -not (Get-CimInstance Win32_Service -Filter "Name='vmpoweragent'") }
    Assert-True ((Get-FileHash $config -Algorithm SHA256).Hash -eq $digest) 'Uninstall changed config'
    Assert-True (Test-Path -LiteralPath $runtime) 'Uninstall removed rollback release'
    $result.phases+=@(@{phase='uninstall-preserves-config-and-releases';status='PASS'})
    # Recreate only the legacy NSSM contract with qualified modern fixture bytes.
    $pins=Get-Content -Raw (Join-Path $PSScriptRoot '../distribution/nssm-pins.json') | ConvertFrom-Json
    $retainedWrapper=Join-Path $product ('Agent\wrappers\'+$pins.x64Sha256+'\nssm.exe')
    $null=Invoke-CheckedNative $retainedWrapper @('install','vmpoweragent',$node)
    $null=Invoke-CheckedNative $retainedWrapper @('set','vmpoweragent','AppDirectory',$configDir)
    $null=Invoke-CheckedNative $retainedWrapper @('set','vmpoweragent','AppParameters',('"'+(Join-Path $Output 'bundle\app\index.js')+'"'))
    $count=(Receiver-State).count;Start-Service -Name vmpoweragent
    $null=Wait-Condition { (Receiver-State).count -gt $count };$legacyChild=Service-Child $node
    $count=(Receiver-State).count
    $null=Install-AgentBundle (Join-Path $Output 'upgrade') $prepared.bundles.upgrade $wrapper $null
    $null=Wait-Condition { (Receiver-State).count -gt $count };$null=Service-Child $runtime
    Assert-True (-not (Get-Process -Id $legacyChild -ErrorAction SilentlyContinue)) 'Legacy fixture child survived migration'
    Assert-True ((Get-FileHash $config -Algorithm SHA256).Hash -eq $digest) 'Legacy migration changed config'
    $null=Uninstall-AgentBundle
    $null=Wait-Condition { -not (Get-CimInstance Win32_Service -Filter "Name='vmpoweragent'") }
    $result.phases+=@(@{phase='synthetic-legacy-NSSM-migration';status='PASS';legacyPublishedBinary='UNRUN'})
    $result.status='PASS'
} finally {
    try { $result.cleanup=Cleanup-Owned } catch { $result.status='FAIL';$result.cleanup=@{status='FAIL'};throw }
    finally { Save-Json (Join-Path $Output 'evidence\lifecycle-windows.json') $result }
}
@{nativeLifecycle='PASS';phases=$result.phases.Count;cleanup=$result.cleanup.status} | ConvertTo-Json
