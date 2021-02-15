# Powershell
  (New-Object System.Net.WebClient).DownloadFile( `
  "http://data.vmpower.com/repo/dist/win32/vmpower-agent/vmpower-agent.nupkg", `
  "vmpower-agent.nupkg")
  $key = [string]$apiKey
  $secret = [string]$apiSecret
  $name = [string]$vmName
  (choco install vmpower-agent --source "'.;https://chocolatey.org/api/v2/'" -params `
  "/API_KEY:'$key' `
  /API_SECRET:'$secret' `
  /VM_ID:'$name'")
  