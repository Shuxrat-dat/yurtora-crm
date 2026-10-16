"""Forms for Group and Enrollment management."""

from django import forms

from apps.accounts.models import User
from apps.accounts.permissions import user_has_role
from apps.groups.models import Enrollment, Group
from apps.students.models import Student


class GroupForm(forms.ModelForm):
    """Guruh ma'lumotlarini kiritish va tahrirlash formasi."""

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # O'qituvchi foizi (FOT) faqat direktor ko'radi va o'zgartiradi
        if user is None or not user_has_role(user, [User.Role.DIRECTOR]):
            self.fields.pop("teacher_percent", None)

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "Tugash sanasi boshlanish sanasidan oldin bo'lishi mumkin emas.")
        max_students = cleaned.get("max_students")
        if max_students is not None and max_students < 1:
            self.add_error("max_students", "Maksimal o'quvchilar soni kamida 1 bo'lishi kerak.")
        if self.instance.pk and max_students:
            current = self.instance.active_students_count
            if max_students < current:
                self.add_error(
                    "max_students", f"Guruhda hozir {current} nafar o'quvchi bor: sig'im bundan kam bo'lishi mumkin emas."
                )
        return cleaned

    class Meta:
        model = Group
        fields = [
            "name",
            "course",
            "teacher",
            "teacher_percent",
            "max_students",
            "start_date",
            "end_date",
            "status",
        ]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "placeholder": "Guruh nomi (masalan: Eng-101)",
                }
            ),
            "course": forms.Select(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                }
            ),
            "teacher": forms.Select(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                }
            ),
            "teacher_percent": forms.NumberInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "min": "0",
                    "max": "100",
                    "step": "1",
                    "placeholder": "Bo'sh: o'qituvchi profilidagi foiz",
                }
            ),
            "max_students": forms.NumberInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                }
            ),
            "start_date": forms.DateInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "type": "date",
                }
            ),
            "end_date": forms.DateInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "type": "date",
                }
            ),
            "status": forms.Select(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                }
            ),
        }


class EnrollStudentForm(forms.Form):
    """Guruhga o'quvchini qo'shish formasi."""

    student = forms.ModelChoiceField(
        queryset=Student.objects.filter(status=Student.Status.ACTIVE),
        label="O'quvchi",
        widget=forms.Select(
            attrs={
                "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"
            }
        ),
    )
    status = forms.ChoiceField(
        choices=[
            (Enrollment.Status.ACTIVE, "Faol a'zo"),
            (Enrollment.Status.TRIAL, "Sinov darsi (Trial)"),
        ],
        initial=Enrollment.Status.ACTIVE,
        label="A'zolik turi",
        widget=forms.Select(
            attrs={
                "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"
            }
        ),
    )
    start_date = forms.DateField(
        required=False,
        label="Boshlanish sanasi",
        widget=forms.DateInput(
            attrs={
                "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                "type": "date",
            }
        ),
    )


class TransferStudentForm(forms.Form):
    """O'quvchini boshqa guruhga ko'chirish formasi."""

    to_group = forms.ModelChoiceField(
        queryset=Group.objects.filter(status=Group.Status.ACTIVE).order_by("name"),
        label="Yangi guruh",
        widget=forms.Select(attrs={"class": "w-full px-3 py-2 border border-slate-300 rounded-lg"}),
    )
    start_date = forms.DateField(
        label="Ko'chirish sanasi",
        widget=forms.DateInput(attrs={"type": "date", "class": "w-full px-3 py-2 border border-slate-300 rounded-lg"}),
    )
    carry_balance = forms.BooleanField(
        required=False,
        label="Balansni (qarz yoki ortiqcha to'lov) yangi guruhga o'tkazish",
    )
    note = forms.CharField(
        required=False,
        label="Izoh",
        widget=forms.TextInput(attrs={"class": "w-full px-3 py-2 border border-slate-300 rounded-lg"}),
    )
