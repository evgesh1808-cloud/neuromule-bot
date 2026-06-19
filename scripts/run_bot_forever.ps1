# Держит бота запущенным: при падении или обрыве python main.py — перезапуск через 5 сек.
# Запускайте ОДНО такое окно и не закрывайте его.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$delaySec = 5
Write-Host "NeuroMule watchdog: перезапуск каждые $delaySec с при падении. Ctrl+C для остановки." -ForegroundColor Cyan

while ($true) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$ts] Старт бота..." -ForegroundColor Green

    # Только если lock указывает на мёртвый PID — не убиваем живой main.py при каждом цикле.
    $lockPath = Join-Path $root "data\telegram_bot.lock"
    if (Test-Path $lockPath) {
        $lockPid = 0
        [void][int]::TryParse((Get-Content $lockPath -Raw).Trim(), [ref]$lockPid)
        $alive = $false
        if ($lockPid -gt 0) {
            $alive = $null -ne (Get-Process -Id $lockPid -ErrorAction SilentlyContinue)
        }
        if (-not $alive) {
            Remove-Item $lockPath -Force -ErrorAction SilentlyContinue
        } else {
            Write-Host "[$ts] Уже работает PID $lockPid — жду $delaySec с..." -ForegroundColor DarkYellow
            Start-Sleep -Seconds $delaySec
            continue
        }
    }

    # Не полагаемся на Test-NetConnection: TUN-VPN часто не проходит тест, но python main.py — да.
    $check = & python scripts/check_telegram_api.py 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[$ts] Telegram API недоступен. Включите VPN или TELEGRAM_PROXY_URL в .env." -ForegroundColor Yellow
        Write-Host ($check | Select-Object -Last 3) -ForegroundColor DarkYellow
        Start-Sleep -Seconds $delaySec
        continue
    }

    & python main.py
    $code = $LASTEXITCODE
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$ts] Бот остановился (код $code). Перезапуск через $delaySec с..." -ForegroundColor Yellow
    Start-Sleep -Seconds $delaySec
}
