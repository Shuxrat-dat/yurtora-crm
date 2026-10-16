"""Markazni buyruq qatoridan yaratish (Yurtora xodimlari uchun): python manage.py create_center ilm "Ilm Academy" director"""

import getpass

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.tenants.services import create_center


class Command(BaseCommand):
    help = "Yangi o'quv markazi: sxema, manzil va direktor akkaunti"

    def add_arguments(self, parser):
        parser.add_argument("subdomain")
        parser.add_argument("name")
        parser.add_argument("director_username")
        parser.add_argument("--phone", default="")
        parser.add_argument("--contact", default="")

    def handle(self, *args, **opts):
        password = getpass.getpass("Direktor paroli: ")
        if len(password) < 10:
            raise CommandError("Parol kamida 10 belgidan iborat bo'lsin.")
        try:
            center = create_center(
                name=opts["name"], subdomain=opts["subdomain"], director_username=opts["director_username"],
                director_password=password, contact_name=opts["contact"], contact_phone=opts["phone"],
            )
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from None
        self.stdout.write(self.style.SUCCESS(f"Yaratildi: {center.name} -> {center.primary_domain}"))
