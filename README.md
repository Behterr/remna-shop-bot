# RemnaShop Bot — Telegram-бот продажи VPN для Remnawave

Production-ready Telegram-бот для автоматической продажи и продления VPN-подписок с полной интеграцией панели **Remnawave**, платежными системами, планировщиком уведомлений, реферальной системой, промокодами и Docker-развертыванием.

## Стек

- **Python 3.12+** (`aiogram 3.x`, `FastAPI`, `uvicorn`, `SQLAlchemy 2.0`, `asyncpg`, `Alembic`, `APScheduler`, `httpx`, `pydantic-settings`)
- **PostgreSQL 16** + **Redis 7**
- **Docker** (multi-stage build) + **Docker Compose**

---

## Быстрая автоустановка из приватного GitHub-репозитория (Ubuntu / Debian VPS)

Замените `<YOUR_GITHUB_TOKEN>` и `<GITHUB_USER>` на свои значения:

```bash
export GITHUB_TOKEN="<YOUR_GITHUB_TOKEN>"
export REPO_OWNER_SLUG="<GITHUB_USER>/remna-shop-bot"
curl -fsSL -H "Authorization: Bearer ${GITHUB_TOKEN}" \
  "https://raw.githubusercontent.com/${REPO_OWNER_SLUG}/main/install.sh" | bash
```

Установщик автоматически:
1. Установит `git`, `curl`, `nano`, `openssl`, Docker Engine и `docker compose` plugin.
2. Склонирует приватный репозиторий в `/opt/remna-shop-bot` (через `GITHUB_TOKEN` или SSH Deploy Key, не сохраняя токен в `.git/config`).
3. Сгенерирует стойкие пароли для PostgreSQL и Redis, интерактивно заполнит `.env`.
4. При необходимости подключит внешнюю Docker-сеть `remnawave-network` через `docker-compose.override.yml`.
5. Соберёт и запустит контейнеры, выполнит миграции Alembic и установит CLI-менеджер `remnabot`.

### Управление установленным ботом

```bash
remnabot
```

Меню CLI:
1. Статус и логи
2. Обновить (`git pull` + пересборка контейнеров)
3. Перезапустить
4. Редактировать `.env` в `nano`
5. Бэкап и восстановление PostgreSQL (`pg_dump` / `psql`)
6. Полное удаление

---

## Конфигурация Nginx Reverse Proxy для вебхуков и кнопки подключения

Бот слушает HTTP-порт `127.0.0.1:8080`. Для приема вебхуков **CryptoBot**, **YooKassa**, опционального webhook-режима Telegram и безопасного редиректа `/connect/{app}` настройте Nginx с SSL (например, через Certbot):

```nginx
server {
    listen 80;
    server_name bot.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name bot.example.com;

    ssl_certificate     /etc/letsencrypt/live/bot.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bot.example.com/privkey.pem;

    # Защита от подмены заголовков
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # Healthcheck
    location = /health {
        proxy_pass http://127.0.0.1:8080/health;
    }

    # Безопасный HTTPS-редирект для кнопок Happ / v2rayTUN / Hiddify / Streisand
    location /connect/ {
        proxy_pass http://127.0.0.1:8080/connect/;
    }

    # Вебхуки платежных систем и Telegram Bot API
    location /webhooks/ {
        proxy_pass http://127.0.0.1:8080/webhooks/;
        proxy_read_timeout 30s;
    }
}
```

### URL вебхуков для настройки в панелях

- **CryptoBot (`@CryptoBot` → Crypto Pay → Webhooks):** `https://bot.example.com/webhooks/cryptobot`
- **YooKassa (Настройки → HTTP-уведомления):** `https://bot.example.com/webhooks/yookassa`
- **Telegram Webhook (если `BOT_MODE=webhook`):** `https://bot.example.com/webhooks/telegram`

---

## Локальная разработка и тестирование

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -v
```
