$dotaCandidates = @(
  'C:\Program Files (x86)\Steam\steamapps\common\dota 2 beta\game\bin\win64\dota2.exe',
  'C:\Program Files (x86)\Steam\steamapps\common\dota 2 beta\game\dota.exe',
  'D:\Steam\steamapps\common\dota 2 beta\game\bin\win64\dota2.exe',
  'D:\SteamLibrary\steamapps\common\dota 2 beta\game\bin\win64\dota2.exe'
)
$found = @()
foreach ($p in $dotaCandidates) { if (Test-Path -LiteralPath $p) { $found += $p } }
# also scan Steam libraries from registry if possible
$steamPath = $null
try {
    $steamPath = (Get-ItemProperty 'HKCU:\Software\Valve\Steam' -ErrorAction Stop).SteamPath
} catch {}
if ($steamPath) {
    $libFile = Join-Path $steamPath 'steamapps\libraryfolders.vdf'
    if (Test-Path $libFile) {
        Get-Content $libFile | Select-String '"path"' | ForEach-Object {
            $line = $_.Line
            if ($line -match '"path"\s+"([^"]+)"') {
                $lp = $Matches[1] -replace '\\\\','\'
                $exe = Join-Path $lp 'steamapps\common\dota 2 beta\game\bin\win64\dota2.exe'
                if (Test-Path $exe) { $found += $exe }
            }
        }
    }
}
$found = $found | Select-Object -Unique
if ($found) {
    $found | ForEach-Object {
        $d = Split-Path -Qualifier $_
        $di = Get-PSDrive ($d.TrimEnd(':'))
        Write-Host ('Dota found: ' + $_)
        Write-Host ('Drive: ' + $d + ' Free: ' + [math]::Round($di.Free/1GB,2) + ' GB')
    }
} else {
    Write-Host 'Dota 2 executable was not found in common Steam paths.'
}
if ($steamPath) { Write-Host ('Steam root: ' + $steamPath) }