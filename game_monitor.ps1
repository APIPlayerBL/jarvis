$csvPath = 'C:\jarvis\game_monitor.csv'
$stopPath = 'C:\jarvis\monitor.stop'
if (Test-Path $stopPath) { Remove-Item -LiteralPath $stopPath -Force -ErrorAction SilentlyContinue }
if (Test-Path $csvPath) { Remove-Item -LiteralPath $csvPath -Force -ErrorAction SilentlyContinue }

$header = 'Timestamp,CPU_Total_Percent,RAM_Used_GB,RAM_Total_GB,GPU_Util_Percent,GPU_Temp_C,GPU_VRAM_Used_MB,GPU_VRAM_Total_MB,CPU_ThermalZone_C,TopCPUProcesses,TopRAMProcesses'
Add-Content -LiteralPath $csvPath -Value $header -Encoding UTF8

$smi = $null
try { $smi = (Get-Command nvidia-smi -ErrorAction Stop).Source } catch { $smi = $null }
$inv = [System.Globalization.CultureInfo]::InvariantCulture
function Fmt($v) {
    if ($null -eq $v -or $v -eq '') { return '' }
    try { return ([double]$v).ToString('0.00', $inv) } catch { return ([string]$v).Replace(',', '.') }
}

if ($smi) { Write-Host ("NVIDIA-SMI found: " + $smi) } else { Write-Host "NVIDIA-SMI not found; GPU metrics will be empty." }

$count = 0
while (-not (Test-Path $stopPath)) {
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

    $cpu = ''
    try {
        $cpuSample = Get-Counter '\Processor(_Total)\% Processor Time' -SampleInterval 1 -MaxSamples 1 -ErrorAction Stop
        $cpu = Fmt $cpuSample.CounterSamples[0].CookedValue
    } catch {}

    $ramUsed = ''
    $ramTotal = ''
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        $ramTotal = Fmt ($os.TotalVisibleMemorySize / 1MB)
        $ramUsed = Fmt (($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / 1MB)
    } catch {}

    $gpuUtil = ''
    $gpuTemp = ''
    $vramUsed = ''
    $vramTotal = ''
    if ($smi) {
        try {
            $raw = & $smi --query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total --format=csv,noheader 2>$null
            if ($raw) {
                $parts = ($raw -split ',') | ForEach-Object { $_.Trim() }
                if ($parts.Count -ge 4) {
                    $gpuUtil = Fmt ($parts[0] -replace '[^0-9\.]','')
                    $gpuTemp = Fmt ($parts[1] -replace '[^0-9\.]','')
                    $vramUsed = Fmt ($parts[2] -replace '[^0-9\.]','')
                    $vramTotal = Fmt ($parts[3] -replace '[^0-9\.]','')
                }
            }
        } catch {}
    }

    $cpuTemp = ''
    try {
        $tz = Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction Stop | Select-Object -First 1
        if ($tz) { $cpuTemp = Fmt (($tz.CurrentTemperature / 10) - 273.15) }
    } catch {}

    $topCpu = ''
    $topRam = ''
    try {
        $topCpu = ((Get-Process -ErrorAction SilentlyContinue | Sort-Object CPU -Descending | Select-Object -First 5 -ExpandProperty ProcessName) -join ';')
        $topRam = ((Get-Process -ErrorAction SilentlyContinue | Sort-Object WorkingSet -Descending | Select-Object -First 5 -ExpandProperty ProcessName) -join ';')
    } catch {}

    $line = ('{0},{1},{2},{3},{4},{5},{6},{7},{8},"{9}","{10}"' -f $timestamp,$cpu,$ramUsed,$ramTotal,$gpuUtil,$gpuTemp,$vramUsed,$vramTotal,$cpuTemp,$topCpu,$topRam)
    Add-Content -LiteralPath $csvPath -Value $line -Encoding UTF8

    $count++
    if ($count % 15 -eq 0) {
        Write-Host ("[monitor] samples=" + $count + " CPU=" + $cpu + "% GPU=" + $gpuUtil + "% RAM=" + $ramUsed + "/" + $ramTotal + "GB time=" + $timestamp)
    }
    Start-Sleep -Seconds 1
}

Write-Host ("Monitoring stopped. Samples: " + $count + ". Log: " + $csvPath)