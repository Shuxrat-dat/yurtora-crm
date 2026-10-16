from django.conf import settings
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.accounts.models import User
from apps.accounts.permissions import RoleRequiredMixin
from apps.students.models import Student

from . import digest, services, telegram
from .models import NotificationLog


class StudentTelegramView(RoleRequiredMixin, View):
    """O'quvchining Telegram ulanishlari: havola yaratish va qarz eslatmasini yuborish."""

    allowed_roles = [User.Role.ADMINISTRATOR]
    template_name = "notifications/student_telegram.html"

    def _context(self, student, link=""):
        debt = next(
            (d for d in services.debts_by_student() if d["student"].pk == student.pk), None
        )
        return {
            "student": student,
            "contacts": student.telegram_contacts.all(),
            "link": link,
            "configured": telegram.is_configured(),
            "debt": debt,
            "logs": NotificationLog.objects.filter(student=student)[:10],
        }

    def get(self, request, pk):
        student = get_object_or_404(Student, pk=pk)
        return render(request, self.template_name, self._context(student))

    def post(self, request, pk):
        student = get_object_or_404(Student, pk=pk)
        action = request.POST.get("action")
        link = ""

        if action == "create_link":
            link = services.deep_link(services.create_link_token(student, request.user))
            if not link:
                messages.error(request, "TELEGRAM_BOT_USERNAME sozlanmagan.")
        elif action == "send_reminder":
            context = self._context(student)
            if not context["configured"]:
                messages.error(request, "Telegram bot sozlanmagan.")
            elif context["debt"] is None:
                messages.info(request, "O'quvchining qarzi yo'q.")
            elif services.cooldown_active(services.last_reminder_at([student.pk]).get(student.pk)):
                messages.warning(
                    request,
                    f"Bu o'quvchiga yaqinda eslatma yuborilgan: {settings.TELEGRAM_REMINDER_COOLDOWN_DAYS} kun ichida qayta yuborilmaydi.",
                )
            else:
                sent, failed = services.send_debt_reminder(
                    student, context["debt"]["groups"], request.user
                )
                if sent:
                    messages.success(request, f"Eslatma yuborildi: {sent} ta chat.")
                if failed:
                    messages.error(request, f"Yuborilmadi: {failed} ta chat.")
                if not sent and not failed:
                    messages.warning(request, "Faol Telegram chat ulanmagan.")
        return render(request, self.template_name, self._context(student, link))


class DebtReminderView(RoleRequiredMixin, View):
    """Qarzdorlarga ommaviy eslatma: avval ro'yxat (ko'rib chiqish), keyin tasdiqlash."""

    allowed_roles = [User.Role.ADMINISTRATOR]
    template_name = "notifications/debt_reminders.html"

    def _rows(self):
        debts = services.debts_by_student()
        ids = [d["student"].pk for d in debts]
        linked = services.students_with_telegram(ids)
        last = services.last_reminder_at(ids)
        rows = []
        for d in debts:
            pk = d["student"].pk
            rows.append(
                {
                    **d,
                    "has_telegram": pk in linked,
                    "last_sent": last.get(pk),
                    "cooldown": services.cooldown_active(last.get(pk)),
                }
            )
        return rows

    def get(self, request):
        rows = self._rows()
        return render(
            request,
            self.template_name,
            {
                "rows": rows,
                "configured": telegram.is_configured(),
                "sendable": sum(1 for r in rows if r["has_telegram"] and not r["cooldown"]),
                "without_telegram": sum(1 for r in rows if not r["has_telegram"]),
            },
        )

    def post(self, request):
        if not telegram.is_configured():
            messages.error(request, "Telegram bot sozlanmagan.")
            return redirect("notifications:debt_reminders")

        chosen = set(request.POST.getlist("student"))
        queued_students = skipped = 0
        # Summalar serverda qayta hisoblanadi, brauzerdan kelgan qiymatlarga ishonilmaydi
        for row in self._rows():
            if str(row["student"].pk) not in chosen:
                continue
            if not row["has_telegram"] or row["cooldown"]:
                skipped += 1
                continue
            queued_students += 1 if services.queue_debt_reminder(row["student"], row["groups"], request.user) else 0

        messages.success(
            request,
            f"Navbatga qo'yildi: {queued_students} ta o'quvchi. Bot bir necha daqiqada yuboradi.",
        )
        if skipped:
            messages.warning(
                request,
                f"O'tkazib yuborildi (Telegram yo'q yoki yaqinda yuborilgan): {skipped}.",
            )
        return redirect("notifications:debt_reminders")


class AdminChatView(RoleRequiredMixin, View):
    """Xodim o'z Telegram chatini ulaydi: haftalik qarz ro'yxati shu yerga keladi."""

    allowed_roles = [User.Role.ADMINISTRATOR]
    template_name = "notifications/admin_chat.html"

    def _context(self, request, link=""):
        return {
            "chats": request.user.telegram_admin_chats.all(),
            "link": link,
            "configured": telegram.is_configured(),
        }

    def get(self, request):
        return render(request, self.template_name, self._context(request))

    def post(self, request):
        link = digest.admin_deep_link(digest.create_admin_token(request.user))
        if not link:
            messages.error(request, "TELEGRAM_BOT_USERNAME sozlanmagan.")
        return render(request, self.template_name, self._context(request, link))
