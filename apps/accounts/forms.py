"""Forms for authentication and user management."""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm,
)
from django.contrib.auth.forms import (
    UserChangeForm as BaseUserChangeForm,
)
from django.contrib.auth.forms import (
    UserCreationForm as BaseUserCreationForm,
)

User = get_user_model()


class LoginForm(AuthenticationForm):
    """Tizimga kirish formasi (Tailwind bilan bezatilgan)."""

    username = forms.CharField(
        label="Foydalanuvchi nomi",
        widget=forms.TextInput(
            attrs={
                "class": "w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500",
                "placeholder": "Foydalanuvchi nomini kiriting",
                "autofocus": True,
            }
        ),
    )
    password = forms.CharField(
        label="Parol",
        widget=forms.PasswordInput(
            attrs={
                "class": "w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500",
                "placeholder": "Parolni kiriting",
            }
        ),
    )


class CustomUserCreationForm(BaseUserCreationForm):
    """Admin panelida foydalanuvchi yaratish formasi."""

    class Meta(BaseUserCreationForm.Meta):
        model = User
        fields = ("username", "email", "role")


class CustomUserChangeForm(BaseUserChangeForm):
    """Admin panelida foydalanuvchini tahrirlash formasi."""

    class Meta(BaseUserChangeForm.Meta):
        model = User
        fields = "__all__"


INPUT = "w-full px-3 py-2 border border-slate-300 rounded-lg"


def _style(form):
    for field in form.fields.values():
        if not isinstance(field.widget, forms.CheckboxInput):
            field.widget.attrs.setdefault("class", INPUT)


class _PasswordMixin:
    """Ikki marta kiritilgan parolni tekshiradi (Django parol qoidalari bilan)."""

    def clean_passwords(self, username=""):
        from django.contrib.auth.password_validation import validate_password

        p1 = self.cleaned_data.get("password1")
        p2 = self.cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Parollar mos kelmadi.")
            return None
        if p1:
            candidate = User(username=username)
            try:
                validate_password(p1, candidate)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
                return None
        return p1


class StaffCreateForm(_PasswordMixin, forms.ModelForm):
    """Direktor yangi xodim (administrator, o'qituvchi, direktor) yaratadi: login va parol bilan."""

    password1 = forms.CharField(label="Parol", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label="Parolni takrorlang", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    teacher = forms.ModelChoiceField(
        label="Mavjud o'qituvchi kartasiga ulash",
        queryset=None,
        required=False,
        empty_label="Yangi karta yaratish",
        help_text="Faqat 'O'qituvchi' roli uchun. Tanlanmasa, yangi o'qituvchi kartasi yaratiladi.",
    )
    phone = forms.CharField(label="Telefon (o'qituvchi uchun)", required=False, max_length=20)

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "role"]
        labels = {"username": "Login", "first_name": "Ism", "last_name": "Familiya", "role": "Roli"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.teachers.models import Teacher

        self.fields["teacher"].queryset = Teacher.objects.filter(user__isnull=True, is_active=True).order_by("full_name")
        self.fields["first_name"].required = True
        _style(self)

    def clean(self):
        cleaned = super().clean()
        self.password = self.clean_passwords(cleaned.get("username", ""))
        if cleaned.get("role") == User.Role.TEACHER and not cleaned.get("teacher") and not cleaned.get("phone"):
            self.add_error("phone", "Yangi o'qituvchi kartasi uchun telefon raqami kerak.")
        return cleaned

    def save(self, commit=True):
        from django.db import transaction

        from apps.teachers.models import Teacher

        with transaction.atomic():
            user = super().save(commit=False)
            user.set_password(self.password)
            user.is_staff = False  # Django admin paneliga kirish berilmaydi
            user.save()
            if user.role == User.Role.TEACHER:
                card = self.cleaned_data.get("teacher")
                if card:
                    card.user = user
                    card.save(update_fields=["user", "updated_at"])
                else:
                    full_name = f"{user.first_name} {user.last_name}".strip() or user.username
                    Teacher.objects.create(full_name=full_name, phone=self.cleaned_data["phone"], user=user)
        return user


class StaffUpdateForm(forms.ModelForm):
    """Xodim ma'lumotlari, roli va faolligi (parol alohida o'zgartiriladi)."""

    class Meta:
        model = User
        fields = ["first_name", "last_name", "role", "is_active"]
        labels = {"first_name": "Ism", "last_name": "Familiya", "role": "Roli", "is_active": "Faol (tizimga kira oladi)"}

    def __init__(self, *args, editor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.editor = editor
        _style(self)

    def clean(self):
        cleaned = super().clean()
        # Direktor o'zini bloklab yoki roldan mahrum qilib qo'ymasligi uchun
        if self.editor and self.instance.pk == self.editor.pk:
            if cleaned.get("role") != User.Role.DIRECTOR:
                self.add_error("role", "O'z rolingizni o'zgartira olmaysiz.")
            if not cleaned.get("is_active"):
                self.add_error("is_active", "O'zingizni o'chira olmaysiz.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit and (not user.is_active or user.role not in (User.Role.DIRECTOR, User.Role.ADMINISTRATOR)):
            # Ishdan chiqarilgan yoki o'qituvchi rolga o'tgan xodim Telegram orqali qarz ma'lumotlarini olmasligi kerak
            from apps.notifications.digest import deactivate_chats_of

            deactivate_chats_of(user)
        return user


class StaffPasswordForm(_PasswordMixin, forms.Form):
    """Direktor xodimga yangi parol o'rnatadi."""

    password1 = forms.CharField(label="Yangi parol", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label="Parolni takrorlang", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        _style(self)

    def clean(self):
        cleaned = super().clean()
        self.password = self.clean_passwords(self.user.username if self.user else "")
        return cleaned

    def save(self):
        self.user.set_password(self.password)
        self.user.save(update_fields=["password"])
        return self.user
