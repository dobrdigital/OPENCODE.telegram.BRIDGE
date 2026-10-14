<#
.SYNOPSIS
    Autotest for the cc-connect autostart chain (scheduled task -> launcher -> bridge -> opencode).

.DESCRIPTION
    Verifies, without side effects by default, that a fresh login would bring the
    Telegram bridge back up against the shared OpenCode session.

    Checks, in order:
      A. Launcher integrity  - file exists, parses, keeps its safety guards
      B. Task definition     - trigger, action, principal, battery/idempotency settings
      C. Runtime state       - process alive, opencode service listening, logs sane
      D. Simulation          - fires the task and asserts it exits 0 twice in a row
      E. Destructive         - stop/start cycle, ONLY with -Live

    WHY ENGLISH ONLY:
    The launcher runs under Windows PowerShell 5.1, which decodes BOM-less files as
    ANSI. Cyrillic in this file would be mangled under that host. ASCII keeps output
    correct regardless of how the file was written.

.LIMITATION (honest):
    A real logon cannot be synthesised on a live machine without a VM snapshot. Section D
    verifies the trigger *definition* field by field and invokes the task's real action,
    which is the closest faithful approximation available. It does NOT prove Windows will
    actually fire the trigger - only a real reboot does that.

.SAFETY:
    Sections A-D are non-destructive. Stopping cc-connect severs the bridge that is very
    likely carrying the conversation you are reading this from, so the stop/start cycle is
    gated behind -Live and refuses to run unless you also pass -Force.

.PARAMETER Live
    Run the destructive stop/start cycle. Killing the bridge mid-session is not
    recoverable from this session, so this is opt-in only.

.PARAMETER SkipSimulation
    Skip section D (does not invoke the task at all).

.PARAMETER Force
    Acknowledgement required in addition to -Live.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\autostart-test.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\autostart-test.ps1 -Live -Force

.OUTPUTS
    Exit 0 = no failures. Exit 1 = at least one failure. Warnings alone still exit 0.
#>

[CmdletBinding()]
param(
    [switch] $Live,
    [switch] $Force,
    [switch] $SkipSimulation,
    [string] $TaskName  = 'cc-connect-autostart',
    [string] $Launcher  = (Join-Path $env:USERPROFILE '.cc-connect\start-cc-connect.ps1'),
    [string] $Bin       = (Join-Path $env:APPDATA  'npm\cc-connect.cmd'),
    [string] $Root      = (Join-Path $env:USERPROFILE '.cc-connect'),
    [int]    $SimTimeoutSec = 45
)

$ErrorActionPreference = 'Continue'
Set-StrictMode -Version 2.0

# ---------------------------------------------------------------- test harness

$script:Failures = 0
$script:Warnings = 0
$script:Passes   = 0
$script:Notes    = 0
$script:Skipped  = 0

function Write-Section { param([string]$Name) Write-Host ''; Write-Host "== $Name ==" -ForegroundColor Cyan }

