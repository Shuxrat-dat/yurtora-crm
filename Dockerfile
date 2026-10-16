FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Системные зависимости (curl для проверок)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Установка Python-зависимостей
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Создание непривилегированного пользователя appuser (UID 1000)
RUN groupadd -r appgroup && useradd -r -u 1000 -g appgroup -d /app appuser

# Копирование исходного кода проекта и назначение прав
COPY . /app/
RUN chown -R appuser:appgroup /app

# Переключение на непривилегированного пользователя
USER appuser

EXPOSE 8000

# По умолчанию — gunicorn (безопасно для production). Dev-compose переопределяет команду на runserver.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-"]
