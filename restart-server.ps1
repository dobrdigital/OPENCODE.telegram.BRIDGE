#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Restart the shared OpenCode server without touching the bridge.

.DESCRIPTION
    When the server's view of the database falls behind, `/list` in Telegram
    comes back empty and looks like a broken bridge. The usual advice was to
    reboot the machine. That was a guess, and it is a heavy one: rebooting
    destroys the session you were in to fix a process you could have replaced
    in two seconds.

    This stops only the `serve --service` process and lets the desktop app
    start a fresh one. It deliberately does not touch cc-connect, the speech
    shim, or the desktop's own window - the bridge recovers on its own within
    seconds, and the desktop restarts its server the next time it opens.

    Nothing here is automatic. Every step prints what it will do first.

.PARAMETER Force
    Skip the confirmation prompt.

.PARAMETER RestartDesktop
    Also relaunch the OpenCode desktop app, which is what normally starts the
    server in the first place.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\restart-server.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\restart-server.ps1 -Force -RestartDesktop
#>
[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$RestartDesktop
)

$ErrorActionPreference = 'Stop'

function Get-Servers {
    <#
      Only processes whose command line contains `serve`. The desktop app's
      renderer, gpu and crashpad processes are named OpenCode.exe and are not
      servers; matching on the name alone would kill the window the user is
      looking at.
    #>
    Get-CimInstance Win32_Process -Filter "Name='opencode-cli.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'serve' } |
        Select-Object ProcessId, ParentProcessId, CreationDate, CommandLine
}

function Get-ParentAlive([int]$Pid_) {
    if ($Pid_ -le 0) { return $false }
    $p = Get-Process -Id $Pid_ -ErrorAction SilentlyContinue
    return ($null -ne $p)
}

Write-Host ''
Write-Host '  Restart the shared OpenCode server' -ForegroundColor Cyan
Write-Host '  ------------------------------------' -ForegroundColor DarkGray

$servers = @(Get-Servers)

if ($servers.Count -eq 0) {
    Write-Host ''
    Write-Host '  no server is running.' -ForegroundColor Yellow
    Write-Host '  Open the OpenCode desktop app once - it starts one.'
    Write-Host ''
    exit 0
}

Write-Host ''
Write-Host "  $($servers.Count) server process(es) found:" -ForegroundColor White
foreach ($s in $servers) {
    $parentState = if (Get-ParentAlive $s.ParentProcessId) {
        "parent $($s.ParentProcessId) alive"
    } else {
        "parent $($s.ParentProcessId) already gone"
    }
    Write-Host "    pid $($s.ProcessId)  started $($s.CreationDate)  $parentState" -ForegroundColor DarkGray
}

Write-Host ''
Write-Host '  A dead parent is normal here.' -ForegroundColor DarkGray
Write-Host '  The desktop starts this server once and lets it outlive the app.' -ForegroundColor DarkGray

if (-not $Force) {
    Write-Host ''
    Write-Host '  Stop them and let the desktop start a fresh one?' -ForegroundColor White
    $answer = Read-Host '  [y/N]'
    if ($answer -notmatch '^(y|yes|д|да)$') {
        Write-Host ''
        Write-Host '  cancelled - nothing was changed' -ForegroundColor DarkGray
        exit 0
    }
}

foreach ($s in $servers) {
    Write-Host ''
    Write-Host "  stopping pid $($s.ProcessId) ..." -ForegroundColor DarkGray
    try {
        Stop-Process -Id $s.ProcessId -Force -ErrorAction Stop
    } catch {
        Write-Host "  could not stop pid $($s.ProcessId): $($_.Exception.Message)" -ForegroundColor Red
    }
}

# Give the port time to clear before anything tries to bind it again. A
# failure here shows up much later as a confusing "address in use".
$deadline = (Get-Date).AddSeconds(15)
while ((Get-Servers).Count -gt 0 -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 400
}

$left = @(Get-Servers)
if ($left.Count -gt 0) {
    Write-Host ''
    Write-Host "  $($left.Count) server process(es) did not exit." -ForegroundColor Red
    Write-Host '  They may be mid-write; check again in a moment.' -ForegroundColor DarkGray
    exit 1
}

Write-Host '  all server processes stopped' -ForegroundColor Green

if ($RestartDesktop) {
    $exe = Join-Path $env:LOCALAPPDATA 'Programs\@opencodedesktop\OpenCode.exe'
    if (Test-Path $exe) {
        Write-Host ''
        Write-Host '  starting the desktop app ...' -ForegroundColor DarkGray
        Start-Process -FilePath $exe | Out-Null
    } else {
        Write-Host ''
        Write-Host "  desktop app not found at $exe" -ForegroundColor Yellow
        Write-Host '  open it by hand - that is what starts the server.' -ForegroundColor DarkGray
    }
}

Write-Host ''
Write-Host '  Done.' -ForegroundColor Green
Write-Host ''
Write-Host '  Check whether the view recovered:' -ForegroundColor DarkGray
Write-Host '    python tools\audit.py --only runtime' -ForegroundColor Gray
Write-Host ''
Write-Host '  It compares what the server reports against a direct read of' -ForegroundColor DarkGray
Write-Host '  the database. Equal counts means the split is over.' -ForegroundColor DarkGray
Write-Host ''