# Opt-in extension of the existing isolated Windows lifecycle fixture. Never run
# standalone: it reuses that fixture's receiver, firewall, journal and cleanup.
$script:lastPackageFailure=$null
function Get-OwnedChocolateyFailureLabels([string]$CapturedOutput) {
    # Output is a closed vocabulary only. Never return captured text, excerpts,
    # paths, matches, exception messages or unknown property values.
    $known=[ordered]@{
        'Unexpected package tools location; shim preserved'='SHIM_PACKAGE_PATH_UNEXPECTED'
        'Legacy shim root is unqualified; shim preserved'='SHIM_ROOT_UNQUALIFIED'
        'Unsafe shim parent; shim preserved'='SHIM_PARENT_UNSAFE'
        'Untrusted shim owner; shim preserved'='SHIM_OWNER_UNTRUSTED'
        'Untrusted shim write access; shim preserved'='SHIM_ACL_UNTRUSTED'
        'Unrecognized legacy shim file; shim preserved'='SHIM_FILE_UNRECOGNIZED'
        'Unqualified legacy shim bytes; shim preserved'='SHIM_HASH_UNQUALIFIED'
        'Shim changed during package operation; shim preserved'='SHIM_CHANGED_DURING_TRANSACTION'
        'Qualified legacy shim removal failed'='SHIM_REMOVAL_FAILED'
        'Reparse point in managed path'='MANAGED_PATH_REPARSE'
        'Managed file must have exactly one link'='MANAGED_FILE_LINKS'
        'Unqualified NSSM executable'='INSTALL_NSSM_UNQUALIFIED'
        'Retained NSSM mismatch'='INSTALL_NSSM_RETAINED_MISMATCH'
        'Bundle manifest hash mismatch'='INSTALL_BUNDLE_MANIFEST_MISMATCH'
        'Bundle file hash mismatch'='INSTALL_BUNDLE_FILE_MISMATCH'
        'Unexpected bundle files'='INSTALL_BUNDLE_MEMBERS_UNEXPECTED'
        'First install requires configuration JSON on stdin'='INSTALL_CONFIG_MISSING'
        'Invalid configuration JSON'='INSTALL_CONFIG_INVALID'
        'Native operation timed out'='INSTALL_NATIVE_TIMEOUT'
        'Native operation failed'='INSTALL_NATIVE_FAILED'
        'Service failed to start'='INSTALL_SERVICE_START_FAILED'
        'Service child exited during startup'='INSTALL_CHILD_EXITED'
        'Service child restarted during startup'='INSTALL_CHILD_RESTARTED'
        'Stable service child was not observed'='INSTALL_CHILD_NOT_STABLE'
        'Existing service uses a custom identity; preserve it for explicit migration qualification'='INSTALL_IDENTITY_UNSUPPORTED'
        'Existing service is not the NSSM contract'='INSTALL_SERVICE_CONTRACT_UNSUPPORTED'
        'Install failed; previous service restored, configuration and all releases retained'='INSTALL_RUNTIME_RESTORED'
        'Install failed and service rollback requires inspection; configuration and all releases retained'='INSTALL_ROLLBACK_FAILED'
    }
    $labels=@()
    foreach ($message in $known.Keys) {
        if ($CapturedOutput -and $CapturedOutput.Contains($message)) { $labels+=@($known[$message]) }
    }
    if ($labels.Count -eq 0) { return 'UNKNOWN' }
    return $labels
}

