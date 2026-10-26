$ErrorActionPreference = 'Stop'
# Keep the existing TLS 1.2 support without disabling certificate validation.
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
$staging = Join-Path ([IO.Path]::GetTempPath()) ('vmpower-bootstrap-' + [Guid]::NewGuid().ToString())
$packageStream = $null
$sha256 = $null
$client = $null
$response = $null
$download = $null
$output = $null
try {
    $null = New-Item -ItemType Directory -Path $staging
    $package = Join-Path $staging 'vmpower-agent.nupkg'
    $request = [Net.WebRequest]::Create('https://vmpower.blob.core.windows.net/repo/dist/win32/vmpower-agent/vmpower-agent.nupkg')
    $request.AllowAutoRedirect = $false
    $response = $request.GetResponse()
    if ($response.StatusCode -ne [Net.HttpStatusCode]::OK) { throw 'VMPower package download did not return HTTP 200.' }
    $download = $response.GetResponseStream()
    $output = [IO.File]::Create($package)
    $buffer = New-Object byte[] 8192
    while (($count = $download.Read($buffer, 0, $buffer.Length)) -gt 0) { $output.Write($buffer, 0, $count) }
    $output.Dispose(); $output = $null
    # SHA256 works on the existing .NET runtime; no Get-FileHash/PowerShell 7 requirement.
    # Keep the read handle open through installation to deny writes/deletion after verification.
    $packageStream = [IO.File]::OpenRead($package)
    $sha256 = [Security.Cryptography.SHA256]::Create()
    $actualHash = [BitConverter]::ToString($sha256.ComputeHash($packageStream)).Replace('-', '').ToLowerInvariant()
    if ($actualHash -ne '1e72a1db993a3b013b8503e8cde2f0492a8bcee18539ee9e1056c2eb203893af') {
        throw 'VMPower package integrity verification failed; nothing was installed.'
    }

    # No installer/package execution occurs until the agent download is verified.
    Set-ExecutionPolicy Bypass -Scope Process -Force
    $client = New-Object System.Net.WebClient
    iex ($client.DownloadString('https://chocolatey.org/install.ps1'))
    # Resolve the package's existing NSSM dependency first. The agent itself must
    # come only from the verified local directory, never a competing remote feed.
    choco install nssm -y --source 'https://chocolatey.org/api/v2/'
    if (@(0, 1641, 3010) -notcontains $LASTEXITCODE) { throw 'NSSM dependency installation failed.' }
    $key = [string]$apiKey
    $secret = [string]$apiSecret
    $name = [string]$vmName
    choco install vmpower-agent --version 0.0.1 -y --source $staging -params "/API_KEY:'$key' /API_SECRET:'$secret' /VM_ID:'$name'"
    if (@(0, 1641, 3010) -notcontains $LASTEXITCODE) { throw 'VMPower package installation failed.' }
} finally {
    if ($null -ne $output) { $output.Dispose() }
    if ($null -ne $download) { $download.Dispose() }
    if ($null -ne $response) { $response.Close() }
    if ($null -ne $packageStream) { $packageStream.Dispose() }
    if ($null -ne $sha256) { $sha256.Dispose() }
    if ($null -ne $client) { $client.Dispose() }
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
}
