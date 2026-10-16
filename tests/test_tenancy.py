"""Multi-tenancy: markazlar ma'lumotlari ajratilgan, manzil bo'yicha yo'naltirish, ro'yxatdan o'tish, umumiy bot."""

import html

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.core.branding import center_name
from apps.finance.models import ReceiptSettings
from apps.notifications import routing, services
from apps.notifications.models import TelegramContact
from apps.students.models import Student
from apps.tenants.models import Center
from apps.tenants.services import create_center

pytestmark = pytest.mark.django_db
PASSWORD = "Strong-Pass-2026!"


@pytest.fixture
def other_center():
    """Ikkinchi markaz (sxema test tranzaksiyasi bilan birga bekor qilinadi)."""
    center = create_center(
        name="Boshqa markaz", subdomain="other", director_username="boss", director_password=PASSWORD
    )
    connection.set_tenant(Center.objects.get(schema_name="testcenter"))
    return center


def signup_payload(**overrides):
    data = {
        "name": "Ilm Academy", "subdomain": "ilm", "contact_name": "Aziz", "contact_phone": "+998901112233",
        "username": "director", "password1": PASSWORD, "password2": PASSWORD,
    }
    data.update(overrides)
    return data


# --- data isolation -----------------------------------------------------------


def test_centers_do_not_see_each_others_data(other_center):
    Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998900000001")
    assert Student.objects.count() == 1
    with schema_context("other"):
        assert Student.objects.count() == 0
        assert list(User.objects.values_list("username", flat=True)) == ["boss"]
        assert ReceiptSettings.get_settings().center_name == "Boshqa markaz"
    assert not User.objects.filter(username="boss").exists()


def test_login_of_one_center_does_not_work_in_another(other_center, director_user, password):
    other = Client(HTTP_HOST="other.localhost")
    assert other.post("/login/", {"username": director_user.username, "password": password}).status_code == 200  # forma xatosi
    assert other.post("/login/", {"username": "boss", "password": PASSWORD}).status_code == 302
    own = Client(HTTP_HOST="testcenter.localhost")
    assert own.post("/login/", {"username": "boss", "password": PASSWORD}).status_code == 200


def test_center_name_cache_is_per_center(other_center):
    settings_obj = ReceiptSettings.get_settings()
    settings_obj.center_name = "Test markaz nomi"
    settings_obj.save()
    assert center_name() == "Test markaz nomi"
    with schema_context("other"):
        assert center_name() == "Boshqa markaz"
    assert center_name() == "Test markaz nomi"


# --- host routing -------------------------------------------------------------


def test_public_site_and_center_routing(public_client):
    landing = public_client.get("/")
    assert landing.status_code == 200 and "Markazni ulash" in landing.content.decode()
    assert public_client.get("/login/").status_code == 404  # CRM faqat markaz manzilida
    assert Client(HTTP_HOST="testcenter.localhost").get("/login/").status_code == 200


def test_unknown_center_address_is_404_not_someone_elses_data():
    response = Client(HTTP_HOST="nope.localhost").get("/login/")
    assert response.status_code == 404 and "Markaz topilmadi" in response.content.decode()


def test_blocked_center_shows_blocked_page():
    Center.objects.filter(schema_name="testcenter").update(is_active=False)
    response = Client(HTTP_HOST="testcenter.localhost").get("/login/")
    assert response.status_code == 403 and "to'xtatilgan" in response.content.decode()


def test_internal_endpoints_work_from_any_host():
    internal = Client(HTTP_HOST="localhost")
    assert internal.get("/healthz/").content == b"ok"
    assert internal.get("/_tls/check/", {"domain": "testcenter.localhost"}).status_code == 200
    assert internal.get("/_tls/check/", {"domain": "localhost"}).status_code == 200
    assert internal.get("/_tls/check/", {"domain": "evil.example"}).status_code == 404
    assert Client(HTTP_HOST="testcenter.localhost").get("/healthz/").content == b"ok"


# --- signup -------------------------------------------------------------------