function Invoke-OwnedChocolatey([string[]]$Arguments) {
    # Package extraction, ACL work and failing-child recovery can exceed the
    # installer's individual 30-second native-operation limit. Bound the whole
    # package transaction separately; timeout is never rollback qualification.
    $script:packageRecoveryObserved=$false
    $script:lastPackageFailure=$null
    $operation=if ($Arguments[0] -in @('--version','list','pack','install','upgrade','uninstall')) {$Arguments[0]} else {'UNCLASSIFIED'}
    $process=New-Object Diagnostics.Process
    $process.StartInfo.FileName=$script:chocolatey
    $process.StartInfo.Arguments=($Arguments | ForEach-Object { Quote-NativeArgument $_ }) -join ' '
    $process.StartInfo.UseShellExecute=$false;$process.StartInfo.CreateNoWindow=$true
    $process.StartInfo.RedirectStandardOutput=$true;$process.StartInfo.RedirectStandardError=$true
    try {
        Assert-True ($process.Start()) 'Chocolatey did not start'
        $stdout=$process.StandardOutput.ReadToEndAsync();$stderr=$process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(180000)) {
            $script:packageTimedOut=$true
            $script:lastPackageFailure=@{classification='CHOCOLATEY_TIMEOUT';operation=$operation}
            $null=Invoke-CheckedNative 'taskkill.exe' @('/PID',[string]$process.Id,'/T','/F')
            throw 'Chocolatey transaction timed out; native qualification failed'
        }
        if ($process.ExitCode -ne 0) {
            # Distinguish the intended crashing-child rollback from download,
            # lock, command-line or package-validation failures. Keep raw logs private.
            $diagnosticLabels=@(Get-OwnedChocolateyFailureLabels ($stdout.GetAwaiter().GetResult()+"`n"+$stderr.GetAwaiter().GetResult()))
            $script:packageRecoveryObserved='INSTALL_RUNTIME_RESTORED' -cin $diagnosticLabels
            $script:lastPackageFailure=@{classification='CHOCOLATEY_EXIT_NONZERO';operation=$operation;exitCode=$process.ExitCode;installerRecoveryObserved=$script:packageRecoveryObserved;diagnosticLabels=$diagnosticLabels}
            throw 'Chocolatey transaction failed'
        }
        return $stdout.GetAwaiter().GetResult().TrimEnd([char[]]"`r`n")
    } finally { $process.Dispose() }
}

function Get-OwnedChocolateyVersion {
    $line=Invoke-OwnedChocolatey @('list','--exact','vmpower-agent','--limit-output')
    if ([string]::IsNullOrWhiteSpace($line)) { return $null }
    if ($line -notmatch '^vmpower-agent\|(0\.0\.[1234])$') { throw 'Unexpected package registration; preserve for inspection' }
    return $Matches[1]
}

function Remove-OwnedLegacyShim([hashtable]$Ownership,[bool]$RegistrationAbsent) {
    Assert-True $RegistrationAbsent 'Package registration must be absent before shim reconciliation'
    $expectedChoco=[IO.Path]::GetFullPath((Join-Path $env:ChocolateyInstall 'bin\choco.exe'))
    $expectedShim=[IO.Path]::GetFullPath((Join-Path $env:ChocolateyInstall 'bin\vmpower-agent.amd64.exe'))
    Assert-True ($Ownership.ContainsKey('chocolateyShimAbsent') -and $Ownership.chocolateyShimAbsent -eq $true -and
        $Ownership.chocolatey -ieq $expectedChoco -and $Ownership.chocolateyShim -ieq $expectedShim) 'Exact absent-before-fixture shim ownership required'
    Assert-NoReparsePoint $expectedShim
    # Never trust a writable bin directory or a replaceable Chocolatey root.
    $trusted=@('S-1-5-18','S-1-5-32-544',[Security.Principal.WindowsIdentity]::GetCurrent().User.Value)
    $writeRights=[Security.AccessControl.FileSystemRights]'Write,Delete,DeleteSubdirectoriesAndFiles,ChangePermissions,TakeOwnership'
    foreach ($parent in @($env:ChocolateyInstall,(Split-Path -Parent $expectedShim))) {
        Assert-True (Test-Path -LiteralPath $parent -PathType Container) 'Shim parent must be a directory'
        $acl=Get-Acl -LiteralPath $parent
        Assert-True ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -in $trusted) 'Untrusted shim parent owner'
        foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
            if ($rule.AccessControlType -eq 'Allow' -and ($rule.FileSystemRights -band $writeRights) -ne 0) {
                Assert-True ($rule.IdentityReference.Value -in $trusted) 'Untrusted write access to shim parent'
            }
        }
    }
    if (-not (Test-Path -LiteralPath $expectedShim)) { return @{status='ALREADY_ABSENT';packageRegistrationAbsent=$true} }
    $file=Get-Item -LiteralPath $expectedShim -Force
    Assert-True ($file -is [IO.FileInfo] -and -not $file.PSIsContainer) 'Owned shim must be a regular file'
    Assert-SingleLinkFile $expectedShim
    $receipt=@{status='REMOVED_OWNED_ORPHAN';packageRegistrationAbsent=$true;bytes=$file.Length;sha256=(Get-FileHash -LiteralPath $expectedShim -Algorithm SHA256).Hash.ToLowerInvariant()}
    Remove-Item -LiteralPath $expectedShim -Force
    Assert-True (-not (Test-Path -LiteralPath $expectedShim)) 'Owned legacy shim survived package cleanup'
    return $receipt
}

