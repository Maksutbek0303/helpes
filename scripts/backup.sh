#!/usr/bin/env bash
# Резервная копия БД PostgreSQL. Параметры берутся из .env (DB_*). Запуск: ./scripts/backup.sh [каталог]
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
DEST="${1:-backups}"; mkdir -p "$DEST"
STAMP="$(date +%Y%m%d-%H%M%S)"
if [ "${DB_ENGINE:-sqlite}" = "sqlite" ]; then
  cp db.sqlite3 "$DEST/helpes-$STAMP.sqlite3"
else
  PGPASSWORD="${DB_PASSWORD:-}" pg_dump -h "${DB_HOST:-localhost}" -p "${DB_PORT:-5432}" -U "${DB_USER:-helpes}" \
    -Fc "${DB_NAME:-helpes}" -f "$DEST/helpes-$STAMP.dump"
fi
echo "Готово: $DEST (файл с меткой $STAMP)"
