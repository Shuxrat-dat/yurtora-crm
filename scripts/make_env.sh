#!/bin/sh
# Создаёт серверный .env из .env.example с НОВЫМИ случайными секретами (SECRET_KEY, пароль БД).
#
#   sh scripts/make_env.sh yurtora.uz
#
# Что делает: подставляет домен в ALLOWED_HOSTS / CSRF_TRUSTED_ORIGINS / ROOT_DOMAIN (+ все поддомены центров), генерирует SECRET_KEY и пароль БД
# (только буквы и цифры: он попадает в DATABASE_URL), ставит DEBUG=False, права 600.
# Токен Telegram-бота нужно вписать вручную (он выдаётся в @BotFather и нигде не хранится в репозитории).
# Существующий .env не перезаписывается. Сами секреты на экран не выводятся.
set -eu

DOMAIN="${1:-}"
[ -n "$DOMAIN" ] || { echo "Использование: sh scripts/make_env.sh <домен, например yurtora.uz>" >&2; exit 2; }
cd "$(dirname "$0")/.."
[ -f .env.example ] || { echo "Не найден .env.example" >&2; exit 2; }
[ ! -e .env ] || { echo ".env уже существует: удалите или переименуйте его, если хотите создать новый." >&2; exit 1; }

PY="$(command -v python3 || command -v python)"
[ -n "$PY" ] || { echo "Нужен python3" >&2; exit 2; }

DOMAIN="$DOMAIN" "$PY" - <<'PYEOF'
import os
import re
import secrets
import string

domain = os.environ["DOMAIN"].strip()
if not re.fullmatch(r"[A-Za-z0-9.-]+", domain):
    raise SystemExit("Домен может содержать только буквы, цифры, точку и дефис.")

alphabet = string.ascii_letters + string.digits
db_password = "".join(secrets.choice(alphabet) for _ in range(32))
values = {
    "SECRET_KEY": secrets.token_urlsafe(60),
    "DEBUG": "False",
    "ALLOWED_HOSTS": f".{domain},web,localhost",
    "POSTGRES_PASSWORD": db_password,
    "POSTGRES_HOST": "db",
    "DATABASE_URL": f"postgres://yurtora_user:{db_password}@db:5432/yurtora_crm",
}
extra = {
    "CSRF_TRUSTED_ORIGINS": f"https://{domain},https://*.{domain}",
    "ROOT_DOMAIN": domain,
}
replace = {**values, **extra}

lines, seen = [], set()
for raw in open(".env.example", encoding="utf-8").read().splitlines():
    match = re.match(r"^(?:#\s*)?([A-Z_]+)=", raw)
    key = match.group(1) if match else ""
    if key in replace and key not in seen:
        lines.append(f"{key}={replace[key]}")
        seen.add(key)
    else:
        lines.append(raw)
for key, value in extra.items():
    if key not in seen:
        lines.append(f"{key}={value}")

with open(".env", "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
PYEOF

chmod 600 .env
echo ".env создан с новыми SECRET_KEY и паролем БД (права 600)."
echo "Осталось вручную: TELEGRAM_BOT_TOKEN и TELEGRAM_BOT_USERNAME (@BotFather), затем: docker compose -f docker-compose.prod.yml up -d --build"