function Add-Result {
    param(
        [Parameter(Mandatory)][string] $Name,
        [Parameter(Mandatory)][bool]   $Ok,
        [string] $Detail = '',
        [ValidateSet('FAIL','WARN','INFO','SKIP')][string] $Severity = 'FAIL'
    )

    switch ($Severity) {

        'SKIP' {
            $script:Skipped++
            Write-Host ('  [SKIP] {0}' -f $Name) -ForegroundColor DarkGray
            if ($Detail) { Write-Host ('         {0}' -f $Detail) -ForegroundColor DarkGray }
            return
        }

        'INFO' {
            $script:Notes++
            Write-Host ('  [INFO] {0}' -f $Name) -ForegroundColor DarkGray
            if ($Detail) { Write-Host ('         {0}' -f $Detail) -ForegroundColor DarkGray }
            return
        }

        'WARN' {
            if ($Ok) {
                $script:Passes++
                Write-Host ('  [PASS] {0}' -f $Name) -ForegroundColor Green
                if ($Detail) { Write-Host ('         {0}' -f $Detail) -ForegroundColor DarkGray }
            }
            else {
                $script:Warnings++
                Write-Host ('  [WARN] {0}' -f $Name) -ForegroundColor Yellow
                if ($Detail) { Write-Host ('         {0}' -f $Detail) -ForegroundColor DarkGray }
            }
            return
        }
    }

    # default: hard assertion
    if ($Ok) {
        $script:Passes++
        Write-Host ('  [PASS] {0}' -f $Name) -ForegroundColor Green
        if ($Detail) { Write-Host ('         {0}' -f $Detail) -ForegroundColor DarkGray }
    }
    else {
        $script:Failures++
        Write-Host ('  [FAIL] {0}' -f $Name) -ForegroundColor Red
        if ($Detail) { Write-Host ('         {0}' -f $Detail) -ForegroundColor Red }
    }
}

function Test-Ok      { param([string]$N, [bool]$C, [string]$D='')            Add-Result $N $C $D 'FAIL' }
function Test-Eq      { param([string]$N, $Expect, $Actual, [string]$D='')
    $ok = ([string]$Expect -eq [string]$Actual)
    Add-Result $N $ok $(if ($ok) { $D } else { "expected '$Expect', got '$Actual'. $D" })
}
function Test-Match   { param([string]$N, [string]$Text, [string]$Pattern, [string]$D='')
    $ok = $false
    if ($null -ne $Text) { $ok = ($Text -match $Pattern) }
    Add-Result $N $ok $(if ($ok) { $D } else { "pattern '$Pattern' not found. $D" })
}
function Test-Warn    { param([string]$N, [bool]$C, [string]$D='')            Add-Result $N $C $D 'WARN' }
function Test-Note    { param([string]$N, [string]$D='')                     Add-Result $N $true $D 'INFO' }
function Skip-Case    { param([string]$N, [string]$D='')                     Add-Result $N $false $D 'SKIP' }

Write-Host ''
Write-Host 'cc-connect autostart autotest' -ForegroundColor White
Write-Host ("  task    : {0}" -f $TaskName)
Write-Host ("  launcher: {0}" -f $Launcher)
Write-Host ("  host    : PS {0}" -f $PSVersionTable.PSVersion) -ForegroundColor DarkGray
Write-Host ("  mode    : {0}" -f $(if ($Live) { 'LIVE (destructive section enabled)' } else { 'SAFE (read-only + simulation)' })) -ForegroundColor DarkGray

if ($env:OS -ne 'Windows_NT') {
    Write-Warning 'Not on Windows NT; the ScheduledTasks module is expected to be unavailable.'
}

# ------------------------------------------------- A. launcher integrity

Write-Section 'A. Launcher integrity'

$launcherExists = Test-Path -LiteralPath $Launcher
Test-Ok 'launcher file exists' $launcherExists $Launcher

if ($launcherExists) {
    $parseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $Launcher, [ref]$null, [ref]$parseErrors)
    Test-Ok 'launcher parses without syntax errors' ($parseErrors.Count -eq 0) `
        $(if ($parseErrors.Count) { ($parseErrors | ForEach-Object { $_.Message }) -join '; ' } else { 'AST parse clean' })

    $src = Get-Content -LiteralPath $Launcher -Raw

    Test-Match 'guard: detects an already-running instance'  $src 'Get-CCProcess'
    Test-Match 'guard: refuses to double-start'               $src 'already running'
    Test-Match 'has a -Stop branch for clean shutdown'        $src '\$Stop'
    Test-Match 'shared mode clears XDG_* so desktop DB is used' $src 'Remove-Item Env:\\XDG_'
    Test-Match 'isolated mode sets XDG_* for a private DB'     $src '\$env:XDG_DATA_HOME'
    Test-Match 'starts the binary detached and hidden'         $src 'Start-Process'
    Test-Match 'captures stdout/stderr for diagnosis'          $src 'RedirectStandardOutput'

    $rootExists = Test-Path -LiteralPath $Root
    Test-Ok 'launcher working directory exists' $rootExists $Root
}
else {
    Skip-Case 'launcher source checks' 'launcher missing, cannot read it'
}

$binExists = Test-Path -LiteralPath $Bin
Test-Ok 'bridge binary exists (cc-connect.cmd)' $binExists $Bin

# ------------------------------------------------- B. task definition

Write-Section 'B. Scheduled task definition'

$task = $null
try { $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop }
catch { }

Test-Ok "task '$TaskName' is registered" ($null -ne $task) `
    $(if ($null -eq $task) { 'Register it, or the bridge will not survive a reboot' } else { 'state: ' + $task.State })

