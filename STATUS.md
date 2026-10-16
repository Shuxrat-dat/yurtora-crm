# STATUS (обновлять после каждого этапа)

## Сделано

- Этап 0: Django 5.2 LTS, PostgreSQL, роли director/administrator/teacher, узбекский интерфейс.

- Этап 1a/1b: Student, Guardian, Teacher, Course, Group, Enrollment, экраны, доступ ролей.

- Этап 2: schedule и attendance добавлены, миграции готовы. enroll_student уже использует transaction.atomic() + select_for_update().

- Этап 3a: finance app, LedgerEntry (immutable), accrue_lesson_charges (детерминированная формула: floor(P*i/n) - floor(P*(i-1)/n)), payments, excused_credit, adjustment, get_balance, get_debtors. mark_lesson_held в schedule/services.py автоматически начисляет через accrue_lesson_charges. Права: admin/director для payment/excused, director для adjustment, teacher только свои группы для mark_lesson_held. Все тесты pass (59/59 на SQLite).

- Этап 3b-2: Система зарплат и раздел Moliya:
  - accrue_lesson_charges проверяет frozen_from: урок во время заморозки (даже если отмечен проведённым после разморозки) не начисляется.
  - unfreeze_enrollment: право только у administrator и director (teacher не может размораживать).
  - Group.teacher_percent (0-100, default 0), Teacher.pay_type (percent/fixed), Teacher.monthly_salary (обязателен для fixed).
  - PayrollMonth, PayrollLine, PayrollAdjustment, SalaryPayout (все immutable с ImmutableManager).
  - compute_payroll(year, month): для процентной зарплаты база = сумма LedgerEntry kind=payment с entry_date в месяце, начислено = base * teacher_percent // 100. Для fixed: monthly_salary.
  - close_payroll_month: только director, месяц должен закончиться, сохраняет PayrollLine (повторное закрытие запрещено).
  - register_payment: запрещает entry_date в закрытом месяце зарплат.
  - Экраны: Moliya (дашборд: тушум, ish haqi to'langan, farq, qarzdorlik, разбивка по способам/группам), Oylik hisob-kitob (ведомость, кнопка "Oyni yopish" для director, CSV экспорт), Mening oyligim (teacher видит только своё, 403 на чужое).
  - Тесты: процент от оплат (не начислений), границы месяцев, fixed независим, закрытый месяц неизменен, платёж в закрытый запрещён, повторное закрытие запрещено, неизменяемость моделей, teacher 403 на полную ведомость, teacher только своё.

- Этап 4 (аудит и production-готовность):
  - gunicorn + whitenoise, `docker-compose.prod.yml`, `.dockerignore`, HTTPS-настройки по env при DEBUG=False.
  - django-axes (защита от перебора пароля) убрана по решению владельца: мешала в работе. Без неё нужен лимит попыток на уровне прокси при выходе в интернет.
  - Бэкапы: сервис `backup` (pg_dump раз в сутки, 14 дней), восстановление описано в README.
  - Журнал действий `core.AuditLog` (неизменяемый), страница `/audit/` только для директора. Пишет только изменения в рамках HTTP-запроса.
  - Tailwind (3.4.17 standalone) собирается локально в `static/css/tailwind.css`; HTMX и ApexCharts в `static/vendor/`; CDN нет (тест `test_no_external_assets`).
  - CI: `.github/workflows/ci.yml` (PostgreSQL 16, Python 3.12). Remote у репозитория не настроен, CI ещё ни разу не запускался.
  - Пагинация групп и преподавателей; причина архивации ученика + отчёт `/students/churn/`; импорт учеников из Excel (`/students/import/`).
  - Telegram-бот (apps.notifications): привязка по одноразовой ссылке, ручная рассылка напоминаний о долге с предпросмотром, кулдаун 3 дня, журнал отправок, команда `run_telegram_bot` (long-polling), сервис `bot` в prod-compose. HTTP-клиент на urllib, без новых зависимостей.
  - Недельная сводка должников директору/администратору в Telegram (apps/notifications/digest.py): по расписанию (пн 09:00), отправка родителям только после кнопки «Yuborish», защита от повторного нажатия, TTL 24 ч, проверка прав по чату. Расписание проверяет сам процесс бота.
  - Управление пользователями (`/staff/`, только директор): создание администратора/преподавателя/директора с логином и паролем, смена пароля, отключение, привязка к карточке преподавателя.

## Не сделано / хвосты

- Тесты прогнаны на PostgreSQL 16 в Docker (13.10.2026): все проходят, включая 3 теста параллельных начислений, которые на SQLite пропускаются.

- Python локально 3.14, в Docker и CI 3.12. Локальную среду стоит перевести на 3.12.

- docker-compose.prod.yml и backup-сервис проверены только синтаксически (dev-compose с web, db, bot работает). CI не запускался: у репозитория нет remote.

- SMS-уведомления не делались. Telegram-бот готов (apps/notifications), привязка чата проверена владельцем на реальном боте. Бот запускается сервисом `bot` в docker-compose.yml.

- 1c (trial/frozen, чёрный список, source, school): частично сделано (trial/frozen уже в models, не хватает blacklist в Enrollment).

- Смена преподавателя группы в середине месяца: зарплата считается на текущего преподавателя (история смены не отслеживается).

## Бизнес-правила (от первого заказчика, база продукта)

- 1 филиал, ~500 учеников, рост до ~1000. Интерфейс только на узбекском (латиница).

- Оплата ПО УРОКАМ. Пробный урок бесплатный. Платный период начинается
  с даты paid_from, которую задаёт администратор.

- Возвратов денег нет. Пропуск с причиной: администратор вручную переносит
  оплату на следующий месяц (отдельная корректирующая запись).

- Заморозка: ученик остаётся в группе, начисления остановлены. Замораживать
  может учитель (только свои группы). Нужен журнал и отчёт для директора.

- Зарплата: основной преподаватель получает ПРОЦЕНТ от оплат, полученных
  за месяц; support-преподаватель получает фиксированную ставку.

- Расходы: только зарплаты преподавателей. Главный отчёт: сколько пришло
  от учеников и сколько выплачено преподавателям.

## Архитектурные решения

- Деньги: BigInteger, сумы. Баланс НЕ хранится, считается из неизменяемых
  записей (начисление, оплата, корректировка). Правка записей запрещена.

- Бизнес-логика в services.py, не во view. Права проверяются на сервере.

- Без новых зависимостей без причины. Тесты на PostgreSQL по умолчанию.

## Вне MVP

Лиды, SMS, Telegram, экзамены, Face ID/QR, мобильное приложение,
мультифилиальность, кабинет ученика, бейджи.

## Открытые вопросы к заказчику

- Процент преподавателя один для всех групп или разный?

- Размораживает ученика администратор вручную или только после оплаты?

- Как заказчик называет цену: за месяц фиксированно или за занятие?

- Нужна ли ведомость зарплат за месяц?

## Следующая задача

[вставить текущую: 1c, затем этап 2, затем 3a]

- Этапы 0, 1a, 1b, 1c (trial/frozen, чёрный список, source, school).
- Этап 2: приложения schedule и attendance есть в коде (проверить полным pytest).
- groups.enroll_student: transaction.atomic + select_for_update.

## Хвосты
- Полный pytest на PostgreSQL ни разу не запускался.
- test_race_condition_concurrent_enrollment сейчас последовательный и гонку
  не проверяет. Настоящий тест возможен только на Postgres.
- Python 3.14 локально, 3.12 в Docker.
- Не проверено: makemigrations --check.