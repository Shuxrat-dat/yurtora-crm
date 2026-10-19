from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("groups", "0006_salary_base_percent")]

    operations = [
        migrations.AddField(
            model_name="enrollment",
            name="custom_price",
            field=models.BigIntegerField(blank=True, null=True, verbose_name="Shaxsiy oylik narx (so'm)"),
        ),
        migrations.AddField(
            model_name="enrollment",
            name="price_note",
            field=models.CharField(blank=True, default="", max_length=120, verbose_name="Narx sababi"),
        ),
        migrations.AddConstraint(
            model_name="enrollment",
            constraint=models.CheckConstraint(
                condition=models.Q(custom_price__isnull=True) | models.Q(custom_price__gte=0), name="enrollment_custom_price_non_negative"
            ),
        ),
    ]