if ($task) {
    Test-Eq 'action runs Windows PowerShell' 'powershell.exe' $task.Actions.Execute

    $args = [string]$task.Actions.Arguments
    Test-Match 'action passes -NoProfile'                 $args '-NoProfile'
    Test-Match 'action bypasses execution policy'         $args '-ExecutionPolicy\s+Bypass'
    Test-Match 'action runs hidden (no console flash)'    $args '-WindowStyle\s+Hidden'
    Test-Match 'action targets the launcher'              $args ([regex]::Escape('-File') + '.+' + [regex]::Escape($Launcher))
    Test-Eq   'action working directory'                  $Root $task.Actions.WorkingDirectory

    $trig = $task.Triggers[0]
    Test-Eq 'trigger type is logon' 'MSFT_TaskLogonTrigger' $trig.CimClass.CimClassName

    $delayText = [string]$trig.Delay
    if ($delayText -match '^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$') {
        $secs = ([int]$Matches[1] * 3600) + ([int]$Matches[2] * 60) + [int]$Matches[3]
    }
    else { $secs = -1 }
    Test-Ok 'trigger delay parsed as ISO-8601 duration' ($secs -ge 0) "delay='$delayText' => ${secs}s"
    Test-Ok 'delay is long enough for the network stack' ($secs -ge 15) "${secs}s (want >= 15s so Wi-Fi/VPN is up)"

    $expectedUser = '{0}\{1}' -f $env:USERDOMAIN, $env:USERNAME
    Test-Eq 'trigger fires for the current user' $expectedUser $trig.UserId

    Test-Eq 'principal is the current user'  $env:USERNAME $task.Principal.UserId
    Test-Eq 'logon type interactive (no stored password)' 'Interactive' ([string]$task.Principal.LogonType)
    Test-Eq 'run level does not require elevation' 'Limited' ([string]$task.Principal.RunLevel)

    Test-Ok 'allowed to start on battery' (-not $task.Settings.DisallowStartIfOnBatteries)
    Test-Ok 'not stopped when switching to battery' (-not $task.Settings.StopIfGoingOnBatteries)
    Test-Ok 'starts late if it missed the logon window' ([bool]$task.Settings.StartWhenAvailable)
    Test-Eq 'a second trigger cannot stack instances' 'IgnoreNew' ([string]$task.Settings.MultipleInstances)
}

# ------------------------------------------------- C. runtime state

Write-Section 'C. Runtime state (now)'

$ccProcs = @(Get-Process -Name 'cc-connect' -ErrorAction SilentlyContinue)
Test-Note 'bridge process currently running' ("PIDs: {0}" -f (($ccProcs | ForEach-Object { $_.Id }) -join ', '))

$ocProcs = @(Get-CimInstance Win32_Process -Filter "Name like 'opencode%'" -ErrorAction SilentlyContinue)
Test-Note 'opencode processes' ("{0} found" -f $ocProcs.Count)

$listener = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
              Where-Object { $_.LocalAddress -eq '127.0.0.1' -and $_.OwningProcess -in $ocProcs.ProcessId })