def test_signup_creates_center_and_director(public_client):
    response = public_client.post("/signup/", signup_payload())
    assert response.status_code == 302
    assert response["Location"] == "http://ilm.localhost/login/?welcome=1"
    center = Center.objects.get(schema_name="ilm")
    assert center.name == "Ilm Academy" and center.primary_domain == "ilm.localhost"
    with schema_context("ilm"):
        director = User.objects.get(username="director")
        assert director.role == User.Role.DIRECTOR and director.check_password(PASSWORD)
        assert center_name() == "Ilm Academy"
    ilm = Client(HTTP_HOST="ilm.localhost")
    assert "Markazingiz tayyor" in ilm.get("/login/?welcome=1").content.decode()
    assert ilm.post("/login/", {"username": "director", "password": PASSWORD}).status_code == 302


@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"subdomain": "admin"}, "subdomain"),
        ({"subdomain": "testcenter"}, "subdomain"),
        ({"subdomain": "1abc"}, "subdomain"),
        ({"subdomain": "ab"}, "subdomain"),
        ({"subdomain": "ilm-academy"}, "subdomain"),
        ({"password2": "Other-Pass-2026!"}, "password2"),
        ({"password1": "123", "password2": "123"}, "password1"),
    ],
)
def test_signup_validation(public_client, overrides, field):
    response = public_client.post("/signup/", signup_payload(**overrides))
    assert response.status_code == 200
    assert field in response.context["form"].errors
    assert not Center.objects.exclude(schema_name="testcenter").exists()


def test_signup_is_rate_limited_per_ip(public_client):
    with schema_context("public"):  # kesh kalitlari sxema bo'yicha: ro'yxatdan o'tish public qismida
        cache.set("signup:127.0.0.1", 5, 3600)
    response = public_client.post("/signup/", signup_payload(), REMOTE_ADDR="127.0.0.1")
    assert response.status_code == 200 and "Juda ko'p urinish" in html.unescape(response.content.decode())
    assert not Center.objects.filter(schema_name="ilm").exists()


def test_yurtora_staff_admin_lists_centers(public_client):
    with schema_context("public"):
        User.objects.create_superuser("yurtora", "", PASSWORD)
    login = public_client.post("/admin/login/", {"username": "yurtora", "password": PASSWORD, "next": "/admin/"})
    assert login.status_code == 302 and login["Location"] == "/admin/"
    page = public_client.get("/admin/tenants/center/")
    assert page.status_code == 200 and "testcenter" in page.content.decode()


# --- shared Telegram bot -------------------------------------------------------


def _start(chat_id, payload):
    return routing.handle_update({"message": {"chat": {"id": chat_id, "type": "private"}, "text": f"/start {payload}"}})


def test_bot_links_parent_in_the_right_center(other_center):
    student = Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998900000001")
    token = services.create_link_token(student)
    chat_id, reply = _start(555, f"testcenter_{token.token}")
    assert "Valiyev" in reply and TelegramContact.objects.filter(chat_id=555).exists()
    with schema_context("other"):
        assert not TelegramContact.objects.exists()


def test_bot_rejects_token_used_with_another_center_or_unknown_center(other_center):
    student = Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998900000001")
    token = services.create_link_token(student)
    assert _start(555, f"other_{token.token}")[1] == routing.INVALID_LINK  # token boshqa markazniki
    assert _start(555, f"nope_{token.token}")[1] == routing.INVALID_LINK  # bunday markaz yo'q
    assert _start(555, token.token)[1] == routing.INVALID_LINK  # markaz kodisiz eski havola
    assert not TelegramContact.objects.exists()


def test_bot_stop_unlinks_chat_in_all_centers(other_center):
    s1 = Student.objects.create(first_name="Ali", last_name="Valiyev", phone="+998900000001")
    _start(555, f"testcenter_{services.create_link_token(s1).token}")
    with schema_context("other"):
        s2 = Student.objects.create(first_name="Vali", last_name="Aliyev", phone="+998900000002")
        token2 = services.create_link_token(s2)
    _start(555, f"other_{token2.token}")
    _, reply = routing.handle_update({"message": {"chat": {"id": 555, "type": "private"}, "text": "/stop"}})
    assert reply == "Bildirishnomalar o'chirildi."
    assert not TelegramContact.objects.filter(is_active=True).exists()
    with schema_context("other"):
        assert not TelegramContact.objects.filter(is_active=True).exists()


def test_bot_ignores_group_chats_and_helps_without_payload():
    assert routing.handle_update({"message": {"chat": {"id": 1, "type": "group"}, "text": "/start"}}) is None
    connection.set_schema_to_public()
    _, reply = routing.handle_update({"message": {"chat": {"id": 1, "type": "private"}, "text": "/start"}})
    assert "Yurtora CRM" in reply
