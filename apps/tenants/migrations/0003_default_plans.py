"""Boshlang'ich tariflar (narx va limitlar — vaqtinchalik, admin panelida o'zgartiriladi) va mavjud markazlarga sinov davri."""

from datetime import timedelta

from django.db import migrations
from django.utils import timezone

PLANS = [
    # code, name, price_month, max_students, max_users, is_default, sort
    ("start", "Start", 299_000, 100, 3, False, 1),
    ("standart", "Standart", 499_000, 300, 10, True, 2),
    ("pro", "Pro", 899_000, None, None, False, 3),
]
TRIAL_DAYS = 14


def forwards(apps, schema_editor):
    # django-tenants RunPython ni har bir markaz sxemasida ham chaqiradi: ma'lumot faqat public da
    if schema_editor.connection.schema_name != "public":
        return
    Plan = apps.get_model("tenants", "Plan")
    Center = apps.get_model("tenants", "Center")
    for code, name, price, students, users, is_default, sort in PLANS:
        Plan.objects.get_or_create(
            code=code,
            defaults={"name": name, "price_month": price, "max_students": students, "max_users": users,
                      "is_default": is_default, "sort": sort},
        )
    default = Plan.objects.get(code="standart")
    trial_end = timezone.localdate() + timedelta(days=TRIAL_DAYS - 1)
    Center.objects.exclude(schema_name="public").filter(
        plan__isnull=True, trial_ends_at__isnull=True, paid_until__isnull=True
    ).update(plan=default, trial_ends_at=trial_end)


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0002_subscription"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
