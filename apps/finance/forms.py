"""Finance forms."""

from django import forms

from apps.core.forms import MoneyField, MoneyInput
from django.core.exceptions import ValidationError

from apps.finance.models import LedgerEntry

# Bitta to'lov uchun yuqori chegara (10 mlrd so'm): xato kiritilgan summa kitobni buzmasligi uchun
MAX_PAYMENT_AMOUNT = 10_000_000_000


class PaymentForm(forms.Form):
    """To'lov qabul qilish formasi."""

    amount = MoneyField(
        min_value=1,
        max_value=MAX_PAYMENT_AMOUNT,
        label="Summa (so'm)",
        widget=MoneyInput(
            attrs={
                "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500",
                "placeholder": "Masalan: 500 000",
            }
        ),
    )
    method = forms.ChoiceField(
        choices=LedgerEntry.PaymentMethod.choices,
        label="To'lov usuli",
        widget=forms.Select(
            attrs={
                "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
            }
        ),
    )
    entry_date = forms.DateField(
        label="To'lov sanasi",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500",
            }
        ),
    )
    comment = forms.CharField(
        required=False,
        label="Izoh",
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500",
                "placeholder": "Ixtiyoriy izoh...",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Bugungi sana default
        from django.utils import timezone

        if "initial" not in kwargs or "entry_date" not in kwargs.get("initial", {}):
            self.fields["entry_date"].initial = timezone.localdate()


    def clean_entry_date(self):
        from django.utils import timezone

        entry_date = self.cleaned_data["entry_date"]
        if entry_date > timezone.localdate():
            raise ValidationError("To'lov sanasi kelajakda bo'lishi mumkin emas.")
        return entry_date


class ExcusedCreditForm(forms.Form):
    """Uzrli sabab: kreditlash formasi."""

    comment = forms.CharField(
        label="Sabab (majburiy)",
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500",
                "placeholder": "Uzrli sabab: bemor, oilaviy vaziyat, va h.k.",
                "required": True,
            }
        ),
    )

    def clean_comment(self):
        comment = self.cleaned_data.get("comment", "").strip()
        if not comment:
            raise ValidationError("Sabab majburiy.")
        return comment


LOGO_MAX_BYTES = 200 * 1024
# Faqat rastr rasmlar: SVG ichida skript bo'lishi mumkin
_LOGO_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


def logo_mime(head: bytes):
    for signature, mime in _LOGO_SIGNATURES:
        if head.startswith(signature):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


class ReceiptSettingsForm(forms.ModelForm):
    """To'lov cheki sozlamalarini tahrirlash formasi."""

    logo_file = forms.FileField(required=False, label="Logotip rasmi")
    logo_remove = forms.BooleanField(required=False, label="Logotipni olib tashlash")

    def clean_logo_file(self):
        upload = self.cleaned_data.get("logo_file")
        if not upload:
            return None
        if upload.size > LOGO_MAX_BYTES:
            raise forms.ValidationError("Rasm hajmi 200 KB dan oshmasligi kerak.")
        data = upload.read()
        mime = logo_mime(data[:16])
        if mime is None:
            raise forms.ValidationError("Faqat PNG, JPEG yoki WebP rasm yuklash mumkin.")
        import base64

        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get("logo_remove"):
            obj.logo_data = ""
        elif self.cleaned_data.get("logo_file"):
            obj.logo_data = self.cleaned_data["logo_file"]
        if commit:
            obj.save()
        return obj

    class Meta:
        from apps.finance.models import ReceiptSettings

        model = ReceiptSettings
        fields = [
            "center_name",
            "address",
            "phone",
            "footer_text",
            "logo_position",
            "show_logo",
            "show_header",
            "show_address",
            "show_phone",
            "show_issued_time",
            "show_payment_id",
            "show_student_info",
            "show_group_info",
            "show_payment_method",
            "show_payment_date",
            "show_cashier",
            "show_amount",
            "show_footer",
            "show_footer_text",
            "show_qr_code",
            "show_teacher",
            "show_course_price",
        ]
        widgets = {
            "address": forms.TextInput(
                attrs={
                    "class": "w-full px-3 py-2.5 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-600 focus:border-indigo-600",
                    "placeholder": "Masalan: Toshkent sh., Chilonzor 12",
                }
            ),
            "phone": forms.TextInput(
                attrs={
                    "class": "w-full px-3 py-2.5 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-600 focus:border-indigo-600",
                    "placeholder": "+998 90 123 45 67",
                }
            ),
            "footer_text": forms.TextInput(
                attrs={
                    "class": "w-full px-3 py-2.5 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-600 focus:border-indigo-600",
                    "placeholder": "Masalan: Rahmat!",
                }
            ),
            "logo_position": forms.HiddenInput(),
        }


class RefundForm(forms.Form):
    """Pul qaytarish formasi (faqat direktor)."""

    amount = MoneyField(
        min_value=1,
        max_value=MAX_PAYMENT_AMOUNT,
        label="Qaytariladigan summa (so'm)",
        widget=MoneyInput(attrs={"class": "w-full px-3 py-2 border border-slate-300 rounded-lg"}),
    )
    method = forms.ChoiceField(
        choices=LedgerEntry.PaymentMethod.choices,
        label="Qaytarish usuli",
        widget=forms.Select(attrs={"class": "w-full px-3 py-2 border border-slate-300 rounded-lg"}),
    )
    entry_date = forms.DateField(
        label="Sana",
        widget=forms.DateInput(attrs={"type": "date", "class": "w-full px-3 py-2 border border-slate-300 rounded-lg"}),
    )
    comment = forms.CharField(
        label="Sabab (majburiy)",
        widget=forms.Textarea(attrs={"rows": 3, "class": "w-full px-3 py-2 border border-slate-300 rounded-lg"}),
    )

    def clean_comment(self):
        comment = self.cleaned_data["comment"].strip()
        if not comment:
            raise ValidationError("Sabab majburiy.")
        return comment

    def clean_entry_date(self):
        from django.utils import timezone

        entry_date = self.cleaned_data["entry_date"]
        if entry_date > timezone.localdate():
            raise ValidationError("Sana kelajakda bo'lishi mumkin emas.")
        return entry_date
