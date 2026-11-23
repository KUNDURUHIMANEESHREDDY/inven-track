# Installs daily encrypted backup under the current Windows user (no admin required).
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
Set-Location $project
$base = Join-Path $env:LOCALAPPDATA 'InvenTrack'
$keyPath = Join-Path $base 'backup.key'
New-Item -ItemType Directory -Path $base -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $base 'Backups') -Force | Out-Null
if (-not (Test-Path $keyPath)) {
    $key = (& py -m backend.backup generate-key).Trim()
    if (-not $key) { throw 'Could not generate a backup encryption key' }
    Set-Content -Path $keyPath -Value $key -NoNewline -Encoding Ascii
}
$scriptPath = Join-Path $PSScriptRoot 'run_backup.ps1'
$cmd = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $scriptPath + '"'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $cmd
$trigger = New-ScheduledTaskTrigger -Daily -At '03:00'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'InvenTrackBackup' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Output 'Registered InvenTrackBackup for 3:00 AM local time, when signed in.'
& $scriptPath
if ($LASTEXITCODE -ne 0) { throw 'Initial encrypted backup failed' }
