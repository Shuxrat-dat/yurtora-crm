"""F-01: повторная отправка формы платежа не создаёт второй платёж."""

import re
from datetime import date

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.courses.models import Course
from apps.finance.models import LedgerEntry
from apps.finance.services import DuplicatePayment, register_payment
from apps.groups.models import Enrollment, Group
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    teacher = Teacher.objects.create(full_name="Ustoz", phone="+998900000000")
    course = Course.objects.create(name="English", monthly_price=400000)
    group = Group.objects.create(name="ENG-1", course=course, teacher=teacher, start_date="2026-01-01")
    student = Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998901112233")
    Enrollment.objects.create(student=student, group=group, status="active", start_date=date(2026, 9, 1))
    return {"student": student, "group": group}


def payments():
    return LedgerEntry.objects.filter(kind="payment").count()


def data(world, key="k-1", **extra):
    base = {
        "student_id": world["student"].pk, "group_id": world["group"].pk, "amount": "100000",
        "method": "cash", "entry_date": "2026-10-10", "comment": "", "idempotency_key": key,
    }
    base.update(extra)
    return base


@pytest.mark.parametrize("route", ["finance:payment_add", "finance:quick_payment"])
def test_double_submit_creates_one_payment(client, director_user, world, route):
    client.force_login(director_user)
    url = reverse(route)
    first = client.post(url, data(world), follow=True)
    second = client.post(url, data(world), follow=True)
    assert payments() == 1
    assert any("to'lov qabul qilindi" in str(m) for m in first.context["messages"])
    assert any("allaqachon qabul qilingan" in str(m) for m in second.context["messages"])


def test_payment_create_view_double_submit(client, director_user, world):
    client.force_login(director_user)
    url = reverse("finance:payment_create", args=[world["student"].pk, world["group"].pk])
    body = {"amount": "100000", "method": "cash", "entry_date": "2026-10-10", "idempotency_key": "k-2"}
    client.post(url, body)
    client.post(url, body)
    assert payments() == 1


def test_different_keys_create_separate_payments(client, director_user, world):
    client.force_login(director_user)
    url = reverse("finance:payment_add")
    client.post(url, data(world, key="a"))
    client.post(url, data(world, key="b"))
    assert payments() == 2


def test_payment_without_key_still_works(client, director_user, world):
    client.force_login(director_user)
    body = data(world)
    body.pop("idempotency_key")
    client.post(reverse("finance:payment_add"), body)
    client.post(reverse("finance:payment_add"), body)
    assert payments() == 2  # без ключа защиты нет (старые клиенты), но и ошибок нет


def test_service_raises_duplicate_and_keeps_single_entry(world):
    register_payment(world["student"], world["group"], 5000, "cash", None, date(2026, 10, 10), idempotency_key="svc-1")
    with pytest.raises(DuplicatePayment):
        register_payment(world["student"], world["group"], 5000, "cash", None, date(2026, 10, 10), idempotency_key="svc-1")
    assert payments() == 1


def test_database_constraint_blocks_duplicate_key(world):
    """Даже если гонка пройдёт проверку в коде, уникальное ограничение БД не даст вторую запись."""
    kwargs = {
        "student": world["student"], "group": world["group"], "kind": "payment", "amount": 1,
        "method": "cash", "entry_date": date(2026, 10, 10),
    }
    LedgerEntry.objects.create(idempotency_key="db-1", **kwargs)
    with pytest.raises(IntegrityError), transaction.atomic():
        LedgerEntry.objects.create(idempotency_key="db-1", **kwargs)
    LedgerEntry.objects.create(**kwargs)  # записи без ключа не конфликтуют между собой
    LedgerEntry.objects.create(**kwargs)


def test_payment_form_renders_unique_key_and_blocks_double_click(client, director_user, world):
    client.force_login(director_user)
    url = reverse("finance:payment_modal_form", args=[world["student"].pk])
    first = client.get(url).content.decode()
    second = client.get(url).content.decode()
    keys = [re.search(r'name="idempotency_key" value="([0-9a-f]{32})"', page).group(1) for page in (first, second)]
    assert keys[0] != keys[1]
    assert "data-once" in first


@pytest.mark.django_db(transaction=True)
def test_concurrent_submits_with_same_key_create_one_payment(world):
    """Два потока одновременно: ровно один платёж (только PostgreSQL)."""
    import threading

    from django.db import connection

    if connection.vendor != "postgresql":
        pytest.skip("Параллельный тест требует PostgreSQL")
    barrier = threading.Barrier(2)
    results = []
    center = connection.tenant  # har bir oqim o'z ulanishini ochadi: markaz sxemasini o'rnatamiz

    def worker():
        connection.set_tenant(center)
        try:
            barrier.wait()
            register_payment(world["student"], world["group"], 7000, "cash", None, date(2026, 10, 10), idempotency_key="race-1")
            results.append("ok")
        except DuplicatePayment:
            results.append("duplicate")
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["duplicate", "ok"]
    assert LedgerEntry.objects.filter(idempotency_key="race-1").count() == 1
