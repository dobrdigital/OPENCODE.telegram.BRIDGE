<#
.SYNOPSIS
    Starts the GigaAM STT shim (OpenAI-compatible /v1/audio/transcriptions).

.DESCRIPTION
    Counterpart to start-cc-connect.ps1. Kept as a file rather than an inline
    -Command string in the scheduled task, because nested quoting in a task
    action is where silent breakage lives.

    Guards against double-start by probing the port rather than by process
    name, because the shim is a plain python.exe and "python.exe is running"
    proves nothing.

.PARAMETER Stop
    Stop the shim, if it is up.

.PARAMETER Port
    Port to listen on. Default 8300.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File start-gigaam-stt.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File start-gigaam-stt.ps1 -Stop
#>

[CmdletBinding()]
param(
    [switch] $Stop,
    [int]    $Port = 8300
)

$ErrorActionPreference = 'Stop'

$Root    = $PSScriptRoot
$Script  = Join-Path $Root 'server.py'
$StdOut  = Join-Path $Root 'shim.log'
$StdErr  = Join-Path $Root 'shim.err.log'

function Test-ShimUp {
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        return ($r.status -eq 'ok')
    }
    catch { return $false }
}

function Stop-Shim {
    # The shim never wrote a pidfile, so match the exact command line. Anchoring
    # on the full script path keeps this from hitting any other python process.
    $procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
             Where-Object { $_.CommandLine -and $_.CommandLine -like "*$Script*" }

    if (-not $procs) {
        Write-Output 'shim is not running.'
        return
    }

    foreach ($p in $procs) {
        Write-Output ("stopping PID {0}" -f $p.ProcessId)
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2

    if (Test-ShimUp) {
        Write-Output 'warning: something is still answering on the port.'
    }
    else {
        Write-Output 'stopped.'
    }
}

if ($Stop) { Stop-Shim; exit 0 }

if (-not (Test-Path -LiteralPath $Script)) {
    Write-Error "shim script not found: $Script"
    exit 1
}

if (Test-ShimUp) {
    Write-Output "shim already running on port $Port"
    exit 0
}

# Pick a real interpreter. Never trust bare "python": on this machine it
# resolves to an unrelated tool's virtualenv, which can disappear under us.
$candidates = @(
    '<USERPROFILE>\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none\python.exe'
    '<USERPROFILE>\AppData\Local\Programs\Python\Python311\python.exe'
    '<USERPROFILE>\AppData\Local\Programs\Python\Python310\python.exe'
    'C:\Python311\python.exe'
    'C:\Python310\python.exe'
)

$python = $null
foreach ($c in $candidates) {
    if (-not (Test-Path -LiteralPath $c)) { continue }
    if ($c -match '\\venv\\') { continue }
    $ver = & $c -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and $ver) {
        if ([int]($ver -split '\.')[0] -ge 3) {
            $python = $c
            Write-Output ("interpreter: {0} (python {1})" -f $c, $ver)
            break
        }
    }
}

if (-not $python) {
    # Last resort: whatever python is on PATH, as long as it is not a venv.
    $onPath = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if ($onPath -and $onPath -notmatch '\\venv\\') { $python = $onPath }
}

if (-not $python) {
    Write-Error 'no usable Python interpreter found. Install 3.10+ or edit $candidates.'
    exit 1
}

$proc = Start-Process -FilePath $python `
                      -ArgumentList @($Script, '--port', $Port, '--device-index', '0', '--verbose') `
                      -WorkingDirectory $Root `
                      -RedirectStandardOutput $StdOut `
                      -RedirectStandardError $StdErr `
                      -WindowStyle Hidden `
                      -PassThru

Write-Output ("started shim, PID {0}" -f $proc.Id)

# Handy's first call loads the GGUF, so allow a generous window before giving up.
$deadline = (Get-Date).AddSeconds(20)
$up = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 750
    if (Test-ShimUp) { $up = $true; break }
    if ($proc.HasExited) { break }
}

if ($up) {
    Write-Output "shim is healthy on http://127.0.0.1:$Port"
    exit 0
}

Write-Output 'shim did not become healthy in time. shim.err.log:'
if (Test-Path -LiteralPath $StdErr) {
    Get-Content $StdErr -Tail 20 | ForEach-Object { Write-Output ("  " + $_) }
}
exit 1