#!/bin/sh
# Запускать на хосте из cron: делает копию БД, проверяет её и шлёт отчёт админу в Telegram
cd "$(dirname "$0")/.." && docker compose exec -T bot python -m app.backup
