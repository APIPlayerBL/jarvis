$ErrorActionPreference = 'Continue'
$log = New-Object System.Collections.Generic.List[string]
function Log([string]$m){ $script:log.Add($m); Write-Host $m }

# 1. Power plan: prefer Ultimate Performance, then High Performance
try {
  powercfg /setactive 9284f403-691f-48af-8c81-c1538bddc461 2>$null
  Log 'Power plan set to Ultimate Performance (if available).'
} catch {
  try {
    powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
    Log 'Power plan set to High Performance.'
  } catch { Log ('Power plan change failed: ' + $_.Exception.Message) }
}

# 2. Disable Game DVR / Game Bar / background capture
try {
  $gamedvr = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\GameDVR'
  if (-not (Test-Path $gamedvr)) { New-Item -Path $gamedvr -Force | Out-Null }
  Set-ItemProperty -Path $gamedvr -Name 'AppCaptureEnabled' -Value 0 -Type DWord -Force

  $gamebar = 'HKCU:\Software\Microsoft\GameBar'
  if (-not (Test-Path $gamebar)) { New-Item -Path $gamebar -Force | Out-Null }
  Set-ItemProperty -Path $gamebar -Name 'ShowStartupPanel' -Value 0 -Type DWord -Force
  Set-ItemProperty -Path $gamebar -Name 'GameDVR_Enabled' -Value 0 -Type DWord -Force
  Set-ItemProperty -Path $gamebar -Name 'UseNexusForGameBarEnabled' -Value 0 -Type DWord -Force
  Set-ItemProperty -Path $gamebar -Name 'AutoGameModeEnabled' -Value 1 -Type DWord -Force

  $gc = 'HKCU:\System\GameConfigStore'
  if (-not (Test-Path $gc)) { New-Item -Path $gc -Force | Out-Null }
  Set-ItemProperty -Path $gc -Name 'GameDVR_FSEBehaviorMode' -Value 2 -Type DWord -Force
  Set-ItemProperty -Path $gc -Name 'GameDVR_HonorUserFSEBehaviorMode' -Value 1 -Type DWord -Force

  $policy = 'HKCU:\Software\Policies\Microsoft\Windows\GameDVR'
  if (-not (Test-Path $policy)) { New-Item -Path $policy -Force | Out-Null }
  Set-ItemProperty -Path $policy -Name 'AllowGameDVR' -Value 0 -Type DWord -Force
  Log 'Game DVR / capture disabled; Game Mode enabled.'
} catch { Log ('Game DVR tweak failed: ' + $_.Exception.Message) }

# 3. Visual effects/performance UI
try {
  $ve = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects'
  if (-not (Test-Path $ve)) { New-Item -Path $ve -Force | Out-Null }
  Set-ItemProperty -Path $ve -Name 'VisualFXSetting' -Value 2 -Type DWord -Force

  $personalize = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
  if (Test-Path $personalize) { Set-ItemProperty -Path $personalize -Name 'EnableTransparency' -Value 0 -Type DWord -Force }
  Log 'Visual effects set to performance and transparency disabled.'
} catch { Log ('Visual effects tweak failed: ' + $_.Exception.Message) }

# 4. Disable transparency/animation via desktop
try {
  Set-ItemProperty -Path 'HKCU:\Control Panel\Desktop' -Name 'MenuShowDelay' -Value '0' -Type String -Force
  Set-ItemProperty -Path 'HKCU:\Control Panel\Desktop' -Name 'UserPreferencesMask' -Value ([byte[]](144,18,3,128,16,0,0,0)) -Type Binary -Force -ErrorAction SilentlyContinue
} catch {}

# 5. Try Defender exclusions (may need admin)
$gamePaths = @('C:\Program Files (x86)\Steam','C:\Program Files\Epic Games','C:\Games') | Where-Object { Test-Path $_ }
foreach ($p in $gamePaths) {
  try {
    Add-MpPreference -ExclusionPath $p -ErrorAction Stop
    Log ('Defender exclusion added: ' + $p)
  } catch {
    Log ('Defender exclusion failed (needs admin): ' + $p + ' -> ' + $_.Exception.Message)
  }
}

$log | Set-Content -Path 'C:\jarvis\optimize-user-result.txt' -Encoding UTF8
'User-level optimization done.'