function Remove-OwnedChocolateyRegistration {
    if (-not $owned.ContainsKey('chocolatey') -or -not $owned.chocolatey) { return }
    $script:chocolatey=$owned.chocolatey
    $script:cleanupPhase='package-deregistration'
    $version=Get-OwnedChocolateyVersion
    if ($version) {
        # Cleanup must NEVER execute the retained 0.0.1 uninstall script.
        $null=Invoke-OwnedChocolatey @('uninstall','vmpower-agent',('--version='+$version),
            '--yes','--skip-automation-scripts','--skip-autouninstaller','--limit-output')
    }
    Assert-True (-not (Get-OwnedChocolateyVersion)) 'Owned package registration survived cleanup'
    $script:cleanupPhase='package-cache-removal'
    # Exact directories were proved absent before setting journal ownership.
    foreach ($directory in $owned.chocolateyDirectories) {
        Assert-NoReparsePoint $directory
        if (Test-Path -LiteralPath $directory) {
            foreach ($entry in Get-ChildItem -LiteralPath $directory -Recurse -Force) { Assert-NoReparsePoint $entry.FullName }
            Remove-Item -LiteralPath $directory -Recurse -Force
        }
        Assert-True (-not (Test-Path -LiteralPath $directory)) 'Owned package cache survived cleanup'
    }
    $script:cleanupPhase='owned-legacy-shim-reconciliation'
    return Remove-OwnedLegacyShim $owned $true
}

