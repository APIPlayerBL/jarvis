#Requires -RunAsAdministrator
$ErrorActionPreference = 'Continue'
$log = New-Object System.Collections.Generic.List[string]
function Log([string]$m){ $script:log.Add($m); Write-Host $m }

# Disable USB selective suspend (AC and battery)
try {
    powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0 2>$null
    powercfg /setdcvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0 2>$null
    powercfg /setactive SCHEME_CURRENT 2>$null
    Log 'USB selective suspend disabled.'
} catch { Log ('USB tweak failed: ' + $_.Exception.Message) }

# Reduce interrupt overhead: disable power throttling already done; ensure high plan
try {
    powercfg /setactive 9284f403-691f-48af-8c81-c1538bddc461 2>$null
    Log 'Ultimate Performance plan requested.'
} catch { Log 'Ultimate Performance plan not available or could not be activated.' }

# Optional processor idle disabling is hardware/BIOS dependent; leave for BIOS PBO/C-states.
Log 'Latency-oriented system tweaks done. Reboot recommended.'
$log | Set-Content -Path 'C:\jarvis\optimize-latency-result.txt' -Encoding UTF8
'optimize-latency completed'