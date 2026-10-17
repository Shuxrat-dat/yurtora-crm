"""Foydalanuvchilar (o'qituvchi va xodimlar) soni cheklanmaydi: barcha tariflarda limit olib tashlanadi."""

from django.db import migrations


def forwards(apps, schema_editor):
    if schema_editor.connection.schema_name != "public":
        return  # tariflar faqat public sxemada
    apps.get_model("tenants", "Plan").objects.update(max_users=None)


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0003_default_plans"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
