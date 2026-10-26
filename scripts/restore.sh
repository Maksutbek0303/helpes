#!/usr/bin/env bash
# Восстановление из копии. ВНИМАНИЕ: перезаписывает текущие данные. Запуск: ./scripts/restore.sh файл
set -euo pipefail
cd "$(dirname "$0")/.."
[ $# -eq 1 ] || { echo "Использование: $0 <файл копии>"; exit 1; }
set -a; [ -f .env ] && . ./.env; set +a
read -r -p "Текущие данные будут заменены. Остановите приложение. Продолжить? [y/N] " a; [ "$a" = "y" ] || exit 1
if [ "${DB_ENGINE:-sqlite}" = "sqlite" ]; then
  cp "$1" db.sqlite3
else
  PGPASSWORD="${DB_PASSWORD:-}" pg_restore -h "${DB_HOST:-localhost}" -p "${DB_PORT:-5432}" -U "${DB_USER:-helpes}" \
    -d "${DB_NAME:-helpes}" --clean --if-exists --no-owner "$1"
fi
echo "Восстановлено. Запустите приложение и выполните: python manage.py migrate"
