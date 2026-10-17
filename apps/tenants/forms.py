from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .services import normalize_subdomain, validate_subdomain

INPUT = "pub-input"


class CenterSignupForm(forms.Form):
    name = forms.CharField(label="Markaz nomi", max_length=120, widget=forms.TextInput(attrs={"class": INPUT, "placeholder": "Masalan: Ilm Academy", "autocomplete": "organization"}))
    subdomain = forms.CharField(
        label="Manzil",
        max_length=30,
        help_text="Faqat lotin harflari va raqamlar. Masalan: ilm",
        widget=forms.TextInput(attrs={"class": INPUT, "placeholder": "ilm", "autocapitalize": "none", "spellcheck": "false"}),
    )
    contact_name = forms.CharField(label="Ismingiz", max_length=120, widget=forms.TextInput(attrs={"class": INPUT, "autocomplete": "name"}))
    contact_phone = forms.CharField(label="Telefon", max_length=30, widget=forms.TextInput(attrs={"class": INPUT, "placeholder": "+998 90 123 45 67", "autocomplete": "tel"}))
    username = forms.CharField(label="Direktor logini", max_length=150, widget=forms.TextInput(attrs={"class": INPUT, "autocomplete": "username", "autocapitalize": "none"}))
    password1 = forms.CharField(label="Parol", widget=forms.PasswordInput(attrs={"class": INPUT, "autocomplete": "new-password"}))
    password2 = forms.CharField(label="Parolni takrorlang", widget=forms.PasswordInput(attrs={"class": INPUT, "autocomplete": "new-password"}))

    def clean_subdomain(self):
        value = normalize_subdomain(self.cleaned_data["subdomain"])
        validate_subdomain(value)
        return value

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Parollar mos kelmadi.")
        if p1:
            try:
                validate_password(p1)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned


class DemoRequestForm(forms.Form):
    name = forms.CharField(label="Ismingiz", max_length=120, widget=forms.TextInput(attrs={"class": INPUT, "autocomplete": "name", "placeholder": "Masalan: Aziz"}))
    center_name = forms.CharField(label="Markaz nomi", max_length=120, widget=forms.TextInput(attrs={"class": INPUT, "autocomplete": "organization", "placeholder": "Masalan: Ilm Academy"}))
    phone = forms.CharField(label="Telefon", max_length=30, widget=forms.TextInput(attrs={"class": INPUT, "autocomplete": "tel", "inputmode": "tel", "placeholder": "+998 90 123 45 67"}))
    students = forms.ChoiceField(
        label="Taxminan nechta o'quvchi bor?",
        required=False,
        choices=[("", "Ko'rsatilmagan"), ("lt100", "100 tagacha"), ("100-300", "100–300"), ("gt300", "300 dan ko'p")],
        widget=forms.Select(attrs={"class": INPUT}),
    )
    message = forms.CharField(label="Izoh (ixtiyoriy)", max_length=1000, required=False, widget=forms.Textarea(attrs={"class": INPUT, "rows": 3, "placeholder": "Nimalarni ko'rmoqchisiz? Qulay qo'ng'iroq vaqti?"}))
    plan = forms.CharField(required=False, max_length=30, widget=forms.HiddenInput())
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))  # spam tuzog'i: odam ko'rmaydi

    def clean_phone(self):
        raw = self.cleaned_data["phone"].strip()
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not 9 <= len(digits) <= 13:
            raise ValidationError("Telefon raqamini to'liq kiriting, masalan: +998 90 123 45 67")
        return raw

    def clean_plan(self):
        return "".join(ch for ch in self.cleaned_data.get("plan", "") if ch.isalnum() or ch in "-_")[:30]
