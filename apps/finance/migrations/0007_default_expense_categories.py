"""Standart xarajat kategoriyalari migratsiya bilan yaratiladi (ilgari panel ochilganda GET so'rovi yozardi)."""

from django.db import migrations

DEFAULTS = ["Avans", "Marketing", "O'zgarmas xarajatlar", "Ijara va kommunal", "O'quv qurollari"]


def create_defaults(apps, schema_editor):
    Category = apps.get_model("finance", "ExpenseCategory")
    for name in DEFAULTS:
        Category.objects.get_or_create(name=name, defaults={"category_type": "expense", "is_system": True})


class Migration(migrations.Migration):
    dependencies = [("finance", "0006_expenseitem_expense_amount_positive_and_more")]
    operations = [migrations.RunPython(create_defaults, migrations.RunPython.noop)]
