"""Forms for Teacher management."""

from django import forms

from apps.core.forms import MoneyField, MoneyInput

from apps.accounts.models import User
from apps.accounts.permissions import user_has_role
from apps.teachers.models import Teacher

# Ish haqi maydonlari: faqat direktor ko'radi va o'zgartiradi (FOT).
SALARY_FIELDS = ("pay_type", "monthly_salary", "base_percent")


class TeacherForm(forms.ModelForm):
    """O'qituvchi ma'lumotlarini kiritish va tahrirlash formasi."""

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # user berilmasa yoki direktor bo'lmasa, ish haqi maydonlari formada bo'lmaydi
        if user is None or not user_has_role(user, [User.Role.DIRECTOR]):
            for name in SALARY_FIELDS:
                self.fields.pop(name, None)

    class Meta:
        model = Teacher
        fields = ["full_name", "phone", "user", "pay_type", "monthly_salary", "base_percent", "is_active"]
        field_classes = {"monthly_salary": MoneyField}
        widgets = {
            "full_name": forms.TextInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "placeholder": "To'liq ism-familiya",
                }
            ),
            "phone": forms.TextInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "placeholder": "+998901234567",
                }
            ),
            "user": forms.Select(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                }
            ),
            "pay_type": forms.Select(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                }
            ),
            "monthly_salary": MoneyInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "placeholder": "Oylik maosh (so'm)",
                }
            ),
            "base_percent": forms.NumberInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "min": "0",
                    "max": "100",
                    "step": "1",
                    "placeholder": "Masalan: 40",
                }
            ),
            "is_active": forms.CheckboxInput(
                attrs={
                    "class": "h-4 w-4 text-indigo-600 rounded border-slate-300 focus:ring-indigo-500"
                }
            ),
        }
