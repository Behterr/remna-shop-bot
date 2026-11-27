# RemnaShop Bot

Open-source Telegram-бот для автоматической продажи и продления VPN-подписок с полной интеграцией панели **Remnawave**.

## Основные возможности

- **Полная интеграция с Remnawave API:** создание пользователей с привязкой к `telegramId`, выдача `subscriptionUrl`, продление, смена тарифов и сквадов, сброс трафика, блокировка/разблокировка, проверка состояния панели (`health_check`).
- **Удобный пользовательский UX:**
  - Пробный период (строго 1 раз на Telegram ID, параметры настраиваются в `.env` и админке).
  - Каталог тарифов (срок, лимит трафика ГБ или безлимит, лимит устройств HWID, сквады Remnawave).
  - Раздел «Моя подписка»: статус, остаток дней/трафика, QR-код в памяти, кнопки быстрого импорта и инструкций для **Happ**, **v2rayTUN**, **Hiddify**, **Streisand**, переключатель автопродления.
  - Реферальная программа (`%` на баланс или бонусные дни) и промокоды (`%` скидки, бонус на баланс, бонусные дни).
- **Платежи с защитой от двойного зачисления:** внутренний баланс, **Telegram Stars (XTR)**, **CryptoBot** (инвойсы в RUB + HMAC-проверка вебхука), **YooKassa** (API v3 с `Idempotence-Key` и проверкой статуса по API).
- **Фоновые задачи (APScheduler):** напоминания за 3 дня, 1 день и при истечении, автопродление с баланса, батчевый мониторинг трафика (предупреждения на 85% и 100%), опциональная очистка просроченных пользователей.
- **Админ-панель в Telegram (`/admin`):** статистика и выручка (день/месяц/всего), поиск и управление пользователями, CRUD тарифов, промокоды, настройки триала и рефералки, сегментированная рассылка (текст/фото + кнопка) с обработкой `TelegramRetryAfter`.

## Стек технологий

- **Python 3.12+** (`aiogram 3.x`, `FastAPI`, `uvicorn`, `SQLAlchemy 2.0`, `asyncpg`, `Alembic`, `APScheduler`, `httpx`, `pydantic-settings`, `qrcode`, `Pillow`)
- **PostgreSQL 16** + **Redis 7**
- **Docker** (multi-stage build) + **Docker Compose**

---

## Быстрая установка (Ubuntu / Debian VPS)

Выполните от имени `root` одну команду:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Behterr/remna-shop-bot/main/install.sh)
```

### Что делает установщик

1. Проверяет root-права и при необходимости устанавливает `git`, `curl`, `nano`, `openssl`, **Docker Engine** и плагин **Docker Compose**.
2. Клонирует репозиторий `https://github.com/Behterr/remna-shop-bot.git` в `/opt/remna-shop-bot`.
3. Интерактивно запрашивает параметры (`BOT_TOKEN`, `ADMIN_IDS`, `REMNAWAVE_URL`, `REMNAWAVE_API_TOKEN`, `WEBHOOK_DOMAIN`, ключи платёжек), автоматически генерирует надёжные пароли для PostgreSQL и Redis и сохраняет `.env`.
4. При подтверждении подключает внешнюю Docker-сеть `remnawave-network` через `docker-compose.override.yml`.
5. Собирает и запускает контейнеры (`bot`, `postgres`, `redis`), выполняет миграции Alembic и устанавливает CLI-утилиту `remnabot`.

---

## Управление через CLI `remnabot`

После установки панель управления доступна в терминале по команде:

```bash
remnabot
```

Доступные действия в меню:
1. **Статус и логи** (`docker compose ps` и просмотр логов)
2. **Обновить** (`git pull` + пересборка контейнеров)
3. **Перезапустить** контейнеры
4. **Редактировать `.env`** в `nano` с предложением перезапуска
5. **Бэкап и восстановление PostgreSQL** (`pg_dump` / `psql` в директорию `backups/`)
6. **Полное удаление** контейнеров, томов и файлов проекта

---

## Настройка `.env`, платежей и сети `remnawave-network`

### Основные переменные `.env`

- `BOT_TOKEN`, `ADMIN_IDS` — токен бота от `@BotFather` и список Telegram ID администраторов через запятую.
- `REMNAWAVE_URL`, `REMNAWAVE_API_TOKEN` — адрес панели (`https://panel.example.com` или `http://remnawave:3000` внутри Docker-сети) и API-токен из `Settings → API Tokens`.
- `WEBHOOK_DOMAIN` — ваш публичный HTTPS-домен (например `https://bot.example.com`). При заполненном `WEBHOOK_DOMAIN` в разделе «Моя подписка» дополнительно появляются кнопки быстрого импорта для клиентов; если домен не задан — бот показывает ссылку для копирования и QR-код.
- `BOT_MODE` — `polling` (по умолчанию) или `webhook`.

### Платежи и вебхуки

- **Telegram Stars (XTR):** работает из коробки без дополнительных ключей; курс по умолчанию задаётся в `STARS_RUB_RATE` или явно в параметрах тарифа.
- **CryptoBot:** укажите `CRYPTOBOT_TOKEN`. В `@CryptoBot → Crypto Pay → Webhooks` задайте URL:
  `https://bot.example.com/webhooks/cryptobot`
- **YooKassa:** укажите `YOOKASSA_SHOP_ID` и `YOOKASSA_SECRET_KEY`. В личном кабинете ЮKassa в разделе HTTP-уведомлений укажите URL:
  `https://bot.example.com/webhooks/yookassa`

### Подключение к `remnawave-network`

Если Remnawave работает на том же сервере в Docker-сети `remnawave-network`, выберите соответствующий пункт при установке или скопируйте шаблон вручную:

```bash
cp docker-compose.override.example.yml docker-compose.override.yml
docker compose up -d
```

После этого в `.env` можно указывать внутренний адрес панели `REMNAWAVE_URL=http://remnawave:3000`.

---

## Пример конфигурации Nginx Reverse Proxy

Контейнер бота слушает `127.0.0.1:8080`. Для приёма вебхуков и работы безопасного редиректа `/connect/{app}` настройте Nginx с SSL (например, через Certbot):

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

    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    location = /health {
        proxy_pass http://127.0.0.1:8080/health;
    }

    location /connect/ {
        proxy_pass http://127.0.0.1:8080/connect/;
    }

    location /webhooks/ {
        proxy_pass http://127.0.0.1:8080/webhooks/;
        proxy_read_timeout 30s;
    }
}
```

---
