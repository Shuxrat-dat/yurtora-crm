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
