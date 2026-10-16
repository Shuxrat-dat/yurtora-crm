"""O'tgan, davomati belgilangan, lekin "o'tildi" deb belgilanmagan darslarni held qiladi (to'lovlar bilan)."""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.schedule.models import Lesson
from apps.schedule.services import mark_lesson_held


class Command(BaseCommand):
    help = (
        "Davomati belgilangan o'tgan darslarni 'o'tildi' deb belgilaydi va to'lovlarni hisoblaydi. "
        "Standart rejim: faqat ro'yxat (hech narsa o'zgarmaydi). Bajarish uchun --apply."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="O'zgarishlarni haqiqatan bajarish")

    def handle(self, *args, **options):
        lessons = (
            Lesson.objects.filter(
                status=Lesson.Status.SCHEDULED,
                date__lte=timezone.localdate(),
                attendances__isnull=False,
            )
            .distinct()
            .select_related("group")
            .order_by("date", "start_time")
        )
        count = 0
        for lesson in lessons:
            count += 1
            self.stdout.write(f"{lesson.date} {lesson.start_time:%H:%M} {lesson.group.name}")
            if options["apply"]:
                with transaction.atomic():
                    mark_lesson_held(lesson, None)
        if options["apply"]:
            self.stdout.write(self.style.SUCCESS(f"{count} ta dars o'tildi deb belgilandi."))
        else:
            self.stdout.write(f"{count} ta dars topildi. Bajarish uchun: --apply")
