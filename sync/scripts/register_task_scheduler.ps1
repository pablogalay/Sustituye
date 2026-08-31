# Registers the Windows Task Scheduler entry that triggers a sync run via
# trigger_sync.ps1 (an HTTP call to the running app, same path as the admin button).
# Requires `docker compose up` to be running so http://localhost:8000 answers.
# This script is NOT run automatically by anything - run it yourself, once, after
# verifying the button works from the app. Re-running it updates the existing task.
#
# Schedule: weekdays at 07:19, 10:10, 13:33 and 16:02; weekends (Saturday and
# Sunday) once at 20:02.

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$triggerScript = Join-Path $scriptDir "trigger_sync.ps1"
$taskName = "EducaMadridSubstitutionSync"

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$triggerScript`""

$weekdays = "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"
$triggers = @(
    New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "07:19"
    New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "10:10"
    New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "13:33"
    New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "16:02"
    New-ScheduledTaskTrigger -Weekly -DaysOfWeek "Saturday", "Sunday" -At "20:02"
)

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers -Settings $settings `
    -Description "Calls POST /admin/sync-educamadrid to pull in new EducaMadrid substitutions - weekdays 07:19/10:10/13:33/16:02, weekends 20:02" `
    -Force
