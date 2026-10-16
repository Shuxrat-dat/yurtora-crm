"""Django settings for yurtora-crm project."""

from pathlib import Path

import environ

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()

# Читаем .env файл, если он существует (не перезаписывая переменные окружения, переданные явно)
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(env_file, overwrite=False)

# Обязательный параметр: SECRET_KEY не имеет значения по умолчанию
SECRET_KEY = env("SECRET_KEY")

# По умолчанию в продакшене DEBUG выключен (False)
DEBUG = env.bool("DEBUG", default=False)

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# Application definition
# Multi-tenant (django-tenants): har bir o'quv markazi o'z PostgreSQL sxemasida.
# SHARED_APPS — "public" sxemada (markazlar ro'yxati, Yurtora xodimlari, ommaviy sayt).
# TENANT_APPS — har bir markaz sxemasida (butun CRM: foydalanuvchilar, o'quvchilar, to'lovlar...).
SHARED_APPS = [
    "django_tenants",
    "apps.tenants.apps.TenantsConfig",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "apps.accounts.apps.AccountsConfig",
    "django.contrib.admin",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]
TENANT_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "apps.accounts.apps.AccountsConfig",
    "django.contrib.admin",
    "django.contrib.sessions",
    "django.contrib.messages",
    "apps.students.apps.StudentsConfig",
    "apps.teachers.apps.TeachersConfig",
    "apps.courses.apps.CoursesConfig",
    "apps.groups.apps.GroupsConfig",
    "apps.schedule.apps.ScheduleConfig",
    "apps.attendance.apps.AttendanceConfig",
    "apps.finance.apps.FinanceConfig",
    "apps.core.apps.CoreConfig",
    "apps.notifications.apps.NotificationsConfig",
]
INSTALLED_APPS = SHARED_APPS + [app for app in TENANT_APPS if app not in SHARED_APPS]

TENANT_MODEL = "tenants.Center"
TENANT_DOMAIN_MODEL = "tenants.Domain"
DATABASE_ROUTERS = ("django_tenants.routers.TenantSyncRouter",)
PUBLIC_SCHEMA_URLCONF = "config.urls_public"
# search_path faqat sxema almashganda o'rnatiladi (har so'rovdan oldin emas)
TENANT_LIMIT_SET_CALLS = True
# Yurtora asosiy domeni: markazlar <nom>.ROOT_DOMAIN manzilida (lokalda: ilm.localhost:8000)
ROOT_DOMAIN = env.str("ROOT_DOMAIN", default="localhost")
# Yurtora bilan bog'lanish (obuna/to'lov): bloklangan sahifada va "Obuna" sahifasida ko'rsatiladi
YURTORA_SUPPORT = env.str("YURTORA_SUPPORT", default="")

MIDDLEWARE = [
    "apps.tenants.middleware.CenterMiddleware",  # birinchi: manzil bo'yicha markaz sxemasini tanlaydi
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.audit.AuditMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.asset_version",
                "apps.core.context_processors.branding",
                "apps.tenants.views.root_domain",
                "apps.tenants.views.subscription_banner",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Database
# Standart: DATABASE_URL (masalan: postgres://user:password@db:5432/yurtora_crm)
# Faqat PostgreSQL: markazlar alohida sxemalarda (SQLite'da sxema yo'q)
DATABASES = {
    "default": env.db("DATABASE_URL"),
}
DATABASES["default"]["ENGINE"] = "django_tenants.postgresql_backend"

# Kesh kalitlari markaz sxemasi bilan boshlanadi: bir markaz keshi boshqasiga aralashmaydi
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "KEY_FUNCTION": "django_tenants.cache.make_key",
    }
}

# Кастомная модель пользователя
AUTH_USER_MODEL = "accounts.User"

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization
LANGUAGE_CODE = "uz"
TIME_ZONE = "Asia/Tashkent"
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        if not DEBUG
        else "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Jurnal: hamma narsa konsolga (Docker: `docker compose logs web`; fayllar aylanishi compose'da sozlangan).
# Xatolar (500) to'liq izi bilan, kirish/ruxsat muammolari (403/404) ogohlantirish sifatida yoziladi.
# Parol, token va so'rov tanasi jurnalga yozilmaydi. Daraja: LOG_LEVEL (INFO, WARNING, DEBUG).
# Sessiya: faol bo'lmagan foydalanuvchi SESSION_COOKIE_AGE soniyadan keyin chiqariladi (har so'rovda yangilanadi).
# Umumiy kompyuterda unutib qoldirilgan sessiya 2 hafta ochiq turmasligi uchun: standart 12 soat.
SESSION_COOKIE_AGE = env.int("SESSION_COOKIE_AGE", default=60 * 60 * 12)
SESSION_SAVE_EVERY_REQUEST = True

LOG_LEVEL = env.str("LOG_LEVEL", default="INFO").upper()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "apps": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

# Аутентификация: URL маршруты
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:home"
LOGOUT_REDIRECT_URL = "accounts:login"

# Production xavfsizlik sozlamalari (DEBUG=False bo'lganda yoqiladi, env orqali boshqariladi)
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
    SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)
    CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=True)
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True)
    SECURE_CONTENT_TYPE_NOSNIFF = True

# Telegram bot (qarz eslatmalari). Token bo'lmasa funksiya o'chiq turadi.
TELEGRAM_BOT_TOKEN = env.str("TELEGRAM_BOT_TOKEN", default="")
TELEGRAM_BOT_USERNAME = env.str("TELEGRAM_BOT_USERNAME", default="")
TELEGRAM_REMINDER_COOLDOWN_DAYS = env.int("TELEGRAM_REMINDER_COOLDOWN_DAYS", default=3)
TELEGRAM_DIGEST_WEEKDAY = env.int("TELEGRAM_DIGEST_WEEKDAY", default=0)  # 0 = dushanba
TELEGRAM_DIGEST_HOUR = env.int("TELEGRAM_DIGEST_HOUR", default=9)  # Asia/Tashkent
