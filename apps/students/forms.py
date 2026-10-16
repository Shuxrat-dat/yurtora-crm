"""Forms for Student and Guardian management."""

from django import forms

from apps.students.models import Guardian, Student

PHONE_ERROR = "Telefon raqami noto'g'ri. To'g'ri format: +998901234567 (9 ta raqam kod bilan)."


def clean_phone_value(raw):
    """Telefonni +998XXXXXXXXX ko'rinishiga keltiradi; noto'g'ri bo'lsa ValidationError."""
    from apps.students.importer import normalize_phone

    phone = normalize_phone(raw)
    if phone is None:
        raise forms.ValidationError(PHONE_ERROR)
    return phone


class StudentForm(forms.ModelForm):
    """O'quvchi ma'lumotlarini kiritish va tahrirlash formasi."""

    class Meta:
        model = Student
        fields = [
            "first_name",
            "last_name",
            "middle_name",
            "phone",
            "birth_date",
            "gender",
            "school",
            "source",
            "address",
            "status",
            "is_blacklisted",
            "blacklist_reason",
            "joined_date",
            "notes",
        ]
        widgets = {
            "first_name": forms.TextInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                }
            ),
            "last_name": forms.TextInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                }
            ),
            "middle_name": forms.TextInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                }
            ),
            "phone": forms.TextInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "placeholder": "+998901234567",
                }
            ),
            "birth_date": forms.DateInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "type": "date",
                }
            ),
            "gender": forms.Select(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                }
            ),
            "school": forms.TextInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "placeholder": "Maktab yoki kollej nomi",
                }
            ),
            "source": forms.Select(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                }
            ),
            "address": forms.TextInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                }
            ),
            "status": forms.Select(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                }
            ),
            "is_blacklisted": forms.CheckboxInput(
                attrs={
                    "class": "h-4 w-4 text-rose-600 rounded border-slate-300 focus:ring-rose-500"
                }
            ),
            "blacklist_reason": forms.Textarea(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-rose-500 focus:outline-none",
                    "rows": 2,
                    "placeholder": "Qora ro'yxatga kiritish sababi...",
                }
            ),
            "joined_date": forms.DateInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "type": "date",
                }
            ),
            "notes": forms.Textarea(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "rows": 3,
                }
            ),
        }

    allow_duplicate = forms.BooleanField(required=False, label="Dublikat bo'lsa ham saqlash")
    duplicate_of = None

    def clean_phone(self):
        return clean_phone_value(self.cleaned_data.get("phone"))

    def clean(self):
        cleaned = super().clean()
        phone, first, last = cleaned.get("phone"), cleaned.get("first_name"), cleaned.get("last_name")
        if phone and first and last and not cleaned.get("allow_duplicate"):
            same = Student.objects.filter(phone=phone, first_name__iexact=first.strip(), last_name__iexact=last.strip())
            if self.instance.pk:
                same = same.exclude(pk=self.instance.pk)
            existing = same.first()
            if existing:
                self.duplicate_of = existing
                raise forms.ValidationError(
                    f"Bunday o'quvchi allaqachon bor: {existing.full_name} (ID {existing.pk}, {existing.phone}). "
                    "Bu boshqa odam bo'lsa, pastdagi belgini qo'yib qayta saqlang."
                )
        return cleaned

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        # Qora ro'yxatni faqat direktor yoki administrator o'zgartira oladi
        is_admin_or_director = bool(
            user and (getattr(user, "is_director", False) or getattr(user, "is_administrator", False))
        )
        if not is_admin_or_director:
            if "is_blacklisted" in self.fields:
                self.fields["is_blacklisted"].disabled = True
            if "blacklist_reason" in self.fields:
                self.fields["blacklist_reason"].disabled = True

    def save(self, commit=True):
        instance = super().save(commit=False)
        is_admin_or_director = bool(
            self.user
            and (
                getattr(self.user, "is_director", False)
                or getattr(self.user, "is_administrator", False)
            )
        )
        if not is_admin_or_director:
            if self.instance.pk:
                original = Student.objects.filter(pk=self.instance.pk).first()
                if original:
                    instance.is_blacklisted = original.is_blacklisted
                    instance.blacklist_reason = original.blacklist_reason
            else:
                instance.is_blacklisted = False
                instance.blacklist_reason = ""

        if commit:
            instance.save()
            self.save_m2m()
        return instance


class GuardianForm(forms.ModelForm):
    """Vasiy (ota-ona) ma'lumotlarini kiritish formasi."""

    relation = forms.CharField(
        max_length=50,
        required=False,
        label="Qarindoshlik darajasi",
        widget=forms.TextInput(
            attrs={
                "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                "placeholder": "Otasi, Onasi...",
            }
        ),
    )
    is_primary = forms.BooleanField(
        required=False,
        initial=True,
        label="Asosiy aloqa shaxsi",
        widget=forms.CheckboxInput(
            attrs={
                "class": "h-4 w-4 text-indigo-600 rounded border-slate-300 focus:ring-indigo-500"
            }
        ),
    )

    def clean_phone(self):
        return clean_phone_value(self.cleaned_data.get("phone"))

    class Meta:
        model = Guardian
        fields = ["full_name", "phone", "notes"]
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
            "notes": forms.Textarea(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "rows": 2,
                }
            ),
        }
