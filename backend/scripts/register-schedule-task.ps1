<#
.SYNOPSIS
    Registers (or removes) the Windows Task Scheduler task that publishes scheduled posts every 15 minutes.

.DESCRIPTION
    The task runs, from the backend folder:
        <repo>\.vevn\Scripts\python.exe -m social_media_auto_poster.main run --scheduled          (simulated)
        <repo>\.vevn\Scripts\python.exe -m social_media_auto_poster.main run --scheduled --real   (with -Real)

    Without -Real the task is SIMULATED: nothing is ever sent to LinkedIn. Set up and check a simulated
    task first; only then register the real one with -Real. The two tasks have different names, so they
    are never confused, and -Remove removes only the one you name (with or without -Real).

    Settings: every 15 minutes, indefinitely; "Run whether user is logged on or not" without storing a
    password (S4U logon); "Do not start a new instance" if the previous run is still going; run as soon as
    possible after a missed start; stop a run that takes longer than 30 minutes.

    This script never reads or stores the LinkedIn token or anything else from backend\.env; the app reads
    its own settings when the task runs. Registering needs PowerShell started with "Run as administrator"
    (S4U tasks). Use -WhatIf to see exactly what would be done without registering anything.

.PARAMETER Real
    Register (or remove) the REAL task, which publishes due real schedules to LinkedIn. Never a default.

.PARAMETER Remove
    Remove the task instead of registering it.

.EXAMPLE
    .\backend\scripts\register-schedule-task.ps1 -WhatIf          # dry run: show the simulated task
    .\backend\scripts\register-schedule-task.ps1                  # register the simulated task
    .\backend\scripts\register-schedule-task.ps1 -Real            # register the real task (asks to confirm)
    .\backend\scripts\register-schedule-task.ps1 -Remove          # remove the simulated task
    .\backend\scripts\register-schedule-task.ps1 -Remove -Real    # remove the real task
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Real,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

# Everything is found from this script's own location: <repo>\backend\scripts\register-schedule-task.ps1
$Backend = Split-Path -Parent $PSScriptRoot
$Repo = Split-Path -Parent $Backend
$Python = Join-Path $Repo ".vevn\Scripts\python.exe"
$Mode = if ($Real) { "real" } else { "simulated" }
$TaskName = "AutoPoster - scheduled posts ($Mode)"
$Arguments = "-m social_media_auto_poster.main run --scheduled" + $(if ($Real) { " --real" } else { "" })

if ($Remove) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $existing) {
        Write-Host "No task named '$TaskName'; nothing to remove."
        return
    }
    if ($PSCmdlet.ShouldProcess($TaskName, "Remove the scheduled task")) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed '$TaskName'. Scheduled posts stay in the job database; nothing publishes them until a task runs again."
    }
    return
}

foreach ($path in @($Python, (Join-Path $Backend "pyproject.toml"))) {
    if (-not (Test-Path $path)) {
        throw "Not found: $path. Run this script from the project, after creating the .vevn virtual environment."
    }
}

$action = New-ScheduledTaskAction -Execute $Python -Argument $Arguments -WorkingDirectory $Backend
# Start at the next whole minute, then repeat every 15 minutes with no end (no RepetitionDuration = indefinitely).
$start = (Get-Date).Date.AddHours((Get-Date).Hour).AddMinutes((Get-Date).Minute + 1)
$trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Minutes 15)
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
# S4U = "Run whether user is logged on or not" + "Do not store password". Runs as you, with your files.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited

Write-Host ""
Write-Host "Task name : $TaskName"
Write-Host "Program   : $Python"
Write-Host "Arguments : $Arguments"
Write-Host "Start in  : $Backend"
Write-Host "Trigger   : from $($start.ToString('yyyy-MM-dd HH:mm')), every 15 minutes, indefinitely"
Write-Host "Settings  : do not start a new instance if running; start when available after a missed start;"
Write-Host "            stop after 30 minutes; also on battery"
Write-Host "Runs as   : $env:USERDOMAIN\$env:USERNAME, whether logged on or not, no password stored (S4U)"
Write-Host "Log       : $(Join-Path $Backend 'logs\autoposter.log')"
if ($Real) {
    Write-Host ""
    Write-Host "REAL MODE: due real schedules will be PUBLISHED TO LINKEDIN by this task." -ForegroundColor Yellow
    Write-Host "Check first: python -m social_media_auto_poster.main linkedin-check" -ForegroundColor Yellow
}
Write-Host ""

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    throw "A task named '$TaskName' already exists. Remove it first (add -Remove), then register it again."
}

if ($Real -and -not $WhatIfPreference) {
    if (-not $PSCmdlet.ShouldContinue("Register a task that publishes due real schedules to LinkedIn every 15 minutes?",
                                      "Register the REAL task")) {
        Write-Host "Cancelled; nothing was registered."
        return
    }
}

if ($PSCmdlet.ShouldProcess($TaskName, "Register the scheduled task")) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description "Social Media Auto-Poster: publishes due $Mode schedules (run --scheduled). See README, Scheduling." | Out-Null
    Write-Host "Registered '$TaskName'. Check it in Task Scheduler (Task Scheduler Library) and watch its History and the log."
}
