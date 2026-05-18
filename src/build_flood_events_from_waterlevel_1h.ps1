param(
    [string]$StartDate = "2024-01-01",
    [string]$EndDate = "2025-12-31",
    [int]$MaxStations = 0
)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$obsPath = Join-Path $root "metadata_outputs/obsFinal.csv"
$envPath = Join-Path $root ".env"
$outDir = Join-Path $root "output/DTW"
$outFile = Join-Path $outDir "flood_events_han_2024_2025.csv"
$outDates = Join-Path $outDir "flood_event_dates_han_2024_2025.csv"

New-Item -ItemType Directory -Force -Path $outDir | Out-Null
if (Test-Path $outFile) { Remove-Item $outFile -Force }
if (Test-Path $outDates) { Remove-Item $outDates -Force }

$envRaw = Get-Content -Raw -Path $envPath -Encoding UTF8
$tokenMatch = [regex]::Match($envRaw, 'hrfco_token"\s*=\s*"\s*([A-Fa-f0-9\-]+)\s*"')
if (-not $tokenMatch.Success) { throw "hrfco_token not found in .env" }
$token = $tokenMatch.Groups[1].Value

$rows = Import-Csv -Path $obsPath -Encoding Default
$designCols = @($rows[0].PSObject.Properties.Name | Where-Object { $_ -like "designFlood*" })
if ($designCols.Count -eq 0) { throw "No designFlood* columns found in obsFinal.csv" }

function Has-DesignFlood($row, $cols) {
    foreach ($c in $cols) {
        $v = "$($row.$c)".Trim()
        if ($v -and $v -ne "-") { return $true }
    }
    return $false
}

function Norm-Code($s) {
    $v = "$s".Trim()
    if ($v.EndsWith(".0")) { $v = $v.Substring(0, $v.Length - 2) }
    return $v
}

function To-DoubleOrNull($s) {
    $t = "$s".Trim()
    if (-not $t -or $t -eq "-") { return $null }
    $t = $t.Replace(",", "")
    try { return [double]$t } catch { return $null }
}

$targets = New-Object System.Collections.Generic.List[object]
foreach ($r in $rows) {
    if (-not (Has-DesignFlood $r $designCols)) { continue }
    $ws = "$($r.codeWatershed)".Trim()
    if (-not $ws -or -not ($ws -match '^1')) { continue }

    $codeObs = Norm-Code $r.codeObs
    if (-not $codeObs) { continue }

    $adv = To-DoubleOrNull $r.'aFLAdvisory(m)'
    $alt = To-DoubleOrNull $r.'aFLAlert(m)'
    if ($null -eq $adv -and $null -eq $alt) { continue }

    $targets.Add([pscustomobject]@{
        codeObs = $codeObs
        codeWatershed = $ws
        korObs = "$($r.korObs)"
        advisory_m = $adv
        alert_m = $alt
    }) | Out-Null
}

if ($MaxStations -gt 0) { $targets = @($targets | Select-Object -First $MaxStations) }
if ($targets.Count -eq 0) { throw "No target stations after filtering obsFinal." }

$start = Get-Date $StartDate
$end = Get-Date $EndDate

Write-Host "[START] build events from waterlevel(1H) daily max thresholds"
Write-Host "[START] range=$StartDate~$EndDate targets=$($targets.Count)"

$wroteHeader = $false
$eventDateSet = New-Object "System.Collections.Generic.HashSet[string]"
$rowsWritten = 0
$stationIdx = 0

function Fetch-1H($token, $code, $sdt, $edt) {
    $url = "https://api.hrfco.go.kr/$token/waterlevel/list/1H/$code/$sdt/$edt.json"
    return Invoke-RestMethod -Uri $url -Method Get -TimeoutSec 25
}

foreach ($tgt in $targets) {
    $stationIdx++
    $code = $tgt.codeObs
    $adv = $tgt.advisory_m
    $alt = $tgt.alert_m

    # API guideline: 1H max 1 year range. Split by calendar year.
    $year = $start.Year
    while ($year -le $end.Year) {
        $chunkStart = Get-Date "$year-01-01"
        $chunkEnd = Get-Date "$year-12-31 23:00"
        if ($chunkStart -lt $start) { $chunkStart = $start }
        if ($chunkEnd -gt $end) { $chunkEnd = $end }
        if ($chunkStart -gt $chunkEnd) { $year++; continue }

        $sdt = $chunkStart.ToString("yyyyMMddHH")
        $edt = $chunkEnd.ToString("yyyyMMddHH")
        Write-Host "[REQ] station=$code year=$year $sdt~$edt ($stationIdx/$($targets.Count))"

        try {
            $resp = Fetch-1H $token $code $sdt $edt
        } catch {
            Write-Host "[ERR] station=$code year=$year msg=$($_.Exception.Message)"
            $year++
            continue
        }

        $content = @()
        if ($resp -and ($resp.PSObject.Properties.Name -contains "content")) { $content = @($resp.content) }

        # daily max aggregation (key=yyyyMMdd)
        $dailyMax = @{}
        foreach ($pt in $content) {
            $ymdhm = "$($pt.ymdhm)".Trim() # yyyyMMddHH
            if (-not $ymdhm -or $ymdhm.Length -lt 8) { continue }
            $day = $ymdhm.Substring(0,8)
            $wl = To-DoubleOrNull $pt.wl
            if ($null -eq $wl) { continue }
            if (-not $dailyMax.ContainsKey($day) -or $wl -gt $dailyMax[$day]) {
                $dailyMax[$day] = $wl
            }
        }

        foreach ($day in ($dailyMax.Keys | Sort-Object)) {
            $maxWl = $dailyMax[$day]
            $level = $null
            if ($null -ne $alt -and $maxWl -ge $alt) { $level = "ALERT" }
            elseif ($null -ne $adv -and $maxWl -ge $adv) { $level = "ADVISORY" }
            else { continue }

            $row = [pscustomobject]@{
                event_date = $day
                station_code = $code
                level = $level
                wl_max_m = $maxWl
                advisory_m = $adv
                alert_m = $alt
                codeWatershed = $tgt.codeWatershed
                korObs = $tgt.korObs
                source = "waterlevel_1H_dailymax_threshold"
            }

            if (-not $wroteHeader) {
                $row | Export-Csv -Path $outFile -NoTypeInformation -Encoding UTF8
                $wroteHeader = $true
            } else {
                $row | Export-Csv -Path $outFile -NoTypeInformation -Encoding UTF8 -Append
            }
            $rowsWritten++
            $eventDateSet.Add($day) | Out-Null
        }

        $year++
    }

    if (($stationIdx % 5) -eq 0) {
        Write-Host "[PROGRESS] stations=$stationIdx/$($targets.Count) rows=$rowsWritten uniqueDates=$($eventDateSet.Count)"
    }
}

if (-not (Test-Path $outFile)) {
    [pscustomobject]@{ event_date = ""; message = "no_events" } |
        Export-Csv -Path $outFile -NoTypeInformation -Encoding UTF8
    [pscustomobject]@{ event_date = ""; message = "no_events" } |
        Export-Csv -Path $outDates -NoTypeInformation -Encoding UTF8
    Write-Host "[DONE] no_events"
    exit 0
}

$dates = @($eventDateSet | Sort-Object)
$dateRows = foreach ($d in $dates) { [pscustomobject]@{ event_date = $d } }
$dateRows | Export-Csv -Path $outDates -NoTypeInformation -Encoding UTF8

Write-Host "[DONE] rows=$rowsWritten uniqueDates=$($eventDateSet.Count)"
Write-Host "saved: $outFile"
Write-Host "saved: $outDates"
