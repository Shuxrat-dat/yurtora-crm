"""Hisobotlar sahifasi: ro'yxat rol huquqlariga mos va har bir ko'rinadigan havola haqiqatan ochiladi."""

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.core.reports import REPORTS, reports_for

pytestmark = pytest.mark.django_db


@pytest.fixture
def accountant(password):
    return User.objects.create_user("acc", password=password, role=User.Role.ACCOUNTANT)


def keys(user):
    return {r.key for _, items in reports_for(user) for r in items}


def test_visible_reports_per_role(director_user, admin_user, teacher_user, accountant):
    assert keys(director_user) == {r.key for r in REPORTS}  # direktor hammasini ko'radi
    assert keys(admin_user) == {"students_xlsx", "churn", "attendance"}
    assert keys(teacher_user) == {"attendance"}
    assert keys(accountant) == {"cash", "debtors", "students_xlsx"}


@pytest.mark.parametrize("who", ["director_user", "admin_user", "teacher_user", "accountant"])
def test_every_visible_link_opens_for_that_role(client, request, who):
    user = request.getfixturevalue(who)
    client.force_login(user)
    for _, items in reports_for(user):
        for report in items:
            for url in [report.url(), *[e["url"] for e in report.extra_links()]]:
                status = client.get(url).status_code
                assert status == 200, f"{who}: {report.key} -> {url} = {status}"


@pytest.mark.parametrize("who", ["admin_user", "teacher_user", "accountant"])
def test_hidden_reports_are_really_forbidden(client, request, who):
    user = request.getfixturevalue(who)
    client.force_login(user)
    visible = keys(user)
    for report in REPORTS:
        if report.key in visible:
            continue
        # Bu rolga ko'rsatilmagan hisobot sahifasi ochilmasligi kerak (ro'yxat va huquqlar mos)
        assert client.get(report.url()).status_code in (302, 403), f"{who} sees hidden {report.key}"


def test_reports_page_and_sidebar_item(client, director_user, teacher_user):
    for user in (director_user, teacher_user):
        client.force_login(user)
        html = client.get(reverse("reports:reports")).content.decode()
        assert "Hisobotlar" in html and reverse("reports:reports") in html
    client.force_login(director_user)
    html = client.get(reverse("reports:reports")).content.decode()
    for title in ("Moliya paneli", "Qarzdorlar", "Ish haqi hisob-kitobi", "Harakatlar jurnali", "Excel (CSV)"):
        assert title in html


def test_reports_requires_login(client):
    assert client.get(reverse("reports:reports")).status_code == 302
