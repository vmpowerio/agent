# Opt-in extension of the existing isolated Windows lifecycle fixture. Never run
# standalone: it reuses that fixture's receiver, firewall, journal and cleanup.
function Invoke-OwnedChocolatey([string[]]$Arguments) {
    # Package extraction, ACL work and failing-child recovery can exceed the
    # installer's individual 30-second native-operation limit. Bound the whole
    # package transaction separately; timeout is never rollback qualification.
    $script:packageRecoveryObserved=$false
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
            $null=Invoke-CheckedNative 'taskkill.exe' @('/PID',[string]$process.Id,'/T','/F')
            throw 'Chocolatey transaction timed out; native qualification failed'
        }
        if ($process.ExitCode -ne 0) {
            # Distinguish the intended crashing-child rollback from download,
            # lock, command-line or package-validation failures. Keep raw logs private.
            $script:packageRecoveryObserved=($stdout.GetAwaiter().GetResult()+$stderr.GetAwaiter().GetResult()).Contains('Install failed; previous service restored, configuration and all releases retained')
            throw 'Chocolatey transaction failed'
        }
        return $stdout.GetAwaiter().GetResult().TrimEnd([char[]]"`r`n")
    } finally { $process.Dispose() }
}

function Get-OwnedChocolateyVersion {
    $line=Invoke-OwnedChocolatey @('list','--exact','vmpower-agent','--limit-output')
    if ([string]::IsNullOrWhiteSpace($line)) { return $null }
    if ($line -notmatch '^vmpower-agent\|(0\.0\.[123])$') { throw 'Unexpected package registration; preserve for inspection' }
    return $Matches[1]
}

function Remove-OwnedChocolateyRegistration {
    if (-not $owned.ContainsKey('chocolatey') -or -not $owned.chocolatey) { return }
    $script:chocolatey=$owned.chocolatey
    $version=Get-OwnedChocolateyVersion
    if ($version) {
        # Cleanup must NEVER execute the retained 0.0.1 uninstall script.
        $null=Invoke-OwnedChocolatey @('uninstall','vmpower-agent',('--version='+$version),
            '--yes','--skip-automation-scripts','--skip-autouninstaller','--limit-output')
    }
    Assert-True (-not (Get-OwnedChocolateyVersion)) 'Owned package registration survived cleanup'
    # Exact directories were proved absent before setting journal ownership.
    foreach ($directory in $owned.chocolateyDirectories) {
        Assert-NoReparsePoint $directory
        if (Test-Path -LiteralPath $directory) {
            foreach ($entry in Get-ChildItem -LiteralPath $directory -Recurse -Force) { Assert-NoReparsePoint $entry.FullName }
            Remove-Item -LiteralPath $directory -Recurse -Force
        }
        Assert-True (-not (Test-Path -LiteralPath $directory)) 'Owned package cache survived cleanup'
    }
    Assert-True (-not (Test-Path -LiteralPath $owned.chocolateyShim)) 'Owned legacy shim survived package cleanup'
}

