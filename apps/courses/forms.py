"""Forms for Course management."""

from django import forms

from apps.core.forms import MoneyField, MoneyInput
from apps.courses.models import Course


class CourseForm(forms.ModelForm):
    """Kurs ma'lumotlarini kiritish va tahrirlash formasi."""

    class Meta:
        model = Course
        fields = ["name", "level", "monthly_price", "is_active"]
        field_classes = {"monthly_price": MoneyField}
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "placeholder": "Masalan: General English, Matematika",
                }
            ),
            "level": forms.TextInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "placeholder": "Masalan: Beginner, Intermediate, IELTS",
                }
            ),
            "monthly_price": MoneyInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "placeholder": "Masalan: 450 000",
                }
            ),
            "is_active": forms.CheckboxInput(
                attrs={
                    "class": "h-4 w-4 text-indigo-600 rounded border-slate-300 focus:ring-indigo-500"
                }
            ),
        }
