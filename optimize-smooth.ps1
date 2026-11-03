#Requires -RunAsAdministrator
$ErrorActionPreference = 'Continue'
$log = New-Object System.Collections.Generic.List[string]
function Log([string]$m){ $script:log.Add($m); Write-Host $m }

# Activate highest performance plan available
$ultimate='9284f403-691f-48af-8c81-c1538bddc461'
$high='8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c'
$activated=$false
foreach ($g in @($ultimate,$high)) {
    try {
        powercfg /setactive $g 2>$null
        if ($LASTEXITCODE -eq 0) { $activated=$true; Log ("Active power plan switched: " + $g); break }
    } catch {}
}
if (-not $activated) { Log 'WARNING: could not switch power plan (may need manual selection).' }

# Keep CPU at fixed high frequency more aggressively on AC power
try {
    powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMIN 100 | Out-Null
    powercfg /setdcvalueindex SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMIN 100 | Out-Null
    powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMAX 100 | Out-Null
    powercfg /setdcvalueindex SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMAX 100 | Out-Null
    powercfg /setactive SCHEME_CURRENT | Out-Null
    Log 'Processor min/max state set to 100% (AC and DC).'
} catch { Log ('Processor state tweak failed: ' + $_.Exception.Message) }

# Disable idle/dynamic behaviors where exposed by the plan
# (Core parking is not exposed on this plan; if available later, add here.)
try {
    powercfg /attributes SUB_PROCESSOR 0cc5b647-c1df-4637-891a-dec35c318583 -ATTRIB_HIDE 2>$null | Out-Null
} catch {}

# Apply timer resolution? leave to application/games
Log 'Smoothness power tuning done. Reboot recommended for clean state.'
$log | Set-Content -Path 'C:\jarvis\optimize-smooth-result.txt' -Encoding UTF8
'optimize-smooth completed'