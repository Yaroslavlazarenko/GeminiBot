$Action = New-ScheduledTaskAction -Execute "python" -Argument "scripts\update.py" -WorkingDirectory $PWD
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1)
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

$TaskName = "GeminiBotAutoUpdate"
$Description = "Automatically updates GeminiBot every hour"

# Unregister existing task if it exists
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# Register new task
Register-ScheduledTask -Action $Action -Trigger $Trigger -Settings $Settings -TaskName $TaskName -Description $Description

Write-Host "Scheduled task '$TaskName' has been created successfully."