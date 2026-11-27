#!/usr/bin/env bash
set -Eeuo pipefail

# ==============================================================================
# Интерактивный установщик remna-shop-bot для Ubuntu / Debian VPS
# ==============================================================================

REPO_URL="https://github.com/Behterr/remna-shop-bot.git"
INSTALL_DIR="${INSTALL_DIR:-/opt/remna-shop-bot}"
CLI_BIN="/usr/local/bin/remnabot"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_err()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }

if [[ "${EUID}" -ne 0 ]]; then
    log_err "Запустите установщик от имени root (sudo bash install.sh)."
    exit 1
fi

prompt_value() {
    local prompt_text="$1"
    local default_val="${2:-}"
    local is_secret="${3:-false}"
    local result=""

    if [[ "${is_secret}" == "true" ]]; then
        read -r -s -p "${prompt_text}" result </dev/tty
        echo "" >/dev/tty
    else
        if [[ -n "${default_val}" ]]; then
            read -r -p "${prompt_text} [${default_val}]: " result </dev/tty
            result="${result:-$default_val}"
        else
            read -r -p "${prompt_text}: " result </dev/tty
        fi
    fi
    printf '%s' "${result}"
}

install_dependencies() {
    log_info "Проверка системных пакетов (git, curl, nano, openssl)..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq git curl nano openssl ca-certificates gnupg lsb-release >/dev/null

    if ! command -v docker >/dev/null 2>&1; then
        log_info "Установка Docker Engine и Compose plugin..."
        curl -fsSL https://get.docker.com | sh
        systemctl enable --now docker
        log_ok "Docker установлен."
    else
        log_ok "Docker уже установлен."
    fi

    if ! docker compose version >/dev/null 2>&1; then
        log_info "Установка docker-compose-plugin..."
        apt-get install -y -qq docker-compose-plugin >/dev/null
    fi
}

setup_repo() {
    if [[ -d "${INSTALL_DIR}/.git" ]]; then
        log_info "Репозиторий уже существует в ${INSTALL_DIR}, обновляем..."
        git -C "${INSTALL_DIR}" pull --ff-only
        log_ok "Код обновлён."
        return
    fi

    mkdir -p "$(dirname "${INSTALL_DIR}")"
    log_info "Клонирование ${REPO_URL} в ${INSTALL_DIR}..."
    git clone "${REPO_URL}" "${INSTALL_DIR}"
    log_ok "Репозиторий клонирован в ${INSTALL_DIR}."
}

