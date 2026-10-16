"""Birinchi direktor akkauntini yaratish.

Parol kodda saqlanmaydi: u so'raladi (getpass). Mavjud foydalanuvchi
o'zgartirilmaydi va o'chirilmaydi.

Ishlatish (ma'lumotlar bazasi sozlamalari .env orqali o'qiladi):
    python create_user.py
"""

import getpass
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.accounts.models import User  # noqa: E402

USERNAME = "director"
MIN_PASSWORD_LENGTH = 10


def main():
    if User.objects.filter(username=USERNAME).exists():
        print(f"{USERNAME} allaqachon mavjud. Hech narsa o'zgartirilmadi.")
        return

    password = getpass.getpass("Direktor paroli: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"Parol kamida {MIN_PASSWORD_LENGTH} belgidan iborat bo'lishi kerak.")
    if password != getpass.getpass("Parolni takrorlang: "):
        raise SystemExit("Parollar mos kelmadi.")

    user = User.objects.create_superuser(
        username=USERNAME,
        password=password,
        email="",
        first_name="Direktor",
        last_name="",
    )
    print(f"Yaratildi: username={user.username!r}, role={user.role}")


if __name__ == "__main__":
    main()
