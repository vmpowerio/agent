# Runs the actual bootstrap against controlled HTTP/installer boundaries.
# Package bytes are only read/hashed; no package, bundled executable or lifecycle runs.
param([string]$PackageFixture)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Windows PowerShell required' }
$installer=Join-Path $PSScriptRoot '../ps-deploy.ps1'
$origin='https://vmpower.blob.core.windows.net/repo/dist/win32/vmpower-agent/vmpower-agent.nupkg'
$expected='1e72a1db993a3b013b8503e8cde2f0492a8bcee18539ee9e1056c2eb203893af'
$owned=Join-Path ([IO.Path]::GetTempPath()) ('vmpower bootstrap tests '+[Guid]::NewGuid().ToString())
function Assert-True($Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Hash-Bytes([byte[]]$Bytes) {
    $hash=[Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hash.ComputeHash($Bytes)).Replace('-','').ToLowerInvariant() }
    finally { $hash.Dispose() }
}
# Embedded exact pre-fix source, executed only under the same controlled boundaries.
$baselineText=@'
# Install chocolatey
Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://chocolatey.org/install.ps1'))

# Install agent
(New-Object System.Net.WebClient).DownloadFile( `
"http://data.vmpower.com/repo/dist/win32/vmpower-agent/vmpower-agent.nupkg", `
"vmpower-agent.nupkg")
$key = [string]$apiKey
$secret = [string]$apiSecret
$name = [string]$vmName
choco install vmpower-agent -y --source "'.;https://chocolatey.org/api/v2/'" -params ` "/API_KEY:'$key' /API_SECRET:'$secret' /VM_ID:'$name'"
'@
$baselineBytes=[Text.Encoding]::UTF8.GetBytes($baselineText+"`n  `n")
Assert-True ((Hash-Bytes $baselineBytes) -ceq 'adb69eeec9268236021360b3b65285f07a7285977d82c53f145e0146f58bbdd2') 'Baseline source changed'

function Invoke-Case([string]$Mode,[string]$ScriptPath,[bool]$Legacy=$false) {
    [BootstrapHttp]::Mode=$Mode
    [BootstrapHttp]::Events.Clear()
    $script:legacyCase=$Legacy
    $script:case=@{Events=[BootstrapHttp]::Events;Failed=$false;Locked=$false;Destination=$null}
    $apiKey='fixture key'; $apiSecret='fixture secret'; $vmName='fixture vm'
    $global:LASTEXITCODE=0
    function New-Object {
        param([string]$TypeName,$ArgumentList)
        if ($TypeName -eq 'byte[]') { return ,(Microsoft.PowerShell.Utility\New-Object -TypeName 'byte[]' -ArgumentList $ArgumentList) }
        if ($TypeName -ne 'System.Net.WebClient') { throw 'Unexpected object boundary' }
        $client=[pscustomobject]@{}
        $client | Add-Member ScriptMethod DownloadFile {
            param($Uri,$Destination)
            Assert-True $script:legacyCase 'Candidate used uncontrolled WebClient download'
            $script:case.Events.Add('download')
            $bytes=[byte[]][BootstrapHttp]::Bytes.Clone()
            $bytes[123]=$bytes[123] -bxor 1
            [IO.File]::WriteAllBytes($Destination,$bytes)
        }
        $client | Add-Member ScriptMethod DownloadString {
            param($Uri)
            $script:case.Events.Add('bootstrap-download')
            Assert-True ($Uri -ceq 'https://chocolatey.org/install.ps1') 'Unexpected bootstrap origin'
            if ([BootstrapHttp]::Mode -eq 'bootstrap-failure') { throw 'Synthetic bootstrap failure' }
            return '# Synthetic bootstrap: never evaluated'
        }
        $client | Add-Member ScriptMethod Dispose { }
        return $client
    }
    function Set-ExecutionPolicy { param($ExecutionPolicy,$Scope,[switch]$Force); $script:case.Events.Add('execution-policy') }
    function Invoke-Expression { param($Command); $script:case.Events.Add('bootstrap-execute') }
    Set-Alias -Name iex -Value Invoke-Expression -Scope Local
    function choco {
        $script:case.Events.Add('choco-'+$args[1])
        if ($Legacy) { return }
        Assert-True ($args[0] -ceq 'install') 'Unexpected Chocolatey operation'
        if ($args[1] -eq 'nssm') {
            Assert-True (($args -join '|') -ceq 'install|nssm|-y|--source|https://chocolatey.org/api/v2/') 'Dependency contract changed'
            $global:LASTEXITCODE=if ([BootstrapHttp]::Mode -eq 'dependency-failure') {1} else {0}
            return
        }
        Assert-True ($args[1] -ceq 'vmpower-agent') 'Unexpected package'
        $source=$args[[Array]::IndexOf($args,'--source')+1]
        Assert-True ($source -ceq $staging) 'Agent source is not exclusively verified local staging'
        Assert-True (($args[[Array]::IndexOf($args,'--version')+1]) -ceq '0.0.1') 'Published version changed'
        Assert-True (($args[[Array]::IndexOf($args,'-params')+1]) -ceq "/API_KEY:'fixture key' /API_SECRET:'fixture secret' /VM_ID:'fixture vm'") 'Legacy parameters changed'
        $localPackage=Join-Path $source 'vmpower-agent.nupkg'
        Assert-True ((Hash-Bytes ([IO.File]::ReadAllBytes($localPackage))) -ceq $expected) 'Different bytes reached Chocolatey'
        try { $write=[IO.File]::OpenWrite($localPackage); $write.Dispose() }
        catch { $script:case.Locked=$true }
        Assert-True $script:case.Locked 'Verified package was writable before consumption'
        $global:LASTEXITCODE=if ([BootstrapHttp]::Mode -eq 'agent-failure') {1} elseif ([BootstrapHttp]::Mode -eq 'reboot-success') {3010} else {0}
    }
    function Start-Service { $script:case.Events.Add('service'); throw 'Unexpected service action' }
    function Stop-Service { $script:case.Events.Add('service'); throw 'Unexpected service action' }
    function Remove-Service { $script:case.Events.Add('service'); throw 'Unexpected service action' }
    function Invoke-WebRequest { throw 'Unexpected network boundary' }
    function Invoke-RestMethod { throw 'Unexpected network boundary' }
    try { . $ScriptPath } catch { $script:case.Failed=$true }
    if (-not $Legacy) {
        Assert-True (-not (Test-Path -LiteralPath $staging)) 'Owned staging not cleaned'
    }
    return $script:case
}
$originalLocation=Get-Location
$originalProtocol=[Net.ServicePointManager]::SecurityProtocol
try {
    $null=New-Item -ItemType Directory -Path $owned
    if (-not $PackageFixture) {
        $PackageFixture=Join-Path $owned 'published-fixture.nupkg'
        # One public read, normal certificates, no redirects, no package execution.
        [Net.ServicePointManager]::SecurityProtocol=$originalProtocol -bor 3072
        Invoke-WebRequest -Uri $origin -OutFile $PackageFixture -UseBasicParsing -MaximumRedirection 0 -TimeoutSec 60
    }
    $fixtureBytes=[IO.File]::ReadAllBytes($PackageFixture)
    Assert-True ($fixtureBytes.Length -eq 10731882) 'Published fixture size changed'
    Assert-True ((Hash-Bytes $fixtureBytes) -ceq $expected) 'Published fixture digest changed'
    # Test-only .NET request factory: all later HTTP(S) is controlled, no listener.
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Net;
using System.Collections.Generic;
public class BootstrapHttp : IWebRequestCreate {
    public static byte[] Bytes;
    public static string Mode;
    public static List<string> Events = new List<string>();
    public WebRequest Create(Uri uri) {
        Events.Add("request");
        if (uri.AbsoluteUri != "https://vmpower.blob.core.windows.net/repo/dist/win32/vmpower-agent/vmpower-agent.nupkg")
            throw new InvalidOperationException("Noncanonical or non-HTTPS origin");
        return new BootstrapRequest();
    }
}
public class BootstrapRequest : WebRequest {
    public bool AllowAutoRedirect { get; set; }
    public BootstrapRequest() { AllowAutoRedirect = true; }
    public override WebResponse GetResponse() {
        if (AllowAutoRedirect) throw new InvalidOperationException("Redirects not disabled");
        BootstrapHttp.Events.Add("download");
        if (BootstrapHttp.Mode == "download-failure") throw new WebException("Synthetic network failure");
        if (BootstrapHttp.Mode == "certificate-failure") throw new WebException("Synthetic certificate failure", WebExceptionStatus.TrustFailure);
        return new BootstrapResponse();
    }
}
public class BootstrapResponse : WebResponse {
    public HttpStatusCode StatusCode {
        get { return BootstrapHttp.Mode == "redirect" ? HttpStatusCode.Redirect :
                     BootstrapHttp.Mode == "http-failure" ? HttpStatusCode.NotFound : HttpStatusCode.OK; }
    }
    public override Stream GetResponseStream() {
        BootstrapHttp.Events.Add("body");
        byte[] bytes=(byte[])BootstrapHttp.Bytes.Clone();
        if (BootstrapHttp.Mode == "tamper") bytes[123] ^= 1;
        if (BootstrapHttp.Mode == "truncated") Array.Resize(ref bytes, 512);
        if (BootstrapHttp.Mode == "empty") bytes=new byte[0];
        if (BootstrapHttp.Mode == "body-failure") throw new IOException("Synthetic read failure");
        return new MemoryStream(bytes, false);
    }
    public override void Close() { BootstrapHttp.Events.Add("response-close"); }
}
'@
    [BootstrapHttp]::Bytes=$fixtureBytes
    $factory=New-Object BootstrapHttp
    Assert-True ([Net.WebRequest]::RegisterPrefix('https://',$factory)) 'HTTPS boundary registration failed'
    Assert-True ([Net.WebRequest]::RegisterPrefix('http://',$factory)) 'HTTP boundary registration failed'
    Set-Location $owned
    $baselineScript=Join-Path $owned 'baseline.ps1'
    [IO.File]::WriteAllBytes($baselineScript,$baselineBytes)
    $callerFile=Join-Path $owned 'vmpower-agent.nupkg'
    [IO.File]::WriteAllText($callerFile,'stale-caller-file')
    $passed=0
    foreach ($mode in @('success','reboot-success','tamper','truncated','empty','download-failure','certificate-failure','redirect','http-failure','body-failure','bootstrap-failure','dependency-failure','agent-failure')) {
        $result=Invoke-Case $mode $installer
        $expectedFailure=$mode -notin @('success','reboot-success')
        Assert-True ($result.Failed -eq $expectedFailure) ('Unexpected outcome: '+$mode)
        Assert-True ($result.Events -notcontains 'service') ('Existing service touched: '+$mode)
        Assert-True ($result.Events[0] -eq 'request') ('Execution preceded verification: '+$mode)
        if ($mode -in @('tamper','truncated','empty','download-failure','certificate-failure','redirect','http-failure','body-failure')) {
            Assert-True (@($result.Events | Where-Object {$_ -match '^(bootstrap|choco|execution-policy)'}).Count -eq 0) ('Unverified package caused mutation: '+$mode)
            if ($mode -in @('redirect','http-failure')) { Assert-True ($result.Events -notcontains 'body') 'Rejected HTTP response body consumed' }
        } elseif ($mode -eq 'bootstrap-failure') {
            Assert-True (@($result.Events | Where-Object {$_ -like 'choco-*'}).Count -eq 0) 'Bootstrap failure reached package install'
        } elseif ($mode -eq 'dependency-failure') {
            Assert-True ($result.Events -notcontains 'choco-vmpower-agent') 'Failed dependency reached agent install'
        } else {
            Assert-True ($result.Events -contains 'choco-vmpower-agent') ('Agent boundary missing: '+$mode)
            Assert-True $result.Locked ('Package lock not exercised: '+$mode)
        }
        Assert-True ([IO.File]::ReadAllText($callerFile) -ceq 'stale-caller-file') 'Caller file overwritten'
        $passed++
    }
    $baseline=Invoke-Case 'tamper' $baselineScript $true
    Assert-True (-not $baseline.Failed) 'Baseline did not reach controlled vulnerable path'
    Assert-True ($baseline.Events -contains 'choco-vmpower-agent') 'Baseline no longer accepts tampered package'
    Assert-True ($baseline.Events.IndexOf('bootstrap-execute') -lt $baseline.Events.IndexOf('download')) 'Baseline ordering did not reproduce'
    @{status='PASS';cases=$passed;baselineTamperAccepted=$true;packageSha256=$expected;packageExecuted=$false;serviceOperations=0} | ConvertTo-Json -Compress
} finally {
    Set-Location $originalLocation
    [Net.ServicePointManager]::SecurityProtocol=$originalProtocol
    if (Test-Path -LiteralPath $owned) { Remove-Item -LiteralPath $owned -Recurse -Force }
}