Test-Warn 'an opencode server is listening on loopback' ($listener.Count -gt 0) `
    $(if ($listener.Count) { 'ports: ' + (($listener | ForEach-Object { $_.LocalPort } | Sort-Object -Unique) -join ', ') }
       else { 'none - the bridge will start one on demand' })

foreach ($logName in 'run.log', 'run.err.log') {
    $logPath = Join-Path $Root $logName
    $exists  = Test-Path -LiteralPath $logPath
    Test-Warn ("{0} exists" -f $logName) $exists $logPath
    if ($exists) {
        $ageMin = [math]::Round(((Get-Date) - (Get-Item -LiteralPath $logPath).LastWriteTime).TotalMinutes, 1)
        Test-Note ("{0} freshness" -f $logName) ("last write {0} min ago" -f $ageMin)
    }
}

$runLog = Join-Path $Root 'run.log'
if (Test-Path -LiteralPath $runLog) {
    $text = Get-Content -LiteralPath $runLog -Raw

    Test-Match 'bridge bound chat traffic to an opencode session' $text 'agent_session=ses_'

    $bound = [regex]::Matches($text, 'agent_session=(ses_[A-Za-z0-9]+)') |
             ForEach-Object { $_.Groups[1].Value } |
             Select-Object -Unique
    Test-Note 'session id the bridge targets' ($bound -join ', ')

    if ($env:OPENCODE_SESSION_ID) {
        $matchesHere = @($bound) -contains $env:OPENCODE_SESSION_ID
        Test-Warn 'bridge session matches this session' $matchesHere `
            ("this session = {0}; bridge = {1}" -f $env:OPENCODE_SESSION_ID, ($bound -join ','))
    }

    $collisions = [regex]::Matches($text, '(?i)Database is not empty and has no session table')
    Test-Ok 'no opencode shared-DB schema collision in the log' ($collisions.Count -eq 0) `
        $(if ($collisions.Count) { "$($collisions.Count) occurrence(s) - the bridge is pointed at an incompatible database" }
           else { 'no "Database is not empty and has no session table" errors' })

    $failedTurns = [regex]::Matches($text, '"turn complete"').Count
    Test-Note 'completed turns observed' ("{0}" -f $failedTurns)
}

$errLog = Join-Path $Root 'run.err.log'
if (Test-Path -LiteralPath $errLog) {
    $errText = Get-Content -LiteralPath $errLog -Raw

    # The bridge writes its lifecycle lines to stderr, not stdout.
    Test-Match 'bridge logged a successful startup (stderr)' $errText 'acquired instance lock'
    Test-Match 'bridge loaded its config (stderr)'           $errText 'config loaded'

    $bad = [regex]::Matches($errText, '(?mi)^.*\b(ERROR|FATAL|panic|traceback)\b.*$')
    Test-Warn 'no ERROR/FATAL lines in stderr' ($bad.Count -eq 0) `
        $(if ($bad.Count) { ($bad | ForEach-Object { $_.Value.Trim() }) -join ' | ' } else { 'clean' })
}

# ------------------------------------------------- D. simulation

Write-Section 'D. Simulation (fire the task, do not reboot)'

