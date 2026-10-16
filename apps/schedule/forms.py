"""Forms for schedule: room, slots, lesson generation, reschedule."""

from django import forms

from apps.schedule.models import Room, ScheduleSlot


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = ["name", "capacity", "note", "is_active"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "placeholder": "Masalan: 101-xona",
                }
            ),
            "capacity": forms.NumberInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "placeholder": "Masalan: 16",
                    "min": 1,
                }
            ),
            "note": forms.TextInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "placeholder": "Masalan: 2-qavat, proyektor bor",
                }
            ),
            "is_active": forms.CheckboxInput(
                attrs={"class": "h-4 w-4 text-indigo-600 rounded border-slate-300"}
            ),
        }


class ScheduleSlotForm(forms.ModelForm):
    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_time"), cleaned.get("end_time")
        if not (start and end):
            return cleaned
        if end <= start:
            self.add_error("end_time", "Tugash vaqti boshlanish vaqtidan keyin bo'lishi kerak.")
            return cleaned
        group, room, weekday = cleaned.get("group"), cleaned.get("room"), cleaned.get("weekday")
        if cleaned.get("is_active") and group is not None and room is not None and weekday is not None:
            overlapping = ScheduleSlot.objects.filter(
                weekday=weekday, is_active=True, start_time__lt=end, end_time__gt=start
            ).exclude(pk=self.instance.pk)
            clash = overlapping.filter(room=room).select_related("group").first()
            if clash:
                self.add_error("room", f"Bu xona shu vaqtda band: {clash.group.name} ({clash.start_time:%H:%M}–{clash.end_time:%H:%M}).")
            clash = overlapping.filter(group=group).first()
            if clash:
                self.add_error("start_time", f"Bu guruhning shu kuni {clash.start_time:%H:%M}–{clash.end_time:%H:%M} da boshqa darsi bor.")
            clash = overlapping.filter(group__teacher=group.teacher).exclude(group=group).select_related("group").first()
            if clash:
                self.add_error("group", f"O'qituvchi shu vaqtda band: {clash.group.name} ({clash.start_time:%H:%M}–{clash.end_time:%H:%M}).")
        return cleaned

    class Meta:
        model = ScheduleSlot
        fields = ["group", "weekday", "start_time", "end_time", "room", "is_active"]
        widgets = {
            "group": forms.Select(
                attrs={"class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"}
            ),
            "weekday": forms.Select(
                attrs={"class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"}
            ),
            "start_time": forms.TimeInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "type": "time",
                }
            ),
            "end_time": forms.TimeInput(
                attrs={
                    "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                    "type": "time",
                }
            ),
            "room": forms.Select(
                attrs={"class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"}
            ),
            "is_active": forms.CheckboxInput(
                attrs={"class": "h-4 w-4 text-indigo-600 rounded border-slate-300"}
            ),
        }


class GenerateLessonsForm(forms.Form):
    """Davrga dars generatsiyasi formasi."""

    from apps.groups.models import Group

    group = forms.ModelChoiceField(
        queryset=None,  # set in __init__
        label="Guruh",
        widget=forms.Select(
            attrs={"class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"}
        ),
    )
    date_from = forms.DateField(
        label="Dan (sana)",
        widget=forms.DateInput(
            attrs={
                "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                "type": "date",
            }
        ),
    )
    date_to = forms.DateField(
        label="Gacha (sana)",
        widget=forms.DateInput(
            attrs={
                "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                "type": "date",
            }
        ),
    )

    MAX_DAYS = 400  # bir martada yaratiladigan davr: noto'g'ri sana minglab dars yaratmasligi uchun

    def __init__(self, *args, **kwargs):
        from apps.groups.models import Group
        super().__init__(*args, **kwargs)
        self.fields["group"].queryset = Group.objects.filter(status="active").select_related("course")

    def clean(self):
        cleaned = super().clean()
        date_from, date_to = cleaned.get("date_from"), cleaned.get("date_to")
        if date_from and date_to:
            if date_to < date_from:
                self.add_error("date_to", "Tugash sanasi boshlanish sanasidan oldin bo'lishi mumkin emas.")
            elif (date_to - date_from).days > self.MAX_DAYS:
                self.add_error("date_to", f"Bir martada {self.MAX_DAYS} kundan uzoq davr tanlab bo'lmaydi.")
        return cleaned


class CancelLessonForm(forms.Form):
    """Darsni bekor qilish formasi."""

    reason = forms.CharField(
        label="Sabab",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                "placeholder": "Bekor qilish sababi...",
            }
        ),
    )


class RescheduleLessonForm(forms.Form):
    """Darsni ko'chirish formasi."""

    new_date = forms.DateField(
        label="Yangi sana",
        widget=forms.DateInput(
            attrs={
                "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                "type": "date",
            }
        ),
    )
    new_start_time = forms.TimeField(
        label="Yangi boshlanish vaqti",
        widget=forms.TimeInput(
            attrs={
                "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                "type": "time",
            }
        ),
    )
    new_end_time = forms.TimeField(
        label="Yangi tugash vaqti",
        widget=forms.TimeInput(
            attrs={
                "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                "type": "time",
            }
        ),
    )
    new_room = forms.ModelChoiceField(
        queryset=Room.objects.filter(is_active=True),
        required=False,
        label="Yangi xona (ixtiyoriy)",
        widget=forms.Select(
            attrs={"class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none"}
        ),
    )
    reason = forms.CharField(
        label="Sabab",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:outline-none",
                "placeholder": "Ko'chirish sababi...",
            }
        ),
    )

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("new_start_time"), cleaned.get("new_end_time")
        if start and end and end <= start:
            self.add_error("new_end_time", "Tugash vaqti boshlanish vaqtidan keyin bo'lishi kerak.")
        return cleaned