configure_env() {
    cd "${INSTALL_DIR}"

    if [[ -f ".env" ]]; then
        local overwrite
        overwrite="$(prompt_value "Файл .env уже существует. Пересоздать? (y/N)" "N")"
        if [[ ! "${overwrite}" =~ ^[Yy]$ ]]; then
            log_info "Используем существующий .env."
            return
        fi
    fi

    echo ""
    log_info "=== Настройка параметров бота ==="
    local bot_token admin_ids remna_url remna_token remna_squads webhook_domain
    local cryptobot_token yk_shop_id yk_secret support_url

    bot_token="$(prompt_value "Telegram Bot Token (от @BotFather): " "" "true")"
    while [[ -z "${bot_token}" ]]; do
        log_warn "Bot Token обязателен."
        bot_token="$(prompt_value "Telegram Bot Token: " "" "true")"
    done

    admin_ids="$(prompt_value "Admin Telegram ID (через запятую, если несколько)")"
    while [[ -z "${admin_ids}" ]]; do
        log_warn "Укажите хотя бы один Admin ID."
        admin_ids="$(prompt_value "Admin Telegram ID")"
    done

    remna_url="$(prompt_value "URL панели Remnawave" "http://remnawave:3000")"
    remna_token="$(prompt_value "Remnawave API Token: " "" "true")"
    while [[ -z "${remna_token}" ]]; do
        log_warn "Remnawave API Token обязателен."
        remna_token="$(prompt_value "Remnawave API Token: " "" "true")"
    done
    remna_squads="$(prompt_value "UUID сквадов по умолчанию через запятую (Enter — пропустить)" "")"

    webhook_domain="$(prompt_value "HTTPS-домен для вебхуков (например https://bot.example.com, Enter — пропустить)" "")"
    cryptobot_token="$(prompt_value "CryptoBot API Token (Enter — пропустить): " "" "true")"
    yk_shop_id="$(prompt_value "YooKassa Shop ID (Enter — пропустить)" "")"
    yk_secret=""
    if [[ -n "${yk_shop_id}" ]]; then
        yk_secret="$(prompt_value "YooKassa Secret Key: " "" "true")"
    fi
    support_url="$(prompt_value "Ссылка поддержки" "https://t.me/support")"

    local pg_pass redis_pass webhook_secret
    pg_pass="$(openssl rand -hex 20)"
    redis_pass="$(openssl rand -hex 20)"
    webhook_secret="$(openssl rand -hex 24)"

    cat > .env <<EOF
BOT_TOKEN=${bot_token}
BOT_MODE=polling
BOT_WEBHOOK_SECRET=${webhook_secret}
ADMIN_IDS=${admin_ids}
THROTTLE_RATE_LIMIT=0.6

WEB_HOST=0.0.0.0
WEB_PORT=8080
WEBHOOK_DOMAIN=${webhook_domain}

POSTGRES_DB=remnabot
POSTGRES_USER=remnabot
POSTGRES_PASSWORD=${pg_pass}
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
DATABASE_URL=postgresql+asyncpg://remnabot:${pg_pass}@postgres:5432/remnabot

REDIS_PASSWORD=${redis_pass}
REDIS_URL=redis://:${redis_pass}@redis:6379/0

REMNAWAVE_URL=${remna_url}
REMNAWAVE_API_TOKEN=${remna_token}
REMNAWAVE_API_TIMEOUT=15.0
REMNAWAVE_API_RETRIES=3
REMNAWAVE_TRAFFIC_STRATEGY=NO_RESET
REMNAWAVE_DEFAULT_SQUADS=${remna_squads}

TRIAL_ENABLED=true
TRIAL_DURATION_DAYS=3
TRIAL_TRAFFIC_GB=10
TRIAL_DEVICE_LIMIT=2

REFERRAL_ENABLED=true
REFERRAL_REWARD_TYPE=percent
REFERRAL_REWARD_PERCENT=15
REFERRAL_REWARD_DAYS=5
REFERRAL_FIRST_PAYMENT_ONLY=false

STARS_RUB_RATE=1.8
CRYPTOBOT_TOKEN=${cryptobot_token}
CRYPTOBOT_TESTNET=false
CRYPTOBOT_ACCEPTED_ASSETS=USDT,TON,BTC,ETH,LTC,TRX,USDC

YOOKASSA_SHOP_ID=${yk_shop_id}
YOOKASSA_SECRET_KEY=${yk_secret}
YOOKASSA_RETURN_URL=
YOOKASSA_TRUST_WEBHOOK_WITHOUT_API_CHECK=false

SUPPORT_URL=${support_url}
SUPPORT_TEXT=Если у вас возникли вопросы по подключению или оплате, напишите в нашу поддержку.

SCHEDULER_EXPIRY_INTERVAL_MINUTES=5
SCHEDULER_TRAFFIC_INTERVAL_MINUTES=10
ENABLE_EXPIRED_CLEANUP=false
EXPIRED_CLEANUP_DAYS=14
LOG_LEVEL=INFO
EOF

    chmod 600 .env
    log_ok "Конфигурация .env создана с правами 600."
}

configure_remnawave_network() {
    cd "${INSTALL_DIR}"
    local attach_net
    attach_net="$(prompt_value "Подключить контейнер к внешней Docker-сети remnawave-network? (y/N)" "N")"
    if [[ "${attach_net}" =~ ^[Yy]$ ]]; then
        if ! docker network inspect remnawave-network >/dev/null 2>&1; then
            log_warn "Внешняя сеть remnawave-network не найдена, создаю её как bridge..."
            docker network create remnawave-network >/dev/null
        fi
        cp -f docker-compose.override.example.yml docker-compose.override.yml
        log_ok "Создан docker-compose.override.yml с подключением к remnawave-network."
    else
        rm -f docker-compose.override.yml
    fi
}

start_stack_and_install_cli() {
    cd "${INSTALL_DIR}"
    chmod +x entrypoint.sh install.sh manage.sh

    log_info "Сборка и запуск Docker-контейнеров..."
    docker compose up -d --build

    ln -sf "${INSTALL_DIR}/manage.sh" "${CLI_BIN}"
    chmod +x "${CLI_BIN}"

    log_info "Ожидание готовности сервисов..."
    sleep 5
    docker compose ps

    echo ""
    log_ok "Установка завершена! Панель управления доступна по команде: remnabot"
}

main() {
    echo "=========================================="
    echo "   Установка remna-shop-bot (Remnawave)   "
    echo "=========================================="
    install_dependencies
    setup_repo
    configure_env
    configure_remnawave_network
    start_stack_and_install_cli
}

main "$@"