if ($SkipSimulation) {
    Skip-Case 'simulation' '-SkipSimulation was passed'
}
elseif (-not $task) {
    Skip-Case 'simulation' 'task is not registered'
}
else {
    $wasRunningBefore = @(Get-Process -Name 'cc-connect' -ErrorAction SilentlyContinue).Count -gt 0
    $durations = @()

    foreach ($attempt in 1..2) {
        $label = if ($attempt -eq 1) { 'first invocation' } else { 'second invocation (idempotency)' }

        try {
            $before = (Get-Item -LiteralPath (Join-Path $Root 'run.log') -ErrorAction SilentlyContinue).LastWriteTime
            Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        }
        catch {
            Test-Ok ("simulation: {0} accepted" -f $label) $false $_.Exception.Message
            continue
        }

        $sw     = [System.Diagnostics.Stopwatch]::StartNew()
        $result = $null
        while ($sw.Elapsed.TotalSeconds -lt $SimTimeoutSec) {
            Start-Sleep -Milliseconds 500
            $info = Get-ScheduledTaskInfo -TaskName $TaskName
            if ((Get-ScheduledTask -TaskName $TaskName).State -ne 'Running') { $result = $info.LastTaskResult; break }
        }

        if ($null -eq $result) {
            Test-Ok ("simulation: {0} completed" -f $label) $false "still running after ${SimTimeoutSec}s (launcher sleeps 8s then tails logs)"
        }
        else {
            $durations += $sw.Elapsed.TotalSeconds
            Test-Eq ("simulation: {0} exit code" -f $label) 0 $result `
                ("0 = launcher ran to completion in {0:N1}s" -f $sw.Elapsed.TotalSeconds)
        }

        $after = (Get-Item -LiteralPath (Join-Path $Root 'run.log') -ErrorAction SilentlyContinue).LastWriteTime
        Test-Note ("simulation: {0} log effect" -f $label) `
            ("run.log write stamp {0} -> {1}" -f $(if ($before) { $before.ToString('HH:mm:ss') } else { '(none)' }), $(if ($after) { $after.ToString('HH:mm:ss') } else { '(none)' }))
    }

    $stillUp = @(Get-Process -Name 'cc-connect' -ErrorAction SilentlyContinue).Count -gt 0
    Test-Ok 'bridge survived both simulated runs' $stillUp

    # Coverage honesty: the launcher's idempotency guard returns long before its own
    # 8s log-tail sleep, so a sub-3s run proves the guard fired, not the cold start.
    if ($wasRunningBefore -and $durations.Count -gt 0 -and ($durations | Measure-Object -Maximum).Maximum -lt 3) {
        Test-Warn 'cold-start path actually exercised' $false `
            ("bridge was already up, so the launcher short-circuited on its guard ({0:N1}s vs the 8s+ it takes to really start). Run with -Live -Force off a live session, or simply reboot, to cover this." -f ($durations | Measure-Object -Maximum).Maximum)
    }
    else {
        Test-Note 'cold-start path' 'launcher ran past its guard, so the real start path was exercised'
    }
}

# ------------------------------------------------- E. destructive

Write-Section 'E. Destructive stop/start cycle'

if (-not $Live) {
    Skip-Case 'stop/start cycle' 'opt-in; add -Live -Force to enable (it will drop this session)'
}
elseif (-not $Force) {
    Skip-Case 'stop/start cycle' '-Live given without -Force; refusing to sever the bridge'
}
else {
    Write-Host '  WARNING: stopping the bridge now.' -ForegroundColor Yellow
    Test-Note 'note' 'anything in flight through Telegram is lost; this session cannot report its own outcome'

    $pre  = @(Get-Process -Name 'cc-connect' -ErrorAction SilentlyContinue).Count
    Test-Ok 'bridge was running before the cycle' ($pre -gt 0) "count=$pre"

    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Launcher -Stop 2>&1 |
            ForEach-Object { Test-Note 'launcher -Stop output' $_.ToString() }
    }
    catch { Test-Ok 'launcher -Stop ran' $false $_.Exception.Message }

    Start-Sleep -Seconds 3
    $post = @(Get-Process -Name 'cc-connect' -ErrorAction SilentlyContinue).Count
    Test-Ok 'bridge actually stopped' ($post -eq 0) "count=$post"

    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Launcher 2>&1 |
            ForEach-Object { Test-Note 'launcher start output' $_.ToString() }
    }
    catch { Test-Ok 'launcher start ran' $false $_.Exception.Message }

    Start-Sleep -Seconds 6
    $back = @(Get-Process -Name 'cc-connect' -ErrorAction SilentlyContinue).Count
    Test-Ok 'bridge came back up' ($back -gt 0) "count=$back"
}

# ------------------------------------------------- F. GigaAM STT shim

Write-Section 'F. GigaAM STT shim (speech-to-text)'

$shimDir  = Join-Path $Root 'gigaam-stt'
$shimPy   = Join-Path $shimDir 'server.py'
$shimPs1  = Join-Path $shimDir 'start-gigaam-stt.ps1'
$shimTask = 'gigaam-stt-autostart'
$shimPort = 8300

Test-Ok 'shim launcher exists' (Test-Path -LiteralPath $shimPs1) $shimPs1
Test-Ok 'shim server exists'   (Test-Path -LiteralPath $shimPy)  $shimPy

if (Test-Path -LiteralPath $shimPs1) {
    $shimErrs = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($shimPs1, [ref]$null, [ref]$shimErrs)
    Test-Ok 'shim launcher parses' ($shimErrs.Count -eq 0) $shimErrs.Count
    $shimSrc = Get-Content -LiteralPath $shimPs1 -Raw
    Test-Match 'shim launcher probes the port, not the process name' $shimSrc 'Test-ShimUp'
    Test-Match 'shim launcher skips venv interpreters'                $shimSrc '\\venv\\'
    Test-Match 'shim launcher can stop the shim'                      $shimSrc '\$Stop'
}

if (Test-Path -LiteralPath $shimPy) {
    $py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if ($py) {
        $compileOut = & $py -m py_compile $shimPy 2>&1
        Test-Ok 'shim server byte-compiles' ($LASTEXITCODE -eq 0) ($compileOut | Out-String).Trim()
    }
    else {
        Skip-Case 'shim server byte-compiles' 'no python.exe on PATH'
    }
}

# -- dependencies --
$handyExe = Join-Path $env:LOCALAPPDATA 'Handy\handy.exe'
Test-Ok 'handy.exe present' (Test-Path -LiteralPath $handyExe) $handyExe

$gguf = Join-Path $env:USERPROFILE '.cache\huggingface\hub\models--handy-computer--gigaam-v3-e2e-rnnt-gguf'
Test-Warn 'GigaAM GGUF is cached' (Test-Path -LiteralPath $gguf) $gguf

$ffmpegCmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
Test-Ok 'ffmpeg on PATH (needed by both shim and bridge)' ($null -ne $ffmpegCmd) `
    $(if ($ffmpegCmd) { $ffmpegCmd.Source } else { 'install ffmpeg or nothing can decode OGG' })

# -- scheduled task --
$shimTaskDef = $null
try { $shimTaskDef = Get-ScheduledTask -TaskName $shimTask -ErrorAction Stop } catch { }
Test-Ok "task '$shimTask' is registered" ($null -ne $shimTaskDef) `
    $(if ($null -eq $shimTaskDef) { 'voice messages will not be transcribed after a reboot' } else { 'state: ' + $shimTaskDef.State })

if ($shimTaskDef) {
    Test-Match 'task action targets the shim launcher' `
        ([string]$shimTaskDef.Actions.Arguments) ([regex]::Escape($shimPs1))
    $shimDelay = [string]$shimTaskDef.Triggers[0].Delay
    if ($shimDelay -match '^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$') {
        $shimSecs = ([int]$Matches[1] * 3600) + ([int]$Matches[2] * 60) + [int]$Matches[3]
    } else { $shimSecs = -1 }
    Test-Ok 'shim trigger has a delay' ($shimSecs -ge 0) "delay=$shimDelay => ${shimSecs}s"
    Test-Ok 'shim starts at or before the bridge does' ($shimSecs -le $secs) `
        "shim ${shimSecs}s vs bridge ${secs}s - a voice arriving in the gap would fail"
}

# -- live endpoint --
$health = $null
try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:$shimPort/health" -TimeoutSec 5 } catch { }
Test-Warn 'shim is answering /health' ($null -ne $health) `
    $(if ($null -eq $health) { "nothing on 127.0.0.1:$shimPort - run start-gigaam-stt.ps1" } else { 'status=' + $health.status })

if ($health) {
    Test-Eq 'shim reports handy available' $true $health.handy
    Test-Eq 'shim reports ffmpeg available' $true $health.ffmpeg

    $models = $null
    try { $models = Invoke-RestMethod -Uri "http://127.0.0.1:$shimPort/v1/models" -TimeoutSec 5 } catch { }
    Test-Warn 'shim advertises /v1/models' ($null -ne $models -and $models.data.Count -gt 0) `
        $(if ($models) { 'ids: ' + (($models.data | ForEach-Object { $_.id }) -join ', ') } else { 'endpoint missing' })
}

# -- config wiring --
$cfgPath = Join-Path $Root 'config.toml'
if (Test-Path -LiteralPath $cfgPath) {
    $cfg = Get-Content -LiteralPath $cfgPath -Raw
    Test-Match 'config enables speech-to-text'   $cfg '(?ms)^\[speech\]\s*\r?\n\s*enabled\s*=\s*true'
    Test-Match 'config points speech at the shim' $cfg ([regex]::Escape("127.0.0.1:$shimPort"))
    Test-Match 'config has a [tts] block'           $cfg '(?m)^\[tts\]\s*$'
    # Voice replies are opt-in: the block must exist but stay disabled, so the
    # bot only speaks when /tts always is issued.
    Test-Match 'tts is opt-in (disabled by default)' $cfg '(?ms)^\[tts\]\s*\r?\n\s*enabled\s*=\s*false'
}
else {
    Skip-Case 'config wiring checks' "no config at $cfgPath"
}

# -- round trip: synthesise Russian speech, transcribe it, compare --
# Recognition is not exact, so this asserts on keywords with tolerance rather
# than on a verbatim string. A hard pass here means the whole chain works.
#
# The phrase lives in stt-selftest.txt, NOT in this script. Windows PowerShell
# 5.1 decodes BOM-less scripts as ANSI and would mangle any literal Cyrillic
# here into a parse error. A BOM'd data file sidesteps that entirely.
if ($health -and $health.handy -and $ffmpegCmd) {

    $phraseFile = Join-Path $PSScriptRoot 'stt-selftest.txt'
    $havePhrase = Test-Path -LiteralPath $phraseFile

    if ($havePhrase) {
        $lines = @(Get-Content -LiteralPath $phraseFile -Encoding UTF8 | Where-Object { $_.Trim() })
        Test-Ok 'selftest phrase file is readable as UTF-8' ($lines.Count -ge 2) "$phraseFile"
    }
    else {
        Skip-Case 'selftest phrase file' "missing $phraseFile"
    }

    if ($havePhrase -and $lines.Count -ge 2) {
        $phrase = $lines[0]
        $expect = @($lines[1] -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })

        $tmpDir = Join-Path $env:TEMP 'cc-stt-selftest'
        if (-not (Test-Path -LiteralPath $tmpDir)) {
            New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
        }
        $wav = Join-Path $tmpDir 'selftest.wav'

        # Build the TTS snippet as a separate BOM'd script; powershell.exe then
        # reads it correctly no matter how this file was encoded.
        $synth = @"
Add-Type -AssemblyName System.Speech
`$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
`$v = `$s.GetInstalledVoices() | Where-Object { `$_.VoiceInfo.Culture.Name -eq 'ru-RU' } | Select-Object -First 1
if (`$v) { `$s.SelectVoice(`$v.VoiceInfo.Name) } else { exit 3 }
`$s.SetOutputToWaveFile('$wav')
`$s.Speak('$phrase')
`$s.Dispose()
"@
        $synthFile = Join-Path $tmpDir 'synth.ps1'
        Set-Content -LiteralPath $synthFile -Value $synth -Encoding UTF8

        $made = $false
        try {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $synthFile 2>$null | Out-Null
            $made = (Test-Path -LiteralPath $wav) -and ((Get-Item -LiteralPath $wav).Length -gt 1000)
        }
        catch { }
        Test-Warn 'can synthesise a Russian test utterance' $made `
            $(if ($made) { ((Get-Item -LiteralPath $wav).Length.ToString() + ' bytes') }
               else { 'no ru-RU SAPI voice installed - skipped' })

        if ($made) {
            # Invoke-RestMethod -Form exists only in PowerShell 7; this task and
            # the launcher both run under 5.1, so shell out to curl.exe instead.
            $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
            if (-not $curl) {
                Skip-Case 'round-trip transcription' 'curl.exe not found'
            }
            else {
                # Capture curl output to a file and decode it as UTF-8 explicitly.
                # Piping a native command straight into PowerShell decodes it with
                # the console code page, which silently mangles Cyrillic.
                $rawFile = Join-Path $tmpDir 'transcript.json'
                & $curl.Source -s --max-time 180 `
                    -X POST "http://127.0.0.1:$shimPort/v1/audio/transcriptions" `
                    -F "file=@$wav" `
                    -F 'model=whisper-1' `
                    -F 'language=ru' `
                    -o $rawFile 2>$null | Out-Null

                $text  = $null
                $rawTx = ''
                if (Test-Path -LiteralPath $rawFile) {
                    try {
                        $rawTx = Get-Content -LiteralPath $rawFile -Raw -Encoding UTF8
                        $text  = ($rawTx | ConvertFrom-Json).text
                    }
                    catch { }
                }

                if ($text) {
                    $hit = @($expect | Where-Object { $text -match [regex]::Escape($_) })
                    Test-Ok 'round trip: GigaAM recognised the test phrase' ($hit.Count -ge 1) `
                        ("matched {0}/{1} keywords, transcript {2} chars" -f $hit.Count, $expect.Count, $text.Length)
                    Test-Note 'round trip transcript' ("first 40: {0}" -f $text.Substring(0, [Math]::Min(40, $text.Length)))
                }
                else {
                    $snippet = if ($rawTx) { $rawTx.Trim() } else { 'curl produced no file' }
                    if ($snippet.Length -gt 200) { $snippet = $snippet.Substring(0, 200) }
                    Test-Ok 'round trip: shim returned a transcript' $false "no 'text' field in: $snippet"
                }
            }
        }
    }
}
else {
    Skip-Case 'round-trip transcription' 'shim not healthy, or ffmpeg missing'
}

# ------------------------------------------------- summary

Write-Section 'Summary'

Write-Host ("  passed   : {0}" -f $script:Passes)   -ForegroundColor Green
Write-Host ("  warnings : {0}" -f $script:Warnings) -ForegroundColor Yellow
Write-Host ("  failures : {0}" -f $script:Failures) -ForegroundColor $(if ($script:Failures) { 'Red' } else { 'Green' })
Write-Host ("  notes    : {0}" -f $script:Notes)    -ForegroundColor DarkGray
Write-Host ("  skipped  : {0}" -f $script:Skipped)  -ForegroundColor DarkGray

if ($script:Failures -gt 0) {
    Write-Host ''
    Write-Host '  VERDICT: autostart chain is NOT trustworthy. Fix the FAIL lines above.' -ForegroundColor Red
    Write-Host '  Reminder: only a real reboot proves the logon trigger actually fires.' -ForegroundColor DarkGray
    exit 1
}

Write-Host ''
Write-Host '  VERDICT: autostart chain checks out. Warnings are informational.' -ForegroundColor Green
Write-Host '  Reminder: only a real reboot proves the logon trigger actually fires.' -ForegroundColor DarkGray
exit 0