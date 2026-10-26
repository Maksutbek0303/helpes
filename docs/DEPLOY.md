# Развёртывание на сервере компании

Ниже — **шаблон**. Имена сервера, домена, пользователей и паролей вы подставляете сами; в проекте
нет и не должно быть реальных адресов и учётных данных. Приложение рассчитано на внутреннюю сеть;
выставлять его в интернет не планировалось (в MVP это не входит). Ограничение доступа по сети
(файрвол, nginx `allow/deny`) настраивается на вашей инфраструктуре — код этого не гарантирует.

## 1. Требования к серверу
Linux, Python 3.10+, PostgreSQL 14+, nginx (рекомендуется), systemd.

## 2. База данных
```sql
CREATE ROLE helpes LOGIN PASSWORD '<надёжный пароль>';
CREATE DATABASE helpes OWNER helpes ENCODING 'UTF8';
```
(для запуска тестов на этой же СУБД роли нужно право `CREATEDB`; на боевом сервере тесты запускать не обязательно).

## 3. Приложение
```bash
sudo useradd --system --home /opt/helpes helpes
sudo -u helpes git clone <ваш репозиторий> /opt/helpes   # или скопируйте каталог проекта
cd /opt/helpes
sudo -u helpes python3 -m venv .venv
sudo -u helpes .venv/bin/pip install -r requirements.txt
sudo -u helpes cp .env.example .env && sudo chmod 600 .env
```
Заполните `.env`:
```
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=<сгенерируйте, см. .env.example>
DJANGO_ALLOWED_HOSTS=<внутреннее имя или IP сервера>
DJANGO_CSRF_TRUSTED_ORIGINS=<http(s)://внутреннее имя сервера>
DJANGO_SECURE_COOKIES=1          # только если сайт открывается по HTTPS
DB_ENGINE=postgres
DB_NAME=helpes
DB_USER=helpes
DB_PASSWORD=<пароль из шага 2>
DB_HOST=localhost
HELPES_BEHIND_PROXY=1            # если стоит nginx
```
Затем:
```bash
sudo -u helpes .venv/bin/python manage.py migrate
sudo -u helpes .venv/bin/python manage.py collectstatic --noinput
sudo -u helpes .venv/bin/python manage.py createsuperuser
sudo -u helpes .venv/bin/python manage.py check --deploy
```

## 4. Gunicorn под systemd (`/etc/systemd/system/helpes.service`)
```ini
[Unit]
Description=Helpes
After=network.target postgresql.service

[Service]
User=helpes
WorkingDirectory=/opt/helpes
ExecStart=/opt/helpes/.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
`sudo systemctl daemon-reload && sudo systemctl enable --now helpes`

## 5. nginx (фрагмент)
```nginx
server {
    listen 80;                       # или 443 ssl — сертификат из вашей инфраструктуры
    server_name <внутреннее имя сервера>;
    # allow <ваша внутренняя подсеть>; deny all;   # при необходимости ограничить доступ

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```
Статика отдаётся самим приложением (WhiteNoise), отдельная настройка не нужна.
Ссылку на форму `http(s)://<имя сервера>/` раздайте сотрудникам (закладка, ярлык на рабочем столе, QR на стенде).

## 6. Резервное копирование и восстановление
Копия: `./scripts/backup.sh /var/backups/helpes` (PostgreSQL → `pg_dump -Fc`; нужен пакет `postgresql-client`).
Запускайте ежедневно из cron, например: `15 2 * * * helpes /opt/helpes/scripts/backup.sh /var/backups/helpes`,
храните копии вне сервера и периодически проверяйте восстановление на тестовой машине.

Восстановление: остановите службу (`systemctl stop helpes`), `./scripts/restore.sh <файл.dump>`,
затем `python manage.py migrate`, `systemctl start helpes`.
Файл `.env` с секретами копируйте отдельно и храните защищённо.

## 7. Обновление версии
```bash
cd /opt/helpes && sudo -u helpes git pull
sudo -u helpes .venv/bin/pip install -r requirements.txt
sudo -u helpes .venv/bin/python manage.py migrate
sudo -u helpes .venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart helpes
```
Перед обновлением делайте резервную копию.

## 8. Известные ограничения
- Лимит спама хранится в памяти каждого процесса Gunicorn; для строгого общего лимита нужен общий кэш
  (например, Redis/файловый кэш Django) — отдельное решение.
- Блокировки после неудачных входов нет; при необходимости — ограничение на уровне nginx или django-axes.
