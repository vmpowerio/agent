# Product ownership is different from disposable-fixture absent-before ownership.
# These exact shims were created by Chocolatey 2.7.4 from the two pinned 0.0.1
# packages on the default root in native run 40437980932, job 121971195778.
# No candidate shim is executed, including with --shimgen-noop/help.
function Get-VmpowerPackageShimState([string]$ToolsDirectory) {
    $root=[IO.Path]::GetFullPath($env:ChocolateyInstall).TrimEnd('\')
    $expectedTools=Join-Path $root 'lib\vmpower-agent\tools'
    if ([IO.Path]::GetFullPath($ToolsDirectory).TrimEnd('\') -ine $expectedTools) {
        throw 'Unexpected package tools location; shim preserved'
    }
    $shim=Join-Path $root 'bin\vmpower-agent.amd64.exe'
    Assert-NoReparsePoint $shim
    if (-not (Test-Path -LiteralPath $shim)) { return @{status='ABSENT';path=$shim} }
    if ($root -ine 'C:\ProgramData\chocolatey') {
        throw 'Legacy shim root is unqualified; shim preserved'
    }
    $trusted=@('S-1-5-18','S-1-5-32-544',[Security.Principal.WindowsIdentity]::GetCurrent().User.Value)
    $writeRights=[Security.AccessControl.FileSystemRights]'Write,Delete,DeleteSubdirectoriesAndFiles,ChangePermissions,TakeOwnership'
    foreach ($entry in @($root,(Split-Path -Parent $shim),$shim)) {
        $item=Get-Item -LiteralPath $entry -Force
        if ($entry -ne $shim -and -not $item.PSIsContainer) { throw 'Unsafe shim parent; shim preserved' }
        $acl=Get-Acl -LiteralPath $entry
        if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted) { throw 'Untrusted shim owner; shim preserved' }
        foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
            if ($rule.AccessControlType -eq 'Allow' -and ($rule.FileSystemRights -band $writeRights) -ne 0 -and
                $rule.IdentityReference.Value -notin $trusted) { throw 'Untrusted shim write access; shim preserved' }
        }
    }
    $file=Get-Item -LiteralPath $shim -Force
    if ($file -isnot [IO.FileInfo] -or $file.PSIsContainer -or $file.Length -ne 121856) {
        throw 'Unrecognized legacy shim file; shim preserved'
    }
    Assert-SingleLinkFile $shim
    $sha=(Get-FileHash -LiteralPath $shim -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($sha -cnotin @('7d7dad5068622170edda0e1fa6ecfe906ed39cc8727186b404c4c1add1482795',
        '00e537c680019d69bba7a2906c1338f600059ca27c40de93f710faf25f0015b6')) {
        throw 'Unqualified legacy shim bytes; shim preserved'
    }
    return @{status='QUALIFIED_LEGACY';path=$shim;sha256=$sha;bytes=$file.Length}
}

function Remove-VmpowerPackageShim([string]$ToolsDirectory,[hashtable]$Before) {
    # Recheck after the service transaction; a changed/new file is not ours to delete.
    $current=Get-VmpowerPackageShimState $ToolsDirectory
    if ($current.path -ine $Before.path -or $current.status -cne $Before.status -or
        ($current.status -eq 'QUALIFIED_LEGACY' -and $current.sha256 -cne $Before.sha256)) {
        throw 'Shim changed during package operation; shim preserved'
    }
    if ($current.status -eq 'ABSENT') { return }
    Remove-Item -LiteralPath $current.path -Force
    if (Test-Path -LiteralPath $current.path) { throw 'Qualified legacy shim removal failed' }
}
