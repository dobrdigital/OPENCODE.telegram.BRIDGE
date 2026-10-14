<#
.SYNOPSIS
    Registers the scheduled tasks that bring the bridge and the STT shim up at logon.

.DESCRIPTION
    Two tasks, ordered by delay so the speech shim is listening before the
    bridge can receive a voice message:

        gigaam-stt-autostart    +15s   start-gigaam-stt.ps1   (:8300)
        cc-connect-autostart    +20s   start-cc-connect.ps1   (Telegram)

    Run with -Uninstall to remove both.

    Idempotent: re-running overwrites the tasks in place.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install-autostart.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install-autostart.ps1 -Uninstall
#>

[CmdletBinding()]
param(
    [switch] $Uninstall,
    [int]    $ShimDelaySec = 15,
    [int]    $BridgeDelaySec = 20,
    [string] $Root = (Join-Path $env:USERPROFILE '.cc-connect'),
    [string] $ShimDir = (Join-Path $env:USERPROFILE '.cc-connect\gigaam-stt')
)

$ErrorActionPreference = 'Stop'

$ShimTask    = 'gigaam-stt-autostart'
$BridgeTask  = 'cc-connect-autostart'

if ($Uninstall) {
    foreach ($n in @($ShimTask, $BridgeTask)) {
        if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {
            schtasks /delete /tn $n /f | Out-Null
            Write-Output "removed $n"
        }
        else {
            Write-Output "$n not present"
        }
    }
    exit 0
}

foreach ($p in @((Join-Path $Root 'start-cc-connect.ps1'), (Join-Path $ShimDir 'start-gigaam-stt.ps1'))) {
    if (-not (Test-Path -LiteralPath $p)) {
        Write-Error "missing launcher: $p"
        exit 1
    }
}

function New-LogonTask {
    param(
        [string] $Name,
        [string] $Script,
        [int]    $DelaySec,
        [string] $WorkingDir,
        [string] $Description
    )

    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $Script)

    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $trigger.Delay = 'PT{0}S' -f $DelaySec

    # Interactive + Limited: no stored password, no elevation prompt.
    $principal = New-ScheduledTaskPrincipal `
        -UserId ('{0}\{1}' -f $env:USERDOMAIN, $env:USERNAME) `
        -LogonType Interactive -RunLevel Limited

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

    Register-ScheduledTask -TaskName $Name -Description $Description `
        -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force |
        Out-Null

    Write-Output ("registered {0,-24} delay={1}s" -f $Name, $DelaySec)
}

New-LogonTask -Name $ShimTask -Script (Join-Path $ShimDir 'start-gigaam-stt.ps1') `
    -DelaySec $ShimDelaySec -WorkingDir $ShimDir `
    -Description 'Autostart the GigaAM STT shim (OpenAI-compatible endpoint on 127.0.0.1:8300)'

New-LogonTask -Name $BridgeTask -Script (Join-Path $Root 'start-cc-connect.ps1') `
    -DelaySec $BridgeDelaySec -WorkingDir $Root `
    -Description 'Autostart the cc-connect bridge (Telegram)'

Write-Host ''
Write-Host 'Both tasks registered. Verify:' -ForegroundColor Cyan
Write-Host ("  schtasks /query /tn {0}" -f $ShimTask)
Write-Host ("  schtasks /query /tn {0}" -f $BridgeTask)
Write-Host ''
Write-Host 'Only a real reboot proves the logon trigger fires.' -ForegroundColor DarkGray
exit 0