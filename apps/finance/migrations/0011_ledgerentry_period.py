from django.db import migrations, models
from django.db.models.functions import TruncMonth


def fill_period(apps, schema_editor):
    """Mavjud to'lovlar o'z sanasining oyi uchun hisoblanadi (hozirgi balanslar o'zgarmaydi)."""
    LedgerEntry = apps.get_model("finance", "LedgerEntry")
    LedgerEntry.objects.filter(kind="payment", period__isnull=True).update(period=TruncMonth("entry_date"))


class Migration(migrations.Migration):
    dependencies = [("finance", "0010_receipt_logo")]

    operations = [
        migrations.AddField(
            model_name="ledgerentry",
            name="period",
            field=models.DateField(blank=True, db_index=True, null=True, verbose_name="Qaysi oy uchun"),
        ),
        migrations.RunPython(fill_period, migrations.RunPython.noop),
    ]
