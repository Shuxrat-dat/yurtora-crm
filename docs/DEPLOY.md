# Деплой Yurtora CRM на сервер

Один сервер (Linux, Docker). Стек из `docker-compose.prod.yml`: PostgreSQL, Django (gunicorn), Caddy (HTTPS), Telegram-бот, резервные копии.

## 0. Что нужно заранее

- Сервер с Docker и Docker Compose v2 (2 vCPU, 2–4 ГБ RAM, 20 ГБ диска).
- Домен, A-запись которого указывает на IP сервера. Порты **80 и 443** открыты (нужны Caddy для сертификата).
- Доступ к GitHub-репозиторию (SSH-ключ или токен только для чтения).

## 1. Получить код

```bash
git clone https://github.com/Shuxrat-dat/yurtora-crm.git
cd yurtora-crm
```

Собирайте образ **только из чистого клона**. Секретов и `.env` в репозитории нет, их создаёте вы на сервере.

## 2. Создать `.env` (секреты только на сервере)

**Быстрый способ (рекомендуется):** скрипт сам создаёт `.env` с новыми случайными `SECRET_KEY` и паролем БД, ставит `DEBUG=False`, подставляет домен и права 600. Существующий `.env` не перезаписывает, секреты на экран не выводит.

```bash
sh scripts/make_env.sh yurtora.uz
nano .env        # вписать TELEGRAM_BOT_TOKEN и TELEGRAM_BOT_USERNAME
```

**Вручную:**

```bash
cp .env.example .env
nano .env
```

Что должно быть заменено (значения из `.env.example` — заглушки):

| Переменная | Что указать |
|---|---|
| `SECRET_KEY` | новая случайная строка: `python3 -c "import secrets; print(secrets.token_urlsafe(60))"` |
| `DEBUG` | `False` |
| `ALLOWED_HOSTS` | `.yurtora.uz,web,localhost` (точка впереди: домен и все поддомены центров) |
| `CSRF_TRUSTED_ORIGINS` | `https://yurtora.uz,https://*.yurtora.uz` |
| `ROOT_DOMAIN` | `yurtora.uz` (центры на `<имя>.yurtora.uz`) |
| `POSTGRES_PASSWORD` | новый пароль **только из букв и цифр** (он попадает в `DATABASE_URL`) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` | токен вашего бота (из @BotFather); без него сервис `bot` не запустится |

Не используйте значения из старого локального `.env`: прежний dev-ключ когда-то попал в историю git, поэтому на сервере нужен новый.

Права на файл: `chmod 600 .env`.

## 3. Запустить

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```

При старте автоматически применяются миграции и собирается статика. Все сервисы должны быть `Up`, `web` и `db` — `healthy`.

## 4. Создать директора

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser
```

Войдите на `https://ваш-домен/`. Дальше администраторов и учителей создаёт директор в разделе «Foydalanuvchilar».

## 5. Проверить после запуска

- [ ] Вход работает, адрес открывается по HTTPS без предупреждений.
- [ ] Откройте любой чек: логотип и QR-код видны (QR ставится вместе с образом, пакет `qrcode`).
- [ ] `docker compose -f docker-compose.prod.yml ps`: сервис `backup` не `unhealthy`; через 10 минут в `./backups` есть файл `yurtora_crm_*.sql.gz` и `LAST_OK`.
- [ ] Проверка восстановления (раз в неделю): `docker compose -f docker-compose.prod.yml run --rm --entrypoint sh backup /scripts/restore_check.sh` — подробности в `docs/RESTORE.md`.
- [ ] Настроена копия вне сервера (`BACKUP_COPY_DIR` / `BACKUP_UPLOAD_CMD`).

## 6. Обновление

```bash
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

Миграции применяются сами. Перед обновлением с изменениями схемы сделайте ручную копию:
`docker compose -f docker-compose.prod.yml run --rm -e RUN_ONCE=1 --entrypoint sh backup /scripts/backup.sh`.

## 7. Откат

```bash
git log --oneline            # найдите прошлую рабочую версию
git checkout <коммит>
docker compose -f docker-compose.prod.yml up -d --build
```

Если новая версия добавила миграции, откатывать код без восстановления базы из копии нельзя: используйте `docs/RESTORE.md`.

## 8. Чего нет в репозитории и что нужно знать

- `.env`, базы, резервные копии, ключи и токены не хранятся в git (`.gitignore`).
- После изменения шаблонов с новыми классами Tailwind нужно выполнить `sh tools/build_css.sh` и закоммитить `static/css/tailwind.css`; на сервере сборка CSS не выполняется.
- Один экземпляр бота на один токен: не запускайте второй (локально и на сервере одновременно), Telegram вернёт конфликт.

## Yurtora: несколько центров на одном сервере

- DNS: две A-записи на IP сервера: `yurtora.uz` и `*.yurtora.uz` (wildcard).
- HTTPS: Caddy получает сертификат для каждого адреса при первом заходе (on-demand), только для `yurtora.uz` и зарегистрированных активных центров (`/_tls/check/`).
- Сотрудник Yurtora (админка `https://yurtora.uz/admin/`, список центров):
  `docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser`
- Новый центр: сам через `https://yurtora.uz/signup/` или командой
  `docker compose -f docker-compose.prod.yml exec web python manage.py create_center ilm "Ilm Academy" director --phone "+998..."`
- Демо-данные в центр (только dev): `python manage.py tenant_command seed_demo --schema=<имя>`.
- Бот один на все центры: в ссылке привязки есть код центра.
- Блокировка центра: в админке снять «Фаол» (данные сохраняются, вход закрыт).
