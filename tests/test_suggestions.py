"""«Taklif yuborish»: markaz taklifni Yurtora egasiga yuboradi (public sxemada saqlanadi)."""

import pytest
from django.urls import reverse
from django_tenants.utils import get_public_schema_name, schema_context

from apps.tenants.billing_views import SUGGEST_LIMIT_PER_HOUR
from apps.tenants.models import Center, Suggestion

pytestmark = pytest.mark.django_db

TEXT = "Davomat jadvalini Excelga yuklab olish imkoniyati kerak."


def public_suggestions():
    with schema_context(get_public_schema_name()):
        return list(Suggestion.objects.select_related("center"))


def test_director_sends_suggestion_to_public_schema(client, director_user):
    client.force_login(director_user)
    response = client.post(reverse("billing:suggest"), {"text": TEXT, "contact": "+998901112233"})
    assert response.status_code == 302 and response.url == reverse("accounts:home")
    [s] = public_suggestions()
    assert s.text == TEXT and s.contact == "+998901112233" and s.status == "new"
    assert s.center == Center.objects.get(schema_name="testcenter")
    assert s.author == director_user.username and s.role == director_user.get_role_display()


def test_administrator_can_send_teacher_cannot(client, admin_user, teacher_user):
    client.force_login(admin_user)
    assert client.post(reverse("billing:suggest"), {"text": TEXT}).status_code == 302
    client.force_login(teacher_user)
    assert client.get(reverse("billing:suggest")).status_code == 403
    assert client.post(reverse("billing:suggest"), {"text": TEXT}).status_code == 403
    assert len(public_suggestions()) == 1


def test_short_text_rejected(client, director_user):
    client.force_login(director_user)
    response = client.post(reverse("billing:suggest"), {"text": "qisqa"})
    assert response.status_code == 200 and "kamida 10" in response.content.decode()
    assert public_suggestions() == []


def test_hourly_limit(client, director_user):
    client.force_login(director_user)
    for _ in range(SUGGEST_LIMIT_PER_HOUR):
        assert client.post(reverse("billing:suggest"), {"text": TEXT}).status_code == 302
    blocked = client.post(reverse("billing:suggest"), {"text": TEXT})
    assert blocked.status_code == 200 and "juda ko&#x27;p taklif" in blocked.content.decode()
    assert len(public_suggestions()) == SUGGEST_LIMIT_PER_HOUR


def test_sidebar_card_roles(client, director_user, admin_user, teacher_user):
    expected = {director_user: True, admin_user: True, teacher_user: False}
    for user, shown in expected.items():
        client.force_login(user)
        html = client.get(reverse("accounts:home")).content.decode()
        assert (reverse("billing:suggest") in html) is shown
