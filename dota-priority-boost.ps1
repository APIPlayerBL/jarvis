$ErrorActionPreference = 'SilentlyContinue'
Write-Host 'Dota 2 priority booster started. It will boost dota2.exe when launched. Close this window to stop.'
while ($true) {
    $game = Get-Process -Name dota2 -ErrorAction SilentlyContinue
    if ($game) {
        foreach ($p in $game) {
            try {
                if ($p.PriorityClass -ne 'High') {
                    $p.PriorityClass = 'High'
                    Write-Host ("Boosted dota2 PID " + $p.Id + " to High priority.")
                }
            } catch {
                Write-Host ("Could not boost PID " + $p.Id + ": " + $_.Exception.Message)
            }
        }
    }
    Start-Sleep -Seconds 3
}