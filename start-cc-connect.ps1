<#
  start-cc-connect.ps1 — launches cc-connect with the isolated OpenCode XDG
  environment it depends on, detached from this console.

  Why the env vars matter: without XDG_CONFIG_HOME / XDG_DATA_HOME, cc-connect
  hands OpenCode 2.x the default database (~/.local/share/opencode/opencode.db),
  which collides with the Desktop app's schema and breaks with
  "Database is not empty and has no session table".

  Usage:
    powershell -NoProfile -ExecutionPolicy Bypass -File start-cc-connect.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File start-cc-connect.ps1 -Stop
    powershell -NoProfile -ExecutionPolicy Bypass -File start-cc-connect.ps1 -Isolated

  -Isolated restores the private XDG_DATA_HOME / XDG_CONFIG_HOME trees, which
  puts cc-connect on its own opencode.db. Telegram sessions then never show up
  in the OpenCode desktop app or the CLI. Default is shared, so one session can
  be continued from Telegram, the desktop app and the CLI alike.
#>

param(
    [switch]$Stop,
    [switch]$Isolated
)

$ErrorActionPreference = 'Stop'

$Root      = '<USERPROFILE>\.cc-connect'
$Bin       = '<USERPROFILE>\AppData\Roaming\npm\cc-connect.cmd'
$StdOut    = Join-Path $Root 'run.log'
$StdErr    = Join-Path $Root 'run.err.log'

function Get-CCProcess {
    Get-Process cc-connect -ErrorAction SilentlyContinue
}

if ($Stop) {
    $procs = Get-CCProcess
    if (-not $procs) {
        Write-Output 'cc-connect is not running.'
        exit 0
    }
    foreach ($p in $procs) {
        Write-Output ("stopping PID {0}" -f $p.Id)
        Stop-Process -Id $p.Id -Force
    }
    Start-Sleep -Seconds 2
    Write-Output 'stopped.'
    exit 0
}

$existing = Get-CCProcess
if ($existing) {
    Write-Output ("cc-connect already running: PID {0}" -f ($existing.Id -join ', '))
    exit 0
}

# Preserve the previous log so the restart boundary stays readable.
if (Test-Path $StdOut) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    Copy-Item $StdOut (Join-Path $Root "backup\run.$stamp.log") -Force
}

# OpenCode config/data trees.
#
# Shared (default): inherit the desktop app's paths, so `opencode run` attaches
# to the SAME background service on 127.0.0.1:49374. One process, one opencode.db,
# one session list - and no cross-process event gap (anomalyco/opencode#55098).
#
# Isolated: private trees, previously used because opencode 1.19.0 choked on a
# shared database ("Database is not empty and has no session table"). That is
# gone in 2.0.28, which migrates the schema itself.
if ($Isolated) {
    $env:XDG_CONFIG_HOME = '<USERPROFILE>/.cc-connect/opencode/config'
    $env:XDG_DATA_HOME   = '<USERPROFILE>/.cc-connect/opencode/data'
    Write-Output 'mode: ISOLATED (separate opencode.db)'
} else {
    Remove-Item Env:\XDG_CONFIG_HOME -ErrorAction SilentlyContinue
    Remove-Item Env:\XDG_DATA_HOME   -ErrorAction SilentlyContinue
    Write-Output 'mode: SHARED (desktop opencode.db)'
}

$proc = Start-Process -FilePath $Bin `
                      -RedirectStandardOutput $StdOut `
                      -RedirectStandardError  $StdErr `
                      -WindowStyle Hidden `
                      -PassThru

Write-Output ("started cc-connect, PID {0}" -f $proc.Id)

# Give it a moment, then report what the log says.
Start-Sleep -Seconds 8

Write-Output ''
Write-Output '=== run.log (tail) ==='
if (Test-Path $StdOut) {
    Get-Content $StdOut -Tail 25
} else {
    Write-Output '(no log yet)'
}

Write-Output ''
Write-Output '=== run.err.log (tail) ==='
if (Test-Path $StdErr) {
    Get-Content $StdErr -Tail 15
}