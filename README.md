# Yurtora CRM

O'quv markazlari uchun CRM tizimi (Yurtora mahsuloti). Markaz nomi, chek va Telegram xabarlari har bir markaz sozlamasidan olinadi.
Texnologik stek: **Python 3.12**, Django 5.2 LTS, PostgreSQL 16, HTMX, Tailwind CSS, Docker + Docker Compose, pytest + pytest-django, ruff.

---

## 1. Rollar bo'yicha kirish nazorati (RBAC)

Tizimda `AUTH_USER_MODEL = 'accounts.User'` asosida maxsus `User` modeli va 3 ta rol mavjud:
- `director` (Direktor)
- `administrator` (Administrator)
- `teacher` (O'qituvchi)

### Rolning standart qiymati (Default)
Oddiy foydalanuvchi yaratilganda minimal zaruriy huquqlar printsipi bo'yicha unga **`teacher`** roli beriladi.
`createsuperuser` buyrug'i orqali yaratilgan superuser esa avtomatik ravishda **`director`** rolini oladi.

### `director` rolining global qoidasi
> **`director` roliga ega foydalanuvchi CRM ning barcha himoyalangan sahifalariga so'zsiz kirish huquqiga ega.**
> Agar sahifaga `allowed_roles` (masalan, faqat o'qituvchilar yoki faqat administratorlar) cheklovi qo'yilgan bo'lsa ham, direktor har bir alohida view da ko'rsatilmasdan avtomatik ruxsat oladi.

### Huquq yetarli bo'lmaganda tizim harakati
- Avtorizatsiyadan o'tmagan (anonim) foydalanuvchi yopiq sahifaga kirmoqchi bo'lsa, tizimga kirish sahifasiga (`/login/?next=...`) yo'naltiriladi.
- Agar foydalanuvchi tizimga kirgan bo'lsa, lekin roli yetarli bo'lmasa (va direktor bo'lmasa), server darhol **HTTP 403 Forbidden** (`raise PermissionDenied`) xatosini qaytaradi (login sahifasiga qayta yo'naltirmaydi).

---

## 2. Foydalanuvchi interfeysi va Django Admin

> **Muhim eslatma:** Django admin paneli (`/admin/`) faqat **ishlab chiquvchi (developer)** uchun mo'ljallangan.
> O'quv markazi xodimlari (administratorlar, o'qituvchilar, direktor) uchun barcha ishchi ekranlar, jadvallar va formalar mustaqil ravishda ishlab chiqiladi va to'liq o'zbek tilida (lotin alifbosida) bo'ladi.

Django admin panelida asosiy sarlavhalar o'zbek tiliga moslangan:
- `site_header`: "Yurtora CRM Boshqaruvi"
- `site_title`: "Yurtora CRM Admin"
- `index_title`: "Tizimni boshqarish"

---

## 3. Frontend: Tailwind CSS va HTMX (lokal, CDN'siz)

- **Tailwind CSS 3.4** `static/css/tailwind.css` (~34 KB) sifatida oldindan yig'ilgan va repozitoriyga qo'shilgan.
  Shablon yoki JS ichidagi sinflar o'zgargandan keyin qayta yig'ing: `sh tools/build_css.sh`
  (standalone CLI `tools/tailwindcss.exe` v3.4.17 git'ga kirmaydi; uni
  `github.com/tailwindlabs/tailwindcss/releases/tag/v3.4.17` dan yuklab, `tools/sha256sums.txt` bilan tekshiring).
- **HTMX 2.0.4** va **ApexCharts 3.54.1** `static/vendor/` da lokal saqlanadi.
- `tests/test_no_external_assets.py` shablonlarga tashqi CDN qaytib kirmasligini tekshiradi.
- Production'da statik fayllarni WhiteNoise tarqatadi (`collectstatic` compose'da avtomatik).

---

## 4. Docker Compose orqali ishga tushirish (Asosiy usul)

### 4.1. Muhit parametrlarini tayyorlash
```bash
cp .env.example .env
```
*(Mahalliy ishlab chiqish uchun `.env.example` faylida `DEBUG=True` o'rnatilgan).*

### 4.2. Konteynerlarni yig'ish va ishga tushirish
> **Eslatma:** `docker-compose.yml` dagi web xizmati buyrug'i (`python manage.py runserver 0.0.0.0:8000`) faqat **ishlab chiqish (dev-only)** uchun mo'ljallangan. Ishlab chiqarishda WSGI server (masalan, `gunicorn config.wsgi:application`) ishlatiladi.
> Poyga holatlarining (race condition) oldini olish maqsadida ma'lumotlar bazasi migratsiyalari ishga tushirish buyrug'iga kiritilmagan.

```bash
docker compose up -d --build
```

### 4.3. Migratsiyalar va superuser yaratish
```bash
# PostgreSQL bazasida migratsiyalarni qo'llash
docker compose exec web python manage.py migrate

# Superuser yaratish (avtomatik ravishda director rolini oladi)
docker compose exec web python manage.py createsuperuser
```

### 4.4. Sahifalarni ochish
- Bosh sahifa: [http://localhost:8000/](http://localhost:8000/)
- Django Admin: [http://localhost:8000/admin/](http://localhost:8000/admin/)
- Tizimga kirish: [http://localhost:8000/login/](http://localhost:8000/login/)

> **Xavfsizlik:** PostgreSQL bazasining 5432-porti xostga chiqarilmagan (faqat ichki docker tarmog'ida ishlaydi). `web` xizmati alohida huquqsiz `appuser` foydalanuvchisi ostida ishlaydi (`root` emas).

---

## 5. Testlar va sifat nazorati

> **Qat'iy qoida:** Standart holatda testlar faqat **PostgreSQL** bazasida ishlaydi.
> Runtime sozlamalarida hech qanday yashirin yoki avtomatik SQLite ga o'tish yo'q.
> Agar PostgreSQL mavjud bo'lmasa, testlar darhol xatolik xabari bilan to'xtatiladi.
>
> **Zaxira SQLite varianti:** Faqat `TEST_DB=sqlite` muhit o'zgaruvchisi aniq ko'rsatilgandagina ruxsat etiladi (bunda pytest boshida ogohlantirish beradi).

### 5.1. Asosiy testlar (Docker / PostgreSQL 16)
```bash
docker compose exec web pytest
```

### 5.2. Mahalliy sinov (TEST_DB=sqlite bilan)
```bash
TEST_DB=sqlite pytest
# yoki PowerShell da:
$env:TEST_DB="sqlite"; pytest
```

### 5.3. Django tizim tekshiruvi va linter
```bash
python manage.py check
ruff check .
pip check
```

---

## 6. Python versiyasi muvofiqligi

Loyihada qat'iy **Python 3.12** tanlangan:
- `Dockerfile`: `FROM python:3.12-slim`
- `pyproject.toml`: `requires-python = ">=3.12, <3.13"`, `target-version = "py312"`
- `CONTEXT.md`: `Python 3.12, Django 5.x, PostgreSQL 16`

---

## 5. Zaxira nusxalar va tiklash (production)

`docker-compose.prod.yml` dagi `backup` xizmati har 24 soatda `pg_dump` qiladi va
`./backups/yurtora_crm_<sana>.sql.gz` fayllarini saqlaydi (oxirgi 14 kun, `BACKUP_KEEP_DAYS`).
Har nusxa tekshiriladi; 36 soat ichida muvaffaqiyatli zaxira bo'lmasa, `backup` konteyneri `unhealthy` bo'ladi.

**Muhim:** zaxirani serverdan tashqariga ham nusxalang (`BACKUP_COPY_DIR` / `BACKUP_UPLOAD_CMD`, `.env.example` ga qarang).
Zaxira faqat shu serverda turgan bo'lsa, disk buzilganda u ham yo'qoladi.

Tiklashni tekshirish (ishchi bazaga tegmaydi) va yangi serverda tiklash: **[docs/RESTORE.md](docs/RESTORE.md)**.
```bash
docker compose -f docker-compose.prod.yml run --rm --entrypoint sh backup /scripts/restore_check.sh
```

HTTPS: `proxy` xizmati (Caddy) `.env` dagi `DOMAIN` uchun sertifikatni o'zi oladi; 80 va 443 portlar ochiq bo'lishi kerak.

---

## 6. Telegram bot: qarz eslatmalari

1. Telegram'da **@BotFather** orqali bot yarating, tokenni oling.
2. `.env` ga yozing (repozitoriyga kiritmang): `TELEGRAM_BOT_TOKEN=...`, `TELEGRAM_BOT_USERNAME=...`.
3. Botni ishga tushiring: production'da `bot` xizmati avtomatik (`docker-compose.prod.yml`),
   lokal: `python manage.py run_telegram_bot` (long-polling, port ochish shart emas).
4. O'quvchi kartasida **Telegram** tugmasi → "Ulanish havolasini yaratish" → havolani ota-onaga yuboring
   (bir marta ishlaydi, 72 soat). Ota-ona havolani bosib, **Start** ni bosadi.
5. Eslatma: o'quvchi sahifasidan yoki "Qarz eslatmalari" sahifasidan (avval ro'yxat, keyin tasdiqlash).
   Avtomatik yuborish yo'q. Bir o'quvchiga 3 kundan oldin qayta yuborilmaydi
   (`TELEGRAM_REMINDER_COOLDOWN_DAYS`). Foydalanuvchi `/stop` yozsa, eslatma to'xtaydi.

Qarz summasi ota-onaga ko'rinadi, shuning uchun havolani faqat ishonchli kishiga yuboring.
Yuborilganlar tarixi `NotificationLog` jadvalida saqlanadi.

### Haftalik qarz ro'yxati (avtomatik, tasdiq bilan)

- Xodim (administrator/direktor): "Qarz eslatmalari" sahifasi → "Haftalik ro'yxatni o'z Telegramimga olish" →
  havolani telefonda oching → Start.
- Har hafta (standart: dushanba 09:00, Asia/Tashkent) bot ulangan xodim chatlariga ro'yxat yuboradi:
  nechta qarzdor, jami summa, eng katta qarzlar va tugmalar **Yuborish** / **Bekor qilish**.
- Ota-onalarga eslatma faqat "Yuborish" bosilgandan keyin ketadi. Ro'yxat 24 soat amal qiladi, takroran bosish
  ishlamaydi, tasdiq paytida allaqachon to'lagan o'quvchilar o'tkazib yuboriladi.
- Vaqtni o'zgartirish: `TELEGRAM_DIGEST_WEEKDAY` (0 = dushanba) va `TELEGRAM_DIGEST_HOUR` (.env).
- Jadvalni bot jarayonining o'zi tekshiradi (har ~30 soniyada), alohida cron kerak emas.
