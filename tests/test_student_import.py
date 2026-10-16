"""Excel orqali o'quvchilarni import qilish testlari."""

from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import Workbook

from apps.core.models import AuditLog
from apps.students.importer import normalize_phone
from apps.students.models import Student

pytestmark = pytest.mark.django_db

HEADER = [
    "Familiya",
    "Ism",
    "Otasining ismi",
    "Telefon",
    "Tug'ilgan sana",
    "Jinsi",
    "Maktab",
    "Manba",
    "Manzil",
]


def make_xlsx(rows, header=HEADER):
    wb = Workbook()
    ws = wb.active
    ws.append(header)
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return SimpleUploadedFile("o.xlsx", buf.getvalue())


def post(client, upload):
    return client.post(reverse("students:student_import"), {"file": upload})


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+998 90 123-45-67", "+998901234567"),
        ("90 123 45 67", "+998901234567"),
        ("998901234567", "+998901234567"),
        ("12345", None),
        ("", None),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_valid_file_creates_students(client, admin_user):
    client.force_login(admin_user)
    upload = make_xlsx(
        [
            ["Valiyev", "Ali", "", "90 123 45 67", "15.03.2012", "Erkak", "12-maktab", "Instagram", "Toshkent"],
            ["Karimova", "Zilola", "", "+998935554433", None, "ayol", "", "", ""],
        ]
    )
    resp = post(client, upload)
    assert resp.status_code == 200
    assert Student.objects.count() == 2
    ali = Student.objects.get(first_name="Ali")
    assert ali.phone == "+998901234567"
    assert str(ali.birth_date) == "2012-03-15"
    assert ali.gender == "male" and ali.source == "instagram"
    assert AuditLog.objects.filter(object_repr__startswith="Excel import: 2").exists()


def test_any_error_saves_nothing(client, admin_user):
    client.force_login(admin_user)
    upload = make_xlsx(
        [
            ["Valiyev", "Ali", "", "+998901234567", "", "", "", "", ""],
            ["Xato", "Telefon", "", "123", "", "", "", "", ""],
        ]
    )
    resp = post(client, upload)
    assert Student.objects.count() == 0
    assert resp.context["result"].errors[0][0] == 3  # sarlavha 1-qator, xato 3-qatorda


def test_duplicates_are_skipped(client, admin_user):
    Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901234567")
    client.force_login(admin_user)
    upload = make_xlsx(
        [
            ["Valiyev", "Ali", "", "+998901234567", "", "", "", "", ""],
            ["Yangi", "Vali", "", "+998909998877", "", "", "", "", ""],
            ["Yangi", "Vali", "", "+998909998877", "", "", "", "", ""],
        ]
    )
    resp = post(client, upload)
    assert Student.objects.count() == 2
    assert len(resp.context["result"].skipped) == 2


def test_missing_required_column_and_bad_file(client, admin_user):
    client.force_login(admin_user)
    resp = post(client, make_xlsx([["Ali"]], header=["Ism"]))
    assert not resp.context["result"].ok and Student.objects.count() == 0

    resp = post(client, SimpleUploadedFile("x.xlsx", b"not an excel file"))
    assert not resp.context["result"].ok


def test_access_and_template_download(client, admin_user, teacher_user):
    url = reverse("students:student_import")
    client.force_login(teacher_user)
    assert client.get(url).status_code == 403
    assert client.get(reverse("students:student_import_template")).status_code == 403

    client.force_login(admin_user)
    assert client.get(url).status_code == 200
    resp = client.get(reverse("students:student_import_template"))
    assert resp.status_code == 200
    assert resp["Content-Disposition"].startswith("attachment")
