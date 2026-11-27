#!/usr/bin/env bash
set -Eeuo pipefail

# ==============================================================================
# CLI-менеджер remna-shop-bot (/usr/local/bin/remnabot -> manage.sh)
# ==============================================================================

SOURCE_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_DIR="$(cd "$(dirname "${SOURCE_PATH}")" && pwd)"
BACKUP_DIR="${PROJECT_DIR}/backups"
CLI_BIN="/usr/local/bin/remnabot"

cd "${PROJECT_DIR}"

if [[ -f "${PROJECT_DIR}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${PROJECT_DIR}/.env"
    set +a
fi

pause() {
    echo ""
    read -r -p "Нажмите Enter для возврата в меню..." </dev/tty
}

menu_status_and_logs() {
    echo "=== Статус контейнеров ==="
    docker compose ps
    echo ""
    echo "1) Последние 100 строк логов бота"
    echo "2) Живые логи (Ctrl+C для выхода)"
    echo "0) Назад"
    read -r -p "Выбор: " log_choice </dev/tty
    case "${log_choice}" in
        1) docker compose logs --tail=100 bot; pause ;;
        2) docker compose logs -f --tail=50 bot || true ;;
        *) ;;
    esac
}

menu_update() {
    echo "=== Обновление remna-shop-bot ==="
    git pull --ff-only
    chmod +x entrypoint.sh install.sh manage.sh
    docker compose up -d --build
    docker image prune -f >/dev/null 2>&1 || true
    echo "Обновление и пересборка выполнены."
    pause
}

menu_restart() {
    echo "=== Перезапуск контейнеров ==="
    docker compose restart
    docker compose ps
    pause
}

menu_edit_env() {
    nano "${PROJECT_DIR}/.env"
    read -r -p "Перезапустить бота для применения изменений? (Y/n): " ans </dev/tty
    if [[ ! "${ans}" =~ ^[Nn]$ ]]; then
        docker compose up -d --force-recreate bot
        echo "Контейнер bot перезапущен с новым .env."
    fi
    pause
}

menu_backup_restore() {
    mkdir -p "${BACKUP_DIR}"
    local pg_user="${POSTGRES_USER:-remnabot}"
    local pg_db="${POSTGRES_DB:-remnabot}"

    echo "=== Бэкап и восстановление PostgreSQL ==="
    echo "1) Создать резервную копию (pg_dump)"
    echo "2) Восстановить из резервной копии (psql)"
    echo "0) Назад"
    read -r -p "Выбор: " choice </dev/tty

    case "${choice}" in
        1)
            local ts out_file
            ts="$(date +%Y%m%d_%H%M%S)"
            out_file="${BACKUP_DIR}/remnabot_${ts}.sql.gz"
            docker compose exec -T postgres pg_dump -U "${pg_user}" -d "${pg_db}" --clean --if-exists | gzip > "${out_file}"
            echo "Бэкап сохранён: ${out_file}"
            pause
            ;;
        2)
            mapfile -t backups < <(ls -1t "${BACKUP_DIR}"/*.sql.gz 2>/dev/null || true)
            if [[ "${#backups[@]}" -eq 0 ]]; then
                echo "Резервные копии в ${BACKUP_DIR} не найдены."
                pause
                return
            fi
            echo "Доступные бэкапы:"
            local idx=1
            for b in "${backups[@]}"; do
                echo "  ${idx}) $(basename "${b}")"
                idx=$((idx + 1))
            done
            read -r -p "Введите номер файла для восстановления: " num </dev/tty
            if [[ ! "${num}" =~ ^[0-9]+$ ]] || (( num < 1 || num > ${#backups[@]} )); then
                echo "Некорректный выбор."
                pause
                return
            fi
            local selected="${backups[$((num - 1))]}"
            read -r -p "ВНИМАНИЕ: текущая БД будет перезаписана файлом $(basename "${selected}"). Продолжить? (yes/N): " confirm </dev/tty
            if [[ "${confirm}" != "yes" ]]; then
                echo "Отменено."
                pause
                return
            fi
            docker compose stop bot
            gunzip -c "${selected}" | docker compose exec -T postgres psql -U "${pg_user}" -d "${pg_db}" -v ON_ERROR_STOP=1
            docker compose start bot
            echo "База данных восстановлена из $(basename "${selected}")."
            pause
            ;;
        *) ;;
    esac
}

menu_uninstall() {
    echo "=== Полное удаление remna-shop-bot ==="
    read -r -p "Вы действительно хотите удалить контейнеры и проект? Введите YES для подтверждения: " confirm </dev/tty
    if [[ "${confirm}" != "YES" ]]; then
        echo "Удаление отменено."
        pause
        return
    fi

    read -r -p "Удалить также тома БД (PostgreSQL + Redis)? (y/N): " del_vols </dev/tty
    if [[ "${del_vols}" =~ ^[Yy]$ ]]; then
        docker compose down -v --rmi local
    else
        docker compose down --rmi local
    fi

    rm -f "${CLI_BIN}"
    read -r -p "Удалить директорию ${PROJECT_DIR}? (y/N): " del_dir </dev/tty
    if [[ "${del_dir}" =~ ^[Yy]$ ]]; then
        rm -rf "${PROJECT_DIR}"
        echo "Директория ${PROJECT_DIR} удалена."
    fi
    echo "Удаление завершено."
    exit 0
}

while true; do
    clear || true
    echo "=========================================="
    echo "       RemnaShop Bot — Управление         "
    echo "=========================================="
    echo "1) Статус и логи"
    echo "2) Обновить (git pull + пересборка)"
    echo "3) Перезапустить"
    echo "4) Редактировать .env в nano"
    echo "5) Бэкап и восстановление PostgreSQL"
    echo "6) Полное удаление"
    echo "0) Выход"
    echo "------------------------------------------"
    read -r -p "Выберите пункт: " action </dev/tty
    case "${action}" in
        1) menu_status_and_logs ;;
        2) menu_update ;;
        3) menu_restart ;;
        4) menu_edit_env ;;
        5) menu_backup_restore ;;
        6) menu_uninstall ;;
        0) exit 0 ;;
        *) echo "Неизвестный пункт." ; sleep 1 ;;
    esac
done