function Invoke-WindowsPackageOwnership([string]$LegacyPackages,[string]$ExpectedChocolateyVersion) {
    Assert-Hosted
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
    $owned.chocolatey=$script:chocolatey;$owned.chocolateyDirectories=@($directories);$owned.chocolateyShim=$shim;Save-Json $journal $owned
    $packages=Join-Path $Output 'chocolatey-ownership';$null=New-Item -ItemType Directory -Path $packages
    $runtimePins=Get-Content -Raw (Join-Path $PSScriptRoot '../distribution/runtime-pins.json') | ConvertFrom-Json
    $nodeArchive=Join-Path $Output $runtimePins.targets.'win-x64'.archive
    $packedProof=@()
    # Successful ownership uses the unmodified product bundle; only the failing
    # successor is synthetic. Final release/version admission remains separate.
    foreach ($variant in @(@{name='bundle';version='0.0.2'},@{name='crashing';version='0.0.3'})) {
        $inputs=Join-Path $packages $variant.name
        $buildArguments=@((Join-Path $PSScriptRoot 'build-windows-package.py'),'--bundle',(Join-Path $Output $variant.name),
            '--expected-manifest-sha256',$prepared.bundles.($variant.name),'--node-archive',$nodeArchive,
            '--nssm-archive',(Join-Path $Output 'nssm.zip'),'--version',$variant.version,'--output',$inputs)
        $null=Invoke-CheckedNative (Get-Command python).Source $buildArguments
        $null=Invoke-OwnedChocolatey @('pack',(Join-Path $inputs 'vmpower-agent.nuspec'),('--output-directory='+$packages),'--limit-output')
        $packed=Join-Path $packages ('vmpower-agent.'+$variant.version+'.nupkg')
        $verified=Invoke-CheckedNative (Get-Command python).Source @((Join-Path $PSScriptRoot 'verify-windows-package.py'),'--inputs',$inputs,'--package',$packed)
        $packedProof+=@($verified | ConvertFrom-Json)
    }
    $families=@(
        @{name='docs-windows.nupkg';sha='3b34023ffa2d05b54bdf10abfe9b3c515ab5dccf70af823407dcd1cb976a8956'},
        @{name='dashboard-windows.nupkg';sha='1e72a1db993a3b013b8503e8cde2f0492a8bcee18539ee9e1056c2eb203893af'})
    $proof=@()
    foreach ($family in $families) {
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
        $null=Invoke-OwnedChocolatey @('upgrade','vmpower-agent','--version=0.0.2',('--source='+$packages),'--yes','--limit-output')
        Assert-True ((Get-OwnedChocolateyVersion) -ceq '0.0.2') 'New package did not replace legacy registration'
        $modern=Join-Path $product ('Agent\releases\'+$prepared.bundles.bundle+'\runtime\node.exe')
        $service=Get-CimInstance Win32_Service -Filter "Name='vmpoweragent'"
        if ($disabled) {
            Assert-True ($service.State -eq 'Stopped' -and $service.StartMode -eq 'Disabled') 'Package upgrade changed stopped/disabled state'
            Set-Service -Name vmpoweragent -StartupType Manual;Start-Service -Name vmpoweragent
        } else { Assert-True ($service.State -eq 'Running' -and $service.StartMode -eq 'Manual') 'Package upgrade changed active/manual state' }
        $null=Wait-Condition { (Receiver-State).count -gt $count };$null=Service-Child $modern
        Assert-True ((Get-FileHash -LiteralPath $config -Algorithm SHA256).Hash -ceq $configDigest) 'Chocolatey upgrade changed configuration'
        Assert-Acl $config $false
        $failed=$false;$count=(Receiver-State).count
        try { $null=Invoke-OwnedChocolatey @('upgrade','vmpower-agent','--version=0.0.3',('--source='+$packages),'--yes','--limit-output') } catch { $failed=$true }
        Assert-True (-not $script:packageTimedOut) 'Timed-out package is not a valid rollback control'
        Assert-True $failed 'Crashing package upgrade was accepted'
        Assert-True $script:packageRecoveryObserved 'Failure did not reach installer service recovery'
        $null=Wait-Condition { (Receiver-State).count -gt $count };$null=Service-Child $modern
        Assert-True ((Get-OwnedChocolateyVersion) -ceq '0.0.2') 'Failed package did not restore prior registration'
        Assert-True ((Get-FileHash -LiteralPath $config -Algorithm SHA256).Hash -ceq $configDigest) 'Package rollback changed configuration'
        # Run the NEW uninstaller normally, proving old destructive script is gone.
        $null=Invoke-OwnedChocolatey @('uninstall','vmpower-agent','--yes','--limit-output')
        Assert-True (-not (Get-OwnedChocolateyVersion)) 'Modern package registration remains'
        $null=Wait-Condition { -not (Get-CimInstance Win32_Service -Filter "Name='vmpoweragent'") }
        Assert-True ((Get-FileHash -LiteralPath $config -Algorithm SHA256).Hash -ceq $configDigest) 'Modern package uninstall deleted configuration'
        Assert-True (Test-Path -LiteralPath $modern) 'Package uninstall deleted rollback runtime'
        $proof+=@(@{family=$family.name;packageSha256=$family.sha;registrationUpgrade='PASS';previousState=$(if ($disabled) {'Stopped/Disabled'} else {'Running/Manual'});serviceRollback='PASS';uninstallPreservesConfig='PASS';legacyPublishedExecutable='UNRUN'})
        Remove-OwnedChocolateyRegistration
        Remove-Item -LiteralPath $localPackage
    }
    return @{phase='real-chocolatey-ownership-synthetic-service';status='PASS';chocolateyVersion=$ExpectedChocolateyVersion;packages=$packedProof;families=$proof;oldRuntimeCompatibility='UNQUALIFIED'}
}
