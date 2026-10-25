param(
    [ValidateSet('Install','Uninstall')][string]$Action = 'Install',
    [string]$Bundle,
    [string]$ExpectedManifestSha256,
    [string]$NssmExe,
    [switch]$ConfigStdin,
    [switch]$Apply
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Quote-NativeArgument([string]$Value) {
    if ($Value.Contains([char]0) -or $Value.Contains("`r") -or $Value.Contains("`n")) { throw 'Invalid native argument' }
    # Windows CRT quoting: preserve inner quotes and trailing backslashes.
    return '"' + ([regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1')) + '"'
}

function Invoke-CheckedNative([string]$Exe, [string[]]$Arguments) {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $Exe
    $info.Arguments = ($Arguments | ForEach-Object { Quote-NativeArgument $_ }) -join ' '
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    try {
        if (-not $process.Start()) { throw 'Native operation failed' }
        $output = $process.StandardOutput.ReadToEndAsync()
        $errors = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(30000)) { $process.Kill(); throw 'Native operation timed out' }
        if ($process.ExitCode -ne 0) { throw 'Native operation failed' }
        return $output.GetAwaiter().GetResult().TrimEnd([char[]]"`r`n")
    } finally { $process.Dispose() }
}

function Assert-NoReparsePoint([string]$Path) {
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            if ((Get-Item -Force -LiteralPath $current).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point in managed path' }
        }
        $parent = [IO.Directory]::GetParent($current)
        $current = if ($parent) { $parent.FullName } else { $null }
    }
}

function Set-ProtectedAcl([string]$Path, [bool]$Directory, [bool]$UsersRead = $false) {
    Assert-NoReparsePoint $Path
    $acl = if ($Directory) { New-Object Security.AccessControl.DirectorySecurity } else { New-Object Security.AccessControl.FileSecurity }
    $acl.SetAccessRuleProtection($true, $false)
    $admin = New-Object Security.Principal.SecurityIdentifier 'S-1-5-32-544'
    $acl.SetOwner($admin)
    $inherit = if ($Directory) { [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit' } else { [Security.AccessControl.InheritanceFlags]::None }
    foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
        $identity = New-Object Security.Principal.SecurityIdentifier $sid
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', $inherit, 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    if ($UsersRead) {
        $identity = New-Object Security.Principal.SecurityIdentifier 'S-1-5-32-545'
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'ReadAndExecute', $inherit, 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Assert-ConfigJson([string]$Json) {
    if ([string]::IsNullOrWhiteSpace($Json)) { throw 'First install requires configuration JSON on stdin' }
    if ($Json.Length -gt 65536) { throw 'Configuration exceeds supported size' }
    try { $value = ConvertFrom-Json -InputObject $Json } catch { throw 'Invalid configuration JSON' }
    foreach ($name in @('key','secret','vm_id')) {
        $field = $value.PSObject.Properties[$name]
        if (-not $field -or $field.Value -isnot [string] -or [string]::IsNullOrWhiteSpace($field.Value)) { throw 'Configuration requires nonempty key, secret and vm_id' }
    }
    $hostField = $value.PSObject.Properties['host']
    if ($hostField -and ($hostField.Value -isnot [string] -or [string]::IsNullOrWhiteSpace($hostField.Value))) { throw 'Invalid host configuration' }
    return $value
}

function Assert-SingleLinkFile([string]$Path) {
    Assert-NoReparsePoint $Path
    if (-not ('VMPower.FileLinks' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
namespace VMPower {
  public static class FileLinks {
    [StructLayout(LayoutKind.Sequential)] public struct Info {
      public uint Attributes, CreationLow, CreationHigh, AccessLow, AccessHigh,
        WriteLow, WriteHigh, Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
    }
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool GetFileInformationByHandle(SafeFileHandle file, out Info info);
  }
}
'@
    }
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        $info=New-Object VMPower.FileLinks+Info
        if (-not [VMPower.FileLinks]::GetFileInformationByHandle($stream.SafeFileHandle,[ref]$info) -or $info.Links -ne 1) { throw 'Managed file must have exactly one link' }
    } finally { $stream.Dispose() }
}

function Ensure-Config([string]$Path, [string]$IncomingJson) {
    Assert-NoReparsePoint $Path
    $directory = Split-Path -Parent $Path
    if (Test-Path -LiteralPath $Path) {
        Assert-SingleLinkFile $Path
        $null = Assert-ConfigJson ([IO.File]::ReadAllText($Path))
        Set-ProtectedAcl $directory $true
        Set-ProtectedAcl $Path $false
        return 'retained'
    }
    if ([string]::IsNullOrWhiteSpace($IncomingJson)) { throw 'First install requires configuration JSON on stdin' }
    $value = Assert-ConfigJson $IncomingJson
    if (-not (Test-Path -LiteralPath $directory)) { $null = New-Item -ItemType Directory -Path $directory }
    Set-ProtectedAcl $directory $true
    $temporary = Join-Path $directory ([Guid]::NewGuid().ToString() + '.tmp')
    try {
        [IO.File]::WriteAllText($temporary, ($value | ConvertTo-Json -Depth 10), (New-Object Text.UTF8Encoding $false))
        Set-ProtectedAcl $temporary $false
        # Move fails if another actor created the target; never overwrite config.
        [IO.File]::Move($temporary, $Path)
    } finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary } }
    return 'created'
}

function Assert-Bundle([string]$Path, [string]$Expected) {
    Assert-NoReparsePoint $Path
    if ($Expected -notmatch '^[a-f0-9]{64}$') { throw 'Qualified manifest SHA256 required' }
    $manifestPath = Join-Path $Path 'bundle-manifest.json'
    if ((Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Expected) { throw 'Bundle manifest hash mismatch' }
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    if ($manifest.target -ne 'win-x64' -or $manifest.nodeVersion -ne '24.22.1') { throw 'Expected qualified Windows x64 runtime bundle' }
    $files = @(Get-ChildItem -LiteralPath $Path -Recurse -Force -File)
    foreach ($entry in Get-ChildItem -LiteralPath $Path -Recurse -Force) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Bundle reparse point' }
    }
    $expectedFiles = @($manifest.files.PSObject.Properties)
    if ($files.Count -ne $expectedFiles.Count + 1) { throw 'Unexpected bundle files' }
    foreach ($entry in $expectedFiles) {
        if ($entry.Name -match '(^/|\\|(^|/)\.\.(/|$)|:)' -or $entry.Name -eq 'bundle-manifest.json') { throw 'Unsafe manifest member' }
        $file = Join-Path $Path ($entry.Name.Replace('/', [IO.Path]::DirectorySeparatorChar))
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.Value) { throw 'Bundle file hash mismatch' }
    }
    return $manifest
}

function Get-ServiceSnapshot([string]$Name, [string]$Nssm) {
    $service = Get-CimInstance Win32_Service -Filter ("Name='" + $Name + "'")
    if (-not $service) { return $null }
    if ($service.StartName -ne 'LocalSystem') { throw 'Existing service uses a custom identity; preserve it for explicit migration qualification' }
    if ($service.PathName -notmatch '(?i)(^|[\\"])nssm\.exe"?$') { throw 'Existing service is not the NSSM contract' }
    return @{ ImagePath=$service.PathName; Active=($service.State -eq 'Running');
        Application=(Invoke-CheckedNative $Nssm @('get',$Name,'Application'));
        AppDirectory=(Invoke-CheckedNative $Nssm @('get',$Name,'AppDirectory'));
        AppParameters=(Invoke-CheckedNative $Nssm @('get',$Name,'AppParameters')) }
}

function Get-ManagedPaths {
    # The legacy installer uses C:\ProgramData; setup-args reads \ProgramData
    # on the service working directory's drive. Do not silently relocate secrets.
    if ([IO.Path]::GetPathRoot($env:ProgramFiles) -ine 'C:\') { throw 'Non-C system drive requires separate configuration-reader qualification' }
    return @{Prefix=(Join-Path $env:ProgramFiles 'VMPower\Agent');Config='C:\ProgramData\vmpower\vmpower.conf'}
}

function Install-AgentBundle([string]$Source, [string]$Expected, [string]$Wrapper, [string]$IncomingJson) {
    $pins = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot '../distribution/nssm-pins.json') | ConvertFrom-Json
    Assert-NoReparsePoint $Wrapper
    if ((Get-FileHash -LiteralPath $Wrapper -Algorithm SHA256).Hash.ToLowerInvariant() -ne $pins.x64Sha256) { throw 'Unqualified NSSM executable' }
    $null = Assert-Bundle $Source $Expected
    $serviceName = 'vmpoweragent'
    $locations=Get-ManagedPaths
    $prefix=$locations.Prefix;$config=$locations.Config
    Assert-NoReparsePoint $prefix
    Assert-NoReparsePoint $config
    # Validate before stopping or changing the service. Existing bytes always win.
    if (Test-Path -LiteralPath $config) { Assert-SingleLinkFile $config; $null = Assert-ConfigJson ([IO.File]::ReadAllText($config)) }
    else { $null = Assert-ConfigJson $IncomingJson }
    $productRoot=Split-Path -Parent $prefix
    if (-not (Test-Path -LiteralPath $productRoot)) { $null=New-Item -ItemType Directory -Path $productRoot }
    Set-ProtectedAcl $productRoot $true $true
    if (-not (Test-Path -LiteralPath $prefix)) { $null = New-Item -ItemType Directory -Path $prefix }
    Set-ProtectedAcl $prefix $true $true
    $releases = Join-Path $prefix 'releases';$release = Join-Path $releases $Expected
    if (-not (Test-Path -LiteralPath $releases)) { $null=New-Item -ItemType Directory -Path $releases }
    Set-ProtectedAcl $releases $true $true
    if (-not (Test-Path -LiteralPath $release)) {
        $staging = Join-Path $releases ([Guid]::NewGuid().ToString())
        try { Copy-Item -LiteralPath $Source -Destination $staging -Recurse; $null=Assert-Bundle $staging $Expected; [IO.Directory]::Move($staging,$release) }
        finally { if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force } }
    }
    $null=Assert-Bundle $release $Expected
    # Remove source ACL inheritance; only admins/SYSTEM can modify executable bytes.
    foreach ($entry in Get-ChildItem -LiteralPath $release -Recurse -Force) { Set-ProtectedAcl $entry.FullName $entry.PSIsContainer $true }
    Set-ProtectedAcl $release $true $true
    $null=Assert-Bundle $release $Expected
    $wrapperDir = Join-Path $prefix ('wrappers\' + $pins.x64Sha256)
    $wrapperRoot=Split-Path -Parent $wrapperDir
    Assert-NoReparsePoint $wrapperDir
    if (-not (Test-Path -LiteralPath $wrapperRoot)) { $null=New-Item -ItemType Directory -Path $wrapperRoot }
    Set-ProtectedAcl $wrapperRoot $true $true
    if (-not (Test-Path -LiteralPath $wrapperDir)) { $null=New-Item -ItemType Directory -Path $wrapperDir -Force }
    Set-ProtectedAcl $wrapperDir $true $true
    $installedWrapper = Join-Path $wrapperDir 'nssm.exe'
    if (-not (Test-Path -LiteralPath $installedWrapper)) { Copy-Item -LiteralPath $Wrapper -Destination $installedWrapper }
    Set-ProtectedAcl $installedWrapper $false $true
    if ((Get-FileHash -LiteralPath $installedWrapper -Algorithm SHA256).Hash.ToLowerInvariant() -ne $pins.x64Sha256) { throw 'Retained NSSM mismatch' }
    # Never execute the caller-supplied wrapper path: copy, protect and hash first.
    $previous = Get-ServiceSnapshot $serviceName $installedWrapper
    $configState = Ensure-Config $config $IncomingJson
    $node = Join-Path $release 'runtime\node.exe';$app = Join-Path $release 'app\index.js'
    $created=$false
    try {
        if ($previous -and $previous.Active) { $null=Invoke-CheckedNative $installedWrapper @('stop',$serviceName) }
        if (-not $previous) {
            $null=Invoke-CheckedNative $installedWrapper @('install',$serviceName,$node)
            $created=$true
            $null=Invoke-CheckedNative $installedWrapper @('set',$serviceName,'Start','SERVICE_AUTO_START')
        } else {
            $null=Invoke-CheckedNative 'sc.exe' @('config',$serviceName,'binPath=',('"'+$installedWrapper+'"'))
            $null=Invoke-CheckedNative $installedWrapper @('set',$serviceName,'Application',$node)
        }
        $null=Invoke-CheckedNative $installedWrapper @('set',$serviceName,'AppDirectory',$release)
        $null=Invoke-CheckedNative $installedWrapper @('set',$serviceName,'AppParameters',('"'+$app+'"'))
        if (-not $previous -or $previous.Active) {
            $null=Invoke-CheckedNative $installedWrapper @('start',$serviceName)
            if ((Get-Service -Name $serviceName).Status -ne 'Running') { throw 'Service failed to start' }
        }
    } catch {
        try {
            $current=Get-Service -Name $serviceName -ErrorAction SilentlyContinue
            if ($current -and $current.Status -ne 'Stopped') { $null=Invoke-CheckedNative $installedWrapper @('stop',$serviceName) }
            if ($created) { $null=Invoke-CheckedNative $installedWrapper @('remove',$serviceName,'confirm') }
            elseif ($previous) {
                foreach ($setting in @('Application','AppDirectory','AppParameters')) { $null=Invoke-CheckedNative $installedWrapper @('set',$serviceName,$setting,$previous[$setting]) }
                $null=Invoke-CheckedNative 'sc.exe' @('config',$serviceName,'binPath=',$previous.ImagePath)
                if ($previous.Active) { Start-Service -Name $serviceName }
            }
        } catch { throw 'Install failed and service rollback requires inspection; configuration and all releases retained' }
        throw 'Install failed; previous service restored, configuration and all releases retained'
    }
    return @{state='installed';configuration=$configState;release=$Expected;service=$serviceName}
}

function Uninstall-AgentBundle {
    $prefix=(Get-ManagedPaths).Prefix
    $service=Get-CimInstance Win32_Service -Filter "Name='vmpoweragent'"
    if (-not $service) { throw 'Service not found; no files changed' }
    $pins=Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot '../distribution/nssm-pins.json') | ConvertFrom-Json
    $wrapper=Join-Path $prefix ('wrappers\'+$pins.x64Sha256+'\nssm.exe')
    Assert-NoReparsePoint $wrapper
    if ($service.PathName.Trim('"') -ne $wrapper -or (Get-FileHash -LiteralPath $wrapper -Algorithm SHA256).Hash.ToLowerInvariant() -ne $pins.x64Sha256) { throw 'Only this installer managed service can be removed' }
    if ($service.State -ne 'Stopped') { $null=Invoke-CheckedNative $wrapper @('stop','vmpoweragent') }
    $null=Invoke-CheckedNative $wrapper @('remove','vmpoweragent','confirm')
    return @{state='uninstalled';configuration='preserved';releases='preserved';wrapper='preserved'}
}

if ($MyInvocation.InvocationName -ne '.') {
    if (-not $Apply) { throw 'No changes made. Explicit -Apply required on a qualified Windows host.' }
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess) { throw 'Native Windows x64 process required' }
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    $principal=New-Object Security.Principal.WindowsPrincipal $identity
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator required' }
    if ($Action -eq 'Install') {
        $json=if ($ConfigStdin) { [Console]::In.ReadToEnd() } else { $null }
        Install-AgentBundle $Bundle $ExpectedManifestSha256 $NssmExe $json | ConvertTo-Json
    } else { Uninstall-AgentBundle | ConvertTo-Json }
}
