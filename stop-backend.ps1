param([string]$Backend)
$root = $Backend.TrimEnd('\').ToLower()
$all = Get-CimInstance Win32_Process
$byParent = @{}
foreach ($p in $all) { if (-not $byParent.ContainsKey($p.ParentProcessId)) { $byParent[$p.ParentProcessId] = @() }; $byParent[$p.ParentProcessId] += $p }
$alive = @{}
foreach ($p in $all) { $alive[$p.ProcessId] = $true }

function Descendants($parentId) {
    $out = @()
    if ($byParent.ContainsKey($parentId)) {
        foreach ($c in $byParent[$parentId]) { $out += (Descendants $c.ProcessId); $out += $c }
    }
    return $out
}

# 2. the backend processes of this folder
$roots = $all | Where-Object {
    $_.CommandLine -and ($_.CommandLine -like '*app.workers.*' -or $_.CommandLine -like '*uvicorn app.main*') -and
    ($_.CommandLine.ToLower().Contains($root) -or ($_.ExecutablePath -and $_.ExecutablePath.ToLower().StartsWith($root)))
}
# 1. their children first
$targets = @()
foreach ($r in $roots) { $targets += (Descendants $r.ProcessId) }
$targets += $roots
# 3. orphaned multiprocessing children of this venv's interpreter
$venvHome = $null
$cfg = Join-Path $Backend 'venv\pyvenv.cfg'
if (Test-Path $cfg) { $line = Get-Content $cfg | Where-Object { $_ -match '^\s*home\s*=' } | Select-Object -First 1; if ($line) { $venvHome = ($line -split '=', 2)[1].Trim().ToLower() } }
$orphans = $all | Where-Object {
    $_.CommandLine -and $_.CommandLine -like '*multiprocessing*spawn_main*' -and -not $alive.ContainsKey($_.ParentProcessId) -and
    $_.ExecutablePath -and (($venvHome -and $_.ExecutablePath.ToLower().StartsWith($venvHome)) -or $_.ExecutablePath.ToLower().StartsWith($root))
}
$targets += $orphans
$seen = @{}
$count = 0
foreach ($p in $targets) {
    if ($seen.ContainsKey($p.ProcessId)) { continue }
    $seen[$p.ProcessId] = $true
    $kind = if ($roots -contains $p) { 'backend' } elseif ($orphans -contains $p) { 'orphaned child' } else { 'child' }
    Write-Host ("Stopping {0} {1} ({2})" -f $p.Name, $p.ProcessId, $kind)
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    $count += 1
}
if ($count -eq 0) { Write-Host 'No backend process of this project was running' } else { Start-Sleep -Seconds 2 }
