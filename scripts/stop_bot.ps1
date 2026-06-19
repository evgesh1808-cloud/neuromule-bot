# Останавливает все экземпляры NeuroMule на этом ПК и снимает lock.
$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (Test-Path "data\telegram_bot.lock") {
    $lockPid = 0
    [void][int]::TryParse((Get-Content "data\telegram_bot.lock" -Raw).Trim(), [ref]$lockPid)
    if ($lockPid -gt 0) {
        Stop-Process -Id $lockPid -Force
        Write-Host "Остановлен lock PID $lockPid" -ForegroundColor Yellow
    }
}

# Все python main.py (в т.ч. WindowsApps-stub + pythoncore — иначе Telegram Conflict).
foreach ($p in Get-CimInstance Win32_Process -Filter "name='python.exe'") {
    $cmd = if ($p.CommandLine) { $p.CommandLine } else { "" }
    if ($cmd -match "main\.py") {
        Stop-Process -Id $p.ProcessId -Force
        Write-Host "Остановлен python PID $($p.ProcessId)" -ForegroundColor Yellow
    }
}

foreach ($p in Get-CimInstance Win32_Process -Filter "name='powershell.exe'") {
    $cmd = if ($p.CommandLine) { $p.CommandLine } else { "" }
    if ($cmd -like "*$projectMark*" -and ($cmd -like "*run_bot_forever*" -or $cmd -like "*start_bot*")) {
        if ($p.ProcessId -ne $PID) {
            Stop-Process -Id $p.ProcessId -Force
            Write-Host "Остановлен shell PID $($p.ProcessId)" -ForegroundColor Yellow
        }
    }
}

Start-Sleep -Seconds 4
Remove-Item "data\telegram_bot.lock" -Force -ErrorAction SilentlyContinue
Write-Host "Lock сброшен. Можно запускать одного бота." -ForegroundColor Green
