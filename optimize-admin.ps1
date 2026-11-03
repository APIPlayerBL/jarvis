#Requires -RunAsAdministrator
$ErrorActionPreference = 'Continue'
$log = New-Object System.Collections.Generic.List[string]
function Log([string]$m){ $script:log.Add($m); Write-Host $m }

# Ensure admin
$wi = [Security.Principal.WindowsIdentity]::GetCurrent()
$wp = New-Object Security.Principal.WindowsPrincipal($wi)
if (-not $wp.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error 'Run this script as Administrator.'; exit 1
}

# HAGS
try {
  $k='HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers'
  Set-ItemProperty -Path $k -Name 'HwSchMode' -Value 2 -Type DWord -Force
  Log 'HAGS enabled (HwSchMode=2).'
} catch { Log ('HAGS failed: ' + $_.Exception.Message) }

# Power throttling off
try {
  $k='HKLM:\SYSTEM\CurrentControlSet\Control\Power\PowerThrottling'
  if (-not (Test-Path $k)) { New-Item -Path $k -Force | Out-Null }
  Set-ItemProperty -Path $k -Name 'PowerThrottlingOff' -Value 1 -Type DWord -Force
  Log 'Power throttling disabled.'
} catch { Log ('PowerThrottling failed: ' + $_.Exception.Message) }

# Multimedia / game priority
try {
  $base='HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile'
  Set-ItemProperty -Path $base -Name 'SystemResponsiveness' -Value 0 -Type DWord -Force
  Set-ItemProperty -Path $base -Name 'NetworkThrottlingIndex' -Value 0xffffffff -Type DWord -Force
  $games=Join-Path $base 'Tasks\Games'
  if (-not (Test-Path $games)) { New-Item -Path $games -Force | Out-Null }
  Set-ItemProperty -Path $games -Name 'Scheduling Category' -Value 'High' -Type String -Force
  Set-ItemProperty -Path $games -Name 'SFIO Priority' -Value 'High' -Type String -Force
  Set-ItemProperty -Path $games -Name 'Background Only' -Value 'False' -Type String -Force
  Set-ItemProperty -Path $games -Name 'Priority' -Value 6 -Type DWord -Force
  Log 'Multimedia SystemProfile game priority enabled.'
} catch { Log ('Multimedia tweak failed: ' + $_.Exception.Message) }

# Disable Game DVR policy (machine)
try {
  $p='HKLM:\SOFTWARE\Policies\Microsoft\Windows\GameDVR'
  if (-not (Test-Path $p)) { New-Item -Path $p -Force | Out-Null }
  Set-ItemProperty -Path $p -Name 'AllowGameDVR' -Value 0 -Type DWord -Force
  Log 'Machine Game DVR policy disabled.'
} catch { Log ('GameDVR policy failed: ' + $_.Exception.Message) }

# Services to disable for maximum gaming (safe-ish)
$svcList = @(
  'Spooler','WSearch','SysMain','WerSvc','DiagTrack',
  'XblAuthManager','XblGameSave','XboxNetApiSvc',
  'MapsBroker','RemoteRegistry','TabletInputService','PhoneSvc',
  'Fax','SSDPSRV','XboxGipSvc','BDESVC'
)
# keep XboxGipSvc? skip disabling controller support; remove from list
$svcList = $svcList | Where-Object { $_ -ne 'XboxGipSvc' }

# If printers installed, do not disable Spooler
$printers = Get-Printer -ErrorAction SilentlyContinue
if ($printers) { $svcList = $svcList | Where-Object { $_ -ne 'Spooler' } }

foreach ($s in $svcList) {
  $svc = Get-Service -Name $s -ErrorAction SilentlyContinue
  if (-not $svc) { continue }
  try {
    Stop-Service -Name $s -Force -ErrorAction SilentlyContinue
    Set-Service -Name $s -StartupType Disabled -ErrorAction Stop
    Log ("Disabled service: " + $s)
  } catch {
    Log ("Service failed " + $s + ": " + $_.Exception.Message)
  }
}

# Telemetry scheduled tasks
$tasks = @(
  '\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser',
  '\Microsoft\Windows\Application Experience\ProgramDataUpdater',
  '\Microsoft\Windows\Autochk\Proxy',
  '\Microsoft\Windows\Customer Experience Improvement Program\Consolidator',
  '\Microsoft\Windows\Customer Experience Improvement Program\UsbCeip'
)
foreach ($t in $tasks) {
  try { Disable-ScheduledTask -TaskName $t -ErrorAction Stop | Out-Null; Log ("Disabled task: " + $t) }
  catch { } 
}

# Defender exclusions (admin)
$gamePaths = @('C:\Program Files (x86)\Steam','C:\Program Files\Epic Games','C:\Riot Games','C:\Games') | Where-Object { Test-Path $_ }
foreach ($p in $gamePaths) {
  try { Add-MpPreference -ExclusionPath $p -ErrorAction Stop; Log ("Defender exclusion: " + $p) }
  catch { Log ("Defender exclusion failed: " + $p + ' -> ' + $_.Exception.Message) }
}

# Hibernate off to free space
try { powercfg /hibernate off 2>$null; Log 'Hibernate disabled.' } catch {}

# Component cleanup
try { dism /online /Cleanup-Image /StartComponentCleanup 2>$null | Out-Null; Log 'Component cleanup invoked.' } catch {}

$log | Set-Content -Path 'C:\jarvis\optimize-admin-result.txt' -Encoding UTF8
'Admin-level optimization done.'