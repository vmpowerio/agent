$pp = Get-PackageParameters
$toolsDir   = "$(Split-Path -parent $MyInvocation.MyCommand.Definition)"

New-Item "C:\ProgramData\vmpower" -ItemType Directory -ea 0
Copy-Item "$toolsDir\vmpower-template.conf" -Destination "C:\ProgramData\vmpower\vmpower.conf"

# Apply VMPower account configuration
(Get-Content -path "C:\ProgramData\vmpower\vmpower.conf" -Raw) -replace '<API_KEY>',$pp['API_KEY'] | Set-Content -Path  "C:\ProgramData\vmpower\vmpower.conf"
(Get-Content -path "C:\ProgramData\vmpower\vmpower.conf" -Raw) -replace '<API_SECRET>',$pp['API_SECRET'] | Set-Content -Path  "C:\ProgramData\vmpower\vmpower.conf"
(Get-Content -path "C:\ProgramData\vmpower\vmpower.conf" -Raw) -replace '<VM_ID>',$pp['VM_ID'] | Set-Content -Path  "C:\ProgramData\vmpower\vmpower.conf"

# Copy agent executable
Copy-Item "$toolsDir\vmpower-agent.amd64.exe" -Destination "C:\ProgramData\vmpower\vmpower.exe"

# Install agent as service
nssm install vmpoweragent C:\ProgramData\vmpower\vmpower.exe
nssm set vmpoweragent Start SERVICE_AUTO_START
nssm start vmpoweragent
