# Один экземпляр бота: stop → проверка API → отдельный процесс python main.py (не умирает с терминалом Cursor).
param(
    [switch]$Watchdog
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

& "$PSScriptRoot\stop_bot.ps1"

Write-Host "Проверка Telegram API..." -ForegroundColor Cyan
python scripts/check_telegram_api.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "Запуск отменён: нет связи с Telegram. VPN или TELEGRAM_PROXY_URL в .env." -ForegroundColor Red
    exit 1
}

if ($Watchdog) {
    Write-Host "Режим watchdog (автоперезапуск). Не закрывайте это окно." -ForegroundColor Green
    & "$PSScriptRoot\run_bot_forever.ps1"
    exit $LASTEXITCODE
}

$logDir = Join-Path $root "data"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
# Реальный интерпретатор (не WindowsApps\python.exe — иначе два процесса и Conflict в Telegram).
$pythonExe = (& python -c "import sys; print(sys.executable)" 2>$null).Trim()
if (-not $pythonExe) { $pythonExe = "python" }

Write-Host "Запуск NeuroMule ($pythonExe) в отдельном окне..." -ForegroundColor Green
$null = Start-Process -FilePath $pythonExe `
    -ArgumentList "main.py" `
    -WorkingDirectory $root `
    -PassThru `
    -WindowStyle Minimized

Start-Sleep -Seconds 10

$lockPid = 0
if (Test-Path "data\telegram_bot.lock") {
    [void][int]::TryParse((Get-Content "data\telegram_bot.lock" -Raw).Trim(), [ref]$lockPid)
}

# Убрать лишние python main.py (иначе Telegram Conflict).
$all = @(
    Get-CimInstance Win32_Process -Filter "name='python.exe'" |
        Where-Object {
            $c = $_.CommandLine
            $c -and $c -match "main\.py"
        }
)
foreach ($p in $all) {
    if ($lockPid -gt 0 -and $p.ProcessId -eq $lockPid) { continue }
    Stop-Process -Id $p.ProcessId -Force
    Write-Host "Убран дубль python PID $($p.ProcessId)" -ForegroundColor Yellow
}

if ($lockPid -le 0 -or -not (Get-Process -Id $lockPid -ErrorAction SilentlyContinue)) {
    Write-Host "Бот не запустился (нет lock PID). Проверьте окно python или запустите снова." -ForegroundColor Red
    exit 1
}

$dupes = @(Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object { $_.CommandLine -match "main\.py" })
Write-Host "OK: бот PID $lockPid, процессов main.py: $($dupes.Count). Остановка: .\scripts\stop_bot.ps1" -ForegroundColor Green
if ($dupes.Count -gt 1) {
    Write-Host "VNIMANIE: neskolko main.py - budet Telegram Conflict. Zapustite stop_bot.ps1" -ForegroundColor Red
}
