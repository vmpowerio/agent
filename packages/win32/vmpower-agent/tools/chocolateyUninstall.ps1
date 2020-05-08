
$ConfFileName = "C:\ProgramData\vmpower\vmpower.conf"
$ExeFileName = "C:\ProgramData\vmpower\vmpower.exe"

# Uninstall service
nssm stop vmpoweragent
nssm remove vmpoweragent confirm

# Remove config file
if (Test-Path $ConfFileName) 
{
  Remove-Item $ConfFileName
}

# Remove exe
if (Test-Path $ExeFileName)
{
  Remove-Item $ExeFileName
}