function Invoke-WindowsPackageOwnership([string]$LegacyPackages,[string]$ExpectedChocolateyVersion,[hashtable]$Evidence) {
    Assert-Hosted
    $Evidence.phase='package-preflight';$Evidence.status='FAIL';$Evidence.packages=@();$Evidence.families=@()
    $script:packageTimedOut=$false
    if ($ExpectedChocolateyVersion -notmatch '^2\.[0-9]+\.[0-9]+$') { throw 'Explicit reviewed Chocolatey 2.x version required' }
    $script:chocolatey=Join-Path $env:ChocolateyInstall 'bin\choco.exe'
    Assert-NoReparsePoint $script:chocolatey
    Assert-True ((Invoke-OwnedChocolatey @('--version')) -ceq $ExpectedChocolateyVersion) 'Chocolatey version differs from admission'
    Assert-True (-not (Get-OwnedChocolateyVersion)) 'Package already registered; not an empty fixture'
    $directories=@('lib\vmpower-agent','lib-bad\vmpower-agent','lib-bkp\vmpower-agent') | ForEach-Object { Join-Path $env:ChocolateyInstall $_ }
    foreach ($directory in $directories) { Assert-NoReparsePoint $directory;Assert-True (-not (Test-Path -LiteralPath $directory)) 'Package directory already exists' }
    $shim=Join-Path $env:ChocolateyInstall 'bin\vmpower-agent.amd64.exe'
    Assert-NoReparsePoint $shim;Assert-True (-not (Test-Path -LiteralPath $shim)) 'Legacy shim already exists'
    $owned.chocolatey=$script:chocolatey;$owned.chocolateyDirectories=@($directories);$owned.chocolateyShim=$shim;$owned.chocolateyShimAbsent=$true;Save-Json $journal $owned
    # Actual product proof must reject an unrelated regular file, even at the
    # right protected path and exact expected size. Never execute this sentinel.
    . (Join-Path $PSScriptRoot '../packages/win32/node24/tools/package-shim.ps1')
    $Evidence.phase='product-shim-ownership-refusal'
    [IO.File]::WriteAllBytes($shim,(New-Object byte[] 121856))
    $sentinelHash=(Get-FileHash -LiteralPath $shim -Algorithm SHA256).Hash
    $rejected=$false
    try { $null=Get-VmpowerPackageShimState (Join-Path $env:ChocolateyInstall 'lib\vmpower-agent\tools') } catch { $rejected=$true }
    Assert-True ($rejected -and (Test-Path -LiteralPath $shim) -and
        (Get-FileHash -LiteralPath $shim -Algorithm SHA256).Hash -ceq $sentinelHash) 'Product proof accepted or changed an unrelated shim'
    Remove-Item -LiteralPath $shim
    $Evidence.unrelatedShimRefusal='PASS'
    $packages=Join-Path $Output 'chocolatey-ownership';$null=New-Item -ItemType Directory -Path $packages
    $runtimePins=Get-Content -Raw (Join-Path $PSScriptRoot '../distribution/runtime-pins.json') | ConvertFrom-Json
    $nodeArchive=Join-Path $Output $runtimePins.targets.'win-x64'.archive
    $packedProof=@()
    # Successful ownership uses the unmodified product bundle; only the failing
    # successor is synthetic. Final release/version admission remains separate.
    foreach ($variant in @(@{name='bundle';source='bundle';version='0.0.2'},@{name='crashing';source='crashing';version='0.0.3'},
        @{name='shim-failure';source='upgrade';version='0.0.4'})) {
        $Evidence.phase='build-'+$variant.name
        $inputs=Join-Path $packages $variant.name
        $buildArguments=@((Join-Path $PSScriptRoot 'build-windows-package.py'),'--bundle',(Join-Path $Output $variant.source),
            '--expected-manifest-sha256',$prepared.bundles.($variant.source),'--node-archive',$nodeArchive,
            '--nssm-archive',(Join-Path $Output 'nssm.zip'),'--version',$variant.version,'--output',$inputs)
        $null=Invoke-CheckedNative (Get-Command python).Source $buildArguments
        if ($variant.name -eq 'shim-failure') {
            # Explicit synthetic package only: replace an absent shim after the
            # real installer activates a different content-addressed runtime.
            # Invoke the actual proof/removal code, which must refuse it and
            # trigger the existing SCM transaction plus Chocolatey rollback.
            $helperPath=Join-Path $inputs 'tools\package-shim.ps1'
            $helper=[IO.File]::ReadAllText($helperPath).Replace('function Remove-VmpowerPackageShim(', 'function Remove-VerifiedVmpowerPackageShim(')
            $helper+=@'

# SYNTHETIC FINALIZATION FAILURE FIXTURE; NEVER PUBLISH.
function Remove-VmpowerPackageShim([string]$ToolsDirectory,[hashtable]$Before) {
    $sentinel=Join-Path $env:ChocolateyInstall 'bin\vmpower-agent.amd64.exe'
    if (Test-Path -LiteralPath $sentinel) { throw 'Unexpected shim before synthetic replacement' }
    [IO.File]::WriteAllText($sentinel,'synthetic unrelated replacement after activation')
    Remove-VerifiedVmpowerPackageShim $ToolsDirectory $Before
}
'@
            [IO.File]::WriteAllText($helperPath,$helper)
        }
        $Evidence.phase='pack-'+$variant.name
        $null=Invoke-OwnedChocolatey @('pack',(Join-Path $inputs 'vmpower-agent.nuspec'),('--output-directory='+$packages),'--limit-output')
        $packed=Join-Path $packages ('vmpower-agent.'+$variant.version+'.nupkg')
        $Evidence.phase='verify-packed-'+$variant.name
        $verified=Invoke-CheckedNative (Get-Command python).Source @((Join-Path $PSScriptRoot 'verify-windows-package.py'),'--inputs',$inputs,'--package',$packed)
        $packedProof+=@($verified | ConvertFrom-Json)
        $Evidence.packages=@($packedProof)
    }
    $families=@(
        @{name='docs-windows.nupkg';sha='3b34023ffa2d05b54bdf10abfe9b3c515ab5dccf70af823407dcd1cb976a8956'},
        @{name='dashboard-windows.nupkg';sha='1e72a1db993a3b013b8503e8cde2f0492a8bcee18539ee9e1056c2eb203893af'})
    $proof=@()
    foreach ($family in $families) {
        $familyEvidence=@{family=$family.name;packageSha256=$family.sha;phases=@()};$Evidence.families+=@($familyEvidence)
        $Evidence.phase='legacy-registration';$Evidence.family=$family.name
        Assert-True (-not (Get-CimInstance Win32_Service -Filter "Name='vmpoweragent'")) 'Service must be absent between package cases'
        $legacy=Join-Path $LegacyPackages $family.name;Assert-NoReparsePoint $legacy
        Assert-True ((Get-FileHash -LiteralPath $legacy -Algorithm SHA256).Hash.ToLowerInvariant() -ceq $family.sha) 'Legacy package family hash mismatch'
        $localPackage=Join-Path $packages 'vmpower-agent.0.0.1.nupkg'
        Copy-Item -LiteralPath $legacy -Destination $localPackage
        # Actual package-manager registration of exact published bytes. Do not run
        # unsupported telemetry or its destructive installer; no dependency fetch.
        $null=Invoke-OwnedChocolatey @('install','vmpower-agent','--version=0.0.1',('--source='+$packages),
            '--yes','--skip-automation-scripts','--ignore-dependencies','--limit-output')
        Assert-True ((Get-OwnedChocolateyVersion) -ceq '0.0.1') 'Legacy ownership not registered'
        Assert-True (-not (Get-CimInstance Win32_Service -Filter "Name='vmpoweragent'")) 'Skipped legacy install created a service'
        Assert-True (Test-Path -LiteralPath $shim -PathType Leaf) 'Legacy automatic shim missing; migration control not exercised'
        $familyEvidence.phases+=@('legacy-registration-scripts-skipped')
        $Evidence.phase='synthetic-service-setup'
        # Reuse only qualified modern Node bytes for the legacy SCM contract.
        $null=Invoke-CheckedNative $wrapper @('install','vmpoweragent',$node)
        $null=Invoke-CheckedNative $wrapper @('set','vmpoweragent','AppDirectory',$configDir)
        $null=Invoke-CheckedNative $wrapper @('set','vmpoweragent','AppParameters',('"'+(Join-Path $Output 'bundle\app\index.js')+'"'))
        $disabled=$family.name -eq 'dashboard-windows.nupkg'
        if ($disabled) { Set-Service -Name vmpoweragent -StartupType Disabled }
        else {
            Set-Service -Name vmpoweragent -StartupType Manual
            $count=(Receiver-State).count;Start-Service -Name vmpoweragent
            $null=Wait-Condition { (Receiver-State).count -gt $count }
        }
        $configDigest=(Get-FileHash -LiteralPath $config -Algorithm SHA256).Hash
        $count=(Receiver-State).count
        $Evidence.phase='modern-package-upgrade'
        $null=Invoke-OwnedChocolatey @('upgrade','vmpower-agent','--version=0.0.2',('--source='+$packages),'--yes','--limit-output')
        Assert-True ((Get-OwnedChocolateyVersion) -ceq '0.0.2') 'New package did not replace legacy registration'
        # Product behavior must pass before any fixture reconciliation can run.
        Assert-True (-not (Test-Path -LiteralPath $shim)) 'Product upgrade left the obsolete legacy shim'
        $familyEvidence.phases+=@('product-removes-qualified-legacy-shim')
        $modern=Join-Path $product ('Agent\releases\'+$prepared.bundles.bundle+'\runtime\node.exe')
        $service=Get-CimInstance Win32_Service -Filter "Name='vmpoweragent'"
        if ($disabled) {
            Assert-True ($service.State -eq 'Stopped' -and $service.StartMode -eq 'Disabled') 'Package upgrade changed stopped/disabled state'
            Set-Service -Name vmpoweragent -StartupType Manual;Start-Service -Name vmpoweragent
        } else { Assert-True ($service.State -eq 'Running' -and $service.StartMode -eq 'Manual') 'Package upgrade changed active/manual state' }
        $null=Wait-Condition { (Receiver-State).count -gt $count };$null=Service-Child $modern
        Assert-True ((Get-FileHash -LiteralPath $config -Algorithm SHA256).Hash -ceq $configDigest) 'Chocolatey upgrade changed configuration'
        Assert-Acl $config $false
        $familyEvidence.phases+=@('modern-upgrade-state-config-acl');$Evidence.phase='crashing-package-rollback'
        $failed=$false;$count=(Receiver-State).count
        try { $null=Invoke-OwnedChocolatey @('upgrade','vmpower-agent','--version=0.0.3',('--source='+$packages),'--yes','--limit-output') } catch { $failed=$true }
        Assert-True (-not $script:packageTimedOut) 'Timed-out package is not a valid rollback control'
        Assert-True $failed 'Crashing package upgrade was accepted'
        Assert-True $script:packageRecoveryObserved 'Failure did not reach installer service recovery'
        $null=Wait-Condition { (Receiver-State).count -gt $count };$null=Service-Child $modern
        Assert-True ((Get-OwnedChocolateyVersion) -ceq '0.0.2') 'Failed package did not restore prior registration'
        Assert-True (-not (Test-Path -LiteralPath $shim)) 'Failed modern successor recreated a legacy shim'
        Assert-True ((Get-FileHash -LiteralPath $config -Algorithm SHA256).Hash -ceq $configDigest) 'Package rollback changed configuration'
        $familyEvidence.phases+=@('package-and-service-rollback')
        $Evidence.phase='post-activation-shim-failure-rollback'
        $priorService=Get-ServiceSnapshot 'vmpoweragent' $wrapper
        $count=(Receiver-State).count;$failed=$false
        try { $null=Invoke-OwnedChocolatey @('upgrade','vmpower-agent','--version=0.0.4',('--source='+$packages),'--yes','--limit-output') } catch { $failed=$true }
        Assert-True ($failed -and -not $script:packageTimedOut -and $script:packageRecoveryObserved) 'Finalization failure did not invoke service compensation'
        Assert-True ((Get-OwnedChocolateyVersion) -ceq '0.0.2') 'Finalization failure did not restore prior package registration'
        $restoredService=Get-ServiceSnapshot 'vmpoweragent' $wrapper
        foreach ($setting in @('ImagePath','Application','AppDirectory','AppParameters','Active','Start')) {
            Assert-True ($restoredService[$setting] -ceq $priorService[$setting]) ('Finalization did not restore service '+$setting)
        }
        $null=Wait-Condition { (Receiver-State).count -gt $count };$null=Service-Child $modern
        Assert-True ((Get-FileHash -LiteralPath $config -Algorithm SHA256).Hash -ceq $configDigest) 'Finalization rollback changed configuration'
        Assert-True ((Test-Path -LiteralPath $shim) -and [IO.File]::ReadAllText($shim) -ceq 'synthetic unrelated replacement after activation') 'Finalization rollback changed unrelated shim'
        $familyEvidence.phases+=@('post-activation-shim-failure-restores-package-service')
        # Remove only the exact sentinel this adverse control created, after its
        # preservation and service/package compensation assertions succeeded.
        Remove-Item -LiteralPath $shim
        $Evidence.phase='modern-package-uninstall'
        # Run the NEW uninstaller normally, proving old destructive script is gone.
        $null=Invoke-OwnedChocolatey @('uninstall','vmpower-agent','--yes','--limit-output')
        Assert-True (-not (Get-OwnedChocolateyVersion)) 'Modern package registration remains'
        Assert-True (-not (Test-Path -LiteralPath $shim)) 'Product uninstall requires fixture shim rescue'
        $null=Wait-Condition { -not (Get-CimInstance Win32_Service -Filter "Name='vmpoweragent'") }
        Assert-True ((Get-FileHash -LiteralPath $config -Algorithm SHA256).Hash -ceq $configDigest) 'Modern package uninstall deleted configuration'
        Assert-True (Test-Path -LiteralPath $modern) 'Package uninstall deleted rollback runtime'
        $familyEvidence.phases+=@('modern-uninstall-retains-config-runtime')
        $proof+=@(@{family=$family.name;packageSha256=$family.sha;registrationUpgrade='PASS';previousState=$(if ($disabled) {'Stopped/Disabled'} else {'Running/Manual'});serviceRollback='PASS';uninstallPreservesConfig='PASS';legacyPublishedExecutable='UNRUN'})
        $Evidence.phase='family-package-cleanup'
        $familyEvidence.shimCleanup=Remove-OwnedChocolateyRegistration
        $familyEvidence.phases+=@('package-and-shim-cleanup')
        Remove-Item -LiteralPath $localPackage
    }
    $Evidence.phase='complete';$Evidence.status='PASS';$Evidence.chocolateyVersion=$ExpectedChocolateyVersion
    return @{phase='real-chocolatey-ownership-synthetic-service';status='PASS';chocolateyVersion=$ExpectedChocolateyVersion;packages=$packedProof;families=$proof;oldRuntimeCompatibility='UNQUALIFIED'}
}
