# Railway Variables sozlash skripti
# Ishlatish: $env:RAILWAY_TOKEN="your-token"; .\setup-railway.ps1

$ErrorActionPreference = "Stop"

if (-not $env:RAILWAY_TOKEN) {
    Write-Host "RAILWAY_TOKEN o'rnatilmagan!" -ForegroundColor Red
    exit 1
}

Write-Host "Railway variables sozlanmoqda..." -ForegroundColor Cyan

railway variables set "BOT_TOKEN=8954620110:AAFMQBobl0y8tI2uGByp9ZBMV2ucxXoc55Y"
railway variables set "ADMIN_IDS=1432810519"
railway variables set "MORNING_HOUR=8"
railway variables set "EVENING_HOUR=20"

Write-Host "Redeploy boshlanmoqda..." -ForegroundColor Cyan
railway redeploy --yes --from-source

Write-Host "Tayyor!" -ForegroundColor Green
