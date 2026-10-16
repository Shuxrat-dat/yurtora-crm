"""Management command to seed comprehensive, realistic demo data for Yurtora CRM."""

import os
import random
import secrets
from datetime import date, time, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.accounts.models import User
from apps.courses.models import Course
from apps.finance.models import (
    ExpenseCategory,
    ExpenseItem,
    LedgerEntry,
    PayrollAdjustment,
    PayrollLine,
    PayrollMonth,
    SalaryPayout,
)
from apps.finance.services import (
    register_payment,
    register_salary_payout,
)
from apps.groups.models import Enrollment, Group
from apps.schedule.models import Lesson, Room, ScheduleSlot
from apps.schedule.services import generate_lessons, mark_lesson_held
from apps.students.models import Guardian, Student, StudentGuardian
from apps.teachers.models import Teacher


class Command(BaseCommand):
    """Seed comprehensive demo data for Yurtora CRM (idempotent, supports --flush/--recreate)."""

    help = "Haqiqiy DEMO ma'lumotlar: 50 ta o'quvchi, 12 ta o'qituvchi, 10 ta guruh, 8 ta kurs, to'lovlar, darslar, davomat, ish haqi"

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Eski demo ma'lumotlarni tozalab, qaytadan yaratish",
        )

    def handle(self, *args, **options):
        self._assert_safe_environment(flush=bool(options.get("flush")))
        # Parol kodda saqlanmaydi: SEED_PASSWORD yoki har safar tasodifiy
        self.password = os.environ.get("SEED_PASSWORD") or secrets.token_urlsafe(12)

        if options.get("flush"):
            self.stdout.write(self.style.WARNING("Eski demo ma'lumotlar tozalanmoqda..."))
            self._flush_data()

        if self._check_existing_data() and not options.get("flush"):
            self.stdout.write(
                self.style.WARNING(
                    "\n⚠️ Demo ma'lumotlar allaqachon mavjud!\n"
                    "Qayta yaratish uchun: python manage.py seed_demo --flush\n"
                )
            )
            return

        random.seed(42)

        self.stdout.write("Demo ma'lumotlar yaratilishi boshlandi...")
        with transaction.atomic():
            self._create_users()
            self._create_courses()
            self._create_teachers()
            self._create_rooms()
            self._create_groups()
            self._create_students()
            self._create_enrollments()
            self._generate_lessons()
            self._mark_lessons_and_attendance()
            self._create_payments()
            self._create_salary_payouts_and_expenses()

        self.stdout.write(self.style.SUCCESS("\n[SUCCESS] Demo ma'lumotlar muvaffaqiyatli yaratildi!"))
        self._print_credentials()

    def _assert_safe_environment(self, flush):
        """Faqat dev yoki test muhitda ishlaydi. Moliyaviy ma'lumotni hech qachon o'chirmaydi."""
        if not settings.DEBUG:
            raise CommandError("seed_demo faqat DEBUG=True bo'lgan dev muhitda ishlaydi.")

        db_name = str(connection.settings_dict.get("NAME", ""))
        if db_name != ":memory:" and not db_name.endswith(("_dev", "_test")):
            raise CommandError(
                f"seed_demo faqat dev yoki test bazada ishlaydi (hozirgi baza: {db_name})."
            )

        if flush and self._has_finance_data():
            raise CommandError(
                "Moliyaviy yozuvlar mavjud, --flush ishlatilmaydi. "
                "Moliya ma'lumotlarini faqat qo'lda, dev bazani qayta yaratib tozalash mumkin."
            )

    def _has_finance_data(self):
        """Moliya jadvallarida birorta yozuv bormi (ularni --flush o'chirmaydi)."""
        finance_models = (LedgerEntry, PayrollMonth, PayrollLine, SalaryPayout, ExpenseItem)
        return any(model.objects.exists() for model in finance_models)

    def _flush_data(self):
        """Moliya bo'lmagan demo jadvallarini TRUNCATE qilish. Moliya jadvallariga tegilmaydi."""
        with connection.cursor() as cursor:
            tables = [
                "attendance_attendancelog",
                "attendance_attendance",
                "schedule_lesson",
                "schedule_scheduleslot",
                "schedule_room",
                "groups_enrollment",
                "groups_group",
                "students_studentguardian",
                "students_guardian",
                "students_student",
                "teachers_teacher",
                "courses_course",
            ]
            for table in tables:
                cursor.execute(f'TRUNCATE TABLE "{table}" CASCADE;')
        self.stdout.write(self.style.SUCCESS("[OK] Demo jadvallar tozalandi (moliya saqlandi)"))

    def _check_existing_data(self):
        return Student.objects.count() >= 30 and Course.objects.count() >= 6

    def _print_credentials(self):
        self.stdout.write("\n" + "=" * 65)
        self.stdout.write("FOYDALANUVCHILAR:")
        self.stdout.write("=" * 65)
        self.stdout.write(f"Barcha demo foydalanuvchilar uchun bir xil parol: {self.password}")
        self.stdout.write("[DIRECTOR]      Demo-admin / (yuqoridagi parol)  (Barcha huquqlar)")
        self.stdout.write("[DIRECTOR]      director / (yuqoridagi parol)")
        self.stdout.write("[ADMINISTRATOR] admin / (yuqoridagi parol)")
        self.stdout.write("[TEACHER]       teacher / (yuqoridagi parol)")
        self.stdout.write("=" * 65)
        self.stdout.write("Moliya paneli: http://localhost:8080/finance/dashboard/")
        self.stdout.write("=" * 65 + "\n")

    def _create_users(self):
        """Rolga ega foydalanuvchilar."""
        users = [
            ("Demo-admin", "Demo", "Admin", User.Role.DIRECTOR, self.password, True),
            ("director", "Azizbek", "Karimov", User.Role.DIRECTOR, self.password, True),
            ("admin", "Malika", "Yusupova", User.Role.ADMINISTRATOR, self.password, False),
            ("teacher", "Sardor", "Toshmatov", User.Role.TEACHER, self.password, False),
        ]
        for username, first, last, role, pwd, is_super in users:
            u = User.objects.filter(username=username).first()
            if not u:
                if is_super:
                    User.objects.create_superuser(
                        username=username,
                        password=pwd,
                        first_name=first,
                        last_name=last,
                        email=f"{username.lower()}@demo.yurtora.uz",
                    )
                else:
                    User.objects.create_user(
                        username=username,
                        password=pwd,
                        first_name=first,
                        last_name=last,
                        role=role,
                        email=f"{username.lower()}@demo.yurtora.uz",
                    )
            else:
                u.set_password(pwd)
                u.save()

        # O'qituvchilar uchun akkauntlar (teacher1 .. teacher12)
        teacher_names = [
            ("teacher1", "Jamshid", "Tursunov"),
            ("teacher2", "Feruza", "Mirzayeva"),
            ("teacher3", "Bobur", "Xolmatov"),
            ("teacher4", "Malika", "Azimova"),
            ("teacher5", "Otabek", "Karimov"),
            ("teacher6", "Nigora", "Raxmonova"),
            ("teacher7", "Jasur", "Saidov"),
            ("teacher8", "Dildora", "Qodirova"),
            ("teacher9", "Farhod", "Ergashev"),
            ("teacher10", "Zilola", "Hamidova"),
            ("teacher11", "Ulugbek", "Aliyev"),
            ("teacher12", "Shahnoza", "Bozorova"),
        ]
        for uname, f_name, l_name in teacher_names:
            if not User.objects.filter(username=uname).exists():
                User.objects.create_user(
                    username=uname,
                    password=self.password,
                    first_name=f_name,
                    last_name=l_name,
                    role=User.Role.TEACHER,
                    email=f"{uname}@demo.yurtora.uz",
                )
        self.stdout.write("[OK] Foydalanuvchilar tayyorlandi")

    def _create_courses(self):
        """8 ta kurs."""
        courses_data = [
            ("General English", "Beginner", 450000),
            ("General English", "Elementary", 500000),
            ("General English", "Intermediate", 550000),
            ("IELTS Intensive", "Band 7.5+", 750000),
            ("Rus tili", "So'zlashuv", 400000),
            ("Mental arifmetika", "Boshlang'ich", 420000),
            ("Matematika DTM / Milliy sertifikat", "Yuqori", 480000),
            ("Python Dasturlash", "Frontend & Backend", 650000),
        ]
        self.courses = []
        for name, lvl, price in courses_data:
            c, _ = Course.objects.get_or_create(
                name=name,
                level=lvl,
                defaults={"monthly_price": price, "is_active": True},
            )
            self.courses.append(c)
        self.stdout.write(f"[OK] {len(self.courses)} ta kurs yaratildi")

    def _create_teachers(self):
        """12 ta o'qituvchi (foiz va oklad asosida)."""
        teachers_info = [
            ("teacher", "Sardor Baxtiyorovich Toshmatov", "+998901112233", "percent", None, 35),
            ("teacher1", "Jamshid Akramovich Tursunov", "+998901234567", "percent", None, 35),
            ("teacher2", "Feruza Karimovna Mirzayeva", "+998902345678", "percent", None, 30),
            ("teacher3", "Bobur Shavkatovich Xolmatov", "+998903456789", "percent", None, 40),
            ("teacher4", "Malika Rustamovna Azimova", "+998904567890", "percent", None, 35),
            ("teacher5", "Otabek Aliyevich Karimov", "+998905678901", "percent", None, 30),
            ("teacher6", "Nigora Dilshodovna Raxmonova", "+998906789012", "fixed", 4500000, 0),
            ("teacher7", "Jasur Rustamovich Saidov", "+998907890123", "fixed", 5000000, 0),
            ("teacher8", "Dildora Ikromovna Qodirova", "+998908901234", "percent", None, 35),
            ("teacher9", "Farhod Anvarovich Ergashev", "+998909012345", "percent", None, 40),
            ("teacher10", "Zilola Sanjarovna Hamidova", "+998931112233", "percent", None, 30),
            ("teacher11", "Ulugbek Mansurovich Aliyev", "+998942223344", "fixed", 4000000, 0),
        ]
        self.teachers = []
        for uname, full_name, phone, p_type, salary, percent in teachers_info:
            u = User.objects.get(username=uname)
            t, _ = Teacher.objects.get_or_create(
                user=u,
                defaults={
                    "full_name": full_name,
                    "phone": phone,
                    "pay_type": Teacher.PayType.PERCENT if p_type == "percent" else Teacher.PayType.FIXED,
                    "monthly_salary": salary,
                    "is_active": True,
                },
            )
            self.teachers.append((t, percent))
        self.stdout.write(f"[OK] {len(self.teachers)} ta o'qituvchi yaratildi")

    def _create_rooms(self):
        """6 ta zamonaviy o'quv xonasi."""
        rooms_data = [
            "101-xona",
            "102-xona",
            "103-xona",
            "201-xona",
            "202-xona",
            "IT Lab",
        ]
        self.rooms = []
        for name in rooms_data:
            r, _ = Room.objects.get_or_create(name=name, defaults={"is_active": True})
            self.rooms.append(r)
        self.stdout.write(f"[OK] {len(self.rooms)} ta auditoriya yaratildi")

    def _create_groups(self):
        """10 ta faol guruh."""
        groups_setup = [
            ("ENG-Beg-01", 0, 0, [0, 2, 4], time(9, 0), time(11, 0), 0, 35),   # Dush, Chor, Juma
            ("ENG-Elem-02", 1, 1, [1, 3, 5], time(10, 0), time(12, 0), 1, 35), # Sesh, Pay, Shanba
            ("ENG-Inter-03", 2, 2, [0, 2, 4], time(14, 0), time(16, 0), 2, 30),
            ("IELTS-Intensive-04", 3, 3, [1, 3, 5], time(16, 0), time(18, 0), 3, 40),
            ("RUS-01", 4, 4, [0, 2, 4], time(11, 0), time(13, 0), 0, 30),
            ("MATH-DTM-01", 6, 5, [1, 3, 5], time(14, 0), time(16, 0), 4, 30),
            ("PYTHON-PRO-01", 7, 7, [0, 2, 4], time(16, 0), time(18, 0), 5, 40), # IT Lab
            ("MENTAL-01", 5, 8, [1, 3, 5], time(9, 0), time(11, 0), 2, 35),
            ("ENG-Beg-02", 0, 9, [1, 3, 5], time(14, 0), time(16, 0), 1, 30),
            ("MATH-Milliy-02", 6, 10, [0, 2, 4], time(16, 0), time(18, 0), 4, 30),
        ]
        self.groups = []
        today = date.today()
        # Guruhlar 40 kun avval boshlangan
        start_d = today.replace(day=1) - timedelta(days=35)

        for name, c_idx, t_idx, weekdays, s_time, e_time, r_idx, pct in groups_setup:
            teacher, _ = self.teachers[t_idx]
            g, _ = Group.objects.get_or_create(
                name=name,
                defaults={
                    "course": self.courses[c_idx],
                    "teacher": teacher,
                    "max_students": 16,
                    "teacher_percent": pct,
                    "start_date": start_d,
                    "status": Group.Status.ACTIVE,
                },
            )
            self.groups.append((g, weekdays, s_time, e_time, r_idx))
        self.stdout.write(f"[OK] {len(self.groups)} ta guruh yaratildi")

    def _create_students(self):
        """45 ta o'quvchi."""
        names_male = [
            ("Azizbek", "Rahimov"), ("Sardor", "Aliyev"), ("Bekzod", "Qodirov"),
            ("Javohir", "Karimov"), ("Jamshid", "Tursunov"), ("Otabek", "Nazarov"),
            ("Bobur", "Shavkatov"), ("Jasur", "Saidov"), ("Davron", "Mirzayev"),
            ("Timur", "Xolmatov"), ("Alisher", "Azimov"), ("Farhod", "Nurmatov"),
            ("Abbos", "Ergashev"), ("Doston", "Ismoilov"), ("Sherzod", "Safarov"),
            ("Ulugbek", "Haydarov"), ("Shohruh", "Shodiyev"), ("Nodir", "Yusupov"),
            ("Samandar", "G'aniyev"), ("Sanjar", "Vohidov"), ("Behruz", "Ortiqov"),
            ("Asilbek", "Murodov"), ("Doniyor", "Akramov"), ("Shahzod", "Rustamov"),
        ]
        names_female = [
            ("Madina", "Yusupova"), ("Zarina", "Karimova"), ("Dilnoza", "Aliyeva"),
            ("Kamola", "Rahimova"), ("Feruza", "Toshmatova"), ("Malika", "Nurmatova"),
            ("Nigora", "Safarova"), ("Aziza", "Haydarova"), ("Nilufar", "Xolmatova"),
            ("Shoira", "Mirzayeva"), ("Mohira", "Azimova"), ("Gulnora", "Shodiyeva"),
            ("Dildora", "Tursunova"), ("Sevara", "Ergasheva"), ("Laylo", "Ismoilova"),
            ("Munisa", "Qodirova"), ("Oygul", "Nazirova"), ("Saida", "G'aniyeva"),
            ("Zilola", "Vohidova"), ("Yulduz", "Ortiqova"), ("Rayhona", "Murodova"),
        ]

        all_names = [(fn, ln, "male") for fn, ln in names_male] + [(fn, ln, "female") for fn, ln in names_female]
        random.shuffle(all_names)

        schools = ["1-IDUM", "5-maktab", "Prezident maktabi", "34-maktab", "Litsey", "Universitet"]
        sources = [s[0] for s in Student.Source.choices]

        self.students = []
        for idx, (first, last, gender) in enumerate(all_names[:45]):
            phone = f"+99890{1100000 + idx:07d}"
            st, _ = Student.objects.get_or_create(
                phone=phone,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "gender": gender,
                    "school": random.choice(schools),
                    "source": random.choice(sources),
                    "address": f"Toshkent sh., {random.randint(1, 15)}-mavze",
                    "status": Student.Status.ACTIVE,
                    "joined_date": date.today() - timedelta(days=random.randint(30, 90)),
                },
            )
            # Guardian
            g_phone = f"+99891{2200000 + idx:07d}"
            guardian, _ = Guardian.objects.get_or_create(
                phone=g_phone,
                defaults={
                    "full_name": f"{last}bek {first} o'g'li",
                },
            )
            StudentGuardian.objects.get_or_create(
                student=st,
                guardian=guardian,
                defaults={
                    "is_primary": True,
                    "relation": "Otasi" if random.random() > 0.4 else "Onasi",
                },
            )
            self.students.append(st)
        self.stdout.write(f"[OK] {len(self.students)} ta o'quvchi va ota-onalar yaratildi")

    def _create_enrollments(self):
        """O'quvchilarni guruhlarga taqsimlash."""
        self.enrollments = []
        # Har bir guruhga 4-5 nafardan o'quvchi
        student_idx = 0
        today = date.today()
        month_start = today.replace(day=1)
        prev_month_start = (month_start - timedelta(days=1)).replace(day=1)

        for g_tuple in self.groups:
            group = g_tuple[0]
            # 4-5 ta o'quvchi
            count = 4
            for _ in range(count):
                if student_idx < len(self.students):
                    st = self.students[student_idx]
                    student_idx += 1
                    en, _ = Enrollment.objects.get_or_create(
                        student=st,
                        group=group,
                        defaults={
                            "status": Enrollment.Status.ACTIVE,
                            "start_date": prev_month_start,
                            "paid_from": prev_month_start,
                        },
                    )
                    self.enrollments.append(en)
        self.stdout.write(f"[OK] {len(self.enrollments)} ta a'zolik (Enrollment) biriktirildi")

    def _generate_lessons(self):
        """Slotlar va 2 oy uchun darslar generatsiyasi."""
        today = date.today()
        month_start = today.replace(day=1)
        prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
        # 30 kun oldindan 15 kun keyingacha
        date_from = prev_month_start
        date_to = today + timedelta(days=14)

        total_lessons = 0
        for g_tuple in self.groups:
            group, weekdays, s_time, e_time, r_idx = g_tuple
            room = self.rooms[r_idx]

            # Slotlar
            for wd in weekdays:
                ScheduleSlot.objects.get_or_create(
                    group=group,
                    weekday=wd,
                    defaults={
                        "start_time": s_time,
                        "end_time": e_time,
                        "room": room,
                        "is_active": True,
                    },
                )

            # Darslarni generatsiya qilish
            try:
                created = generate_lessons(group, date_from, date_to)
                total_lessons += len(created)
            except Exception:
                pass
        self.stdout.write(f"[OK] {total_lessons} ta dars jadvali yaratildi")

    def _mark_lessons_and_attendance(self):
        """O'tgan darslarni held qilish va davomatni belgilash."""
        from apps.attendance.services import mark_attendance

        today = date.today()
        admin_user = User.objects.get(username="Demo-admin")

        past_lessons = Lesson.objects.filter(date__lte=today, status=Lesson.Status.SCHEDULED).select_related("group")
        held_count = 0
        att_count = 0

        for lsn in past_lessons:
            try:
                mark_lesson_held(lsn, admin_user)
                held_count += 1

                # Guruh o'quvchilariga davomat
                group_enrollments = Enrollment.objects.filter(group=lsn.group, status=Enrollment.Status.ACTIVE)
                for en in group_enrollments:
                    st_status = random.choices(["present", "late", "absent", "excused"], weights=[80, 10, 7, 3])[0]
                    try:
                        mark_attendance(lsn, en.student, st_status, admin_user)
                        att_count += 1
                    except Exception:
                        pass
            except Exception:
                pass
        self.stdout.write(f"[OK] {held_count} ta dars o'tildi, {att_count} ta davomat qayd etildi")

    def _create_payments(self):
        """O'quvchilar to'lovlari: ko'pchiligi to'lagan, ba'zilari qarzdor, ba'zilari ortiqcha to'lagan."""
        admin_user = User.objects.get(username="Demo-admin")
        today = date.today()
        month_start = today.replace(day=1)
        prev_month_start = (month_start - timedelta(days=1)).replace(day=1)

        methods = ["cash", "card", "click", "payme", "uzum"]
        payment_count = 0

        for idx, en in enumerate(self.enrollments):
            price = en.group.course.monthly_price

            # 1. O'tgan oy to'lovi
            if idx % 7 != 0:  # 7-chi o'quvchilar o'tgan oy uchun ham to'lamagan (qarzdor)
                p_date = prev_month_start + timedelta(days=random.randint(1, 20))
                register_payment(
                    student=en.student,
                    group=en.group,
                    amount=price,
                    method=random.choice(methods),
                    entry_date=p_date,
                    comment="O'tgan oy to'lovi",
                    user=admin_user,
                )
                payment_count += 1

            # 2. Joriy oy to'lovi
            if idx % 4 == 0:
                # To'liq to'lagan
                p_date = month_start + timedelta(days=random.randint(1, min(7, today.day)))
                register_payment(
                    student=en.student,
                    group=en.group,
                    amount=price,
                    method=random.choice(methods),
                    entry_date=p_date,
                    comment="Joriy oy to'lovi (to'liq)",
                    user=admin_user,
                )
                payment_count += 1
            elif idx % 4 == 1:
                # Qisman to'lagan (qarzdorlik qoladi)
                p_date = month_start + timedelta(days=random.randint(1, min(7, today.day)))
                register_payment(
                    student=en.student,
                    group=en.group,
                    amount=int(price * 0.5),
                    method=random.choice(methods),
                    entry_date=p_date,
                    comment="Joriy oy to'lovi (50% avans)",
                    user=admin_user,
                )
                payment_count += 1
            elif idx % 4 == 2:
                # Ortiqcha to'lagan (depozit / prepayment)
                p_date = month_start + timedelta(days=random.randint(1, min(7, today.day)))
                register_payment(
                    student=en.student,
                    group=en.group,
                    amount=int(price * 1.5),
                    method=random.choice(methods),
                    entry_date=p_date,
                    comment="Oldindan to'lov (1.5 oy)",
                    user=admin_user,
                )
                payment_count += 1
            # idx % 4 == 3: joriy oyga umuman to'lamagan (faol qarzdor)

        self.stdout.write(f"[OK] {payment_count} ta to'lov (tushum) muvaffaqiyatli qayd etildi")

    def _create_salary_payouts_and_expenses(self):
        """O'qituvchilar maoshi, xarajatlar, bonus va jarimalar."""
        admin_user = User.objects.get(username="Demo-admin")
        today = date.today()
        month_start = today.replace(day=1)
        prev_month = (month_start - timedelta(days=1))

        # 1. O'tgan oy uchun ish haqi to'lovlari
        for t_tuple in self.teachers[:8]:
            teacher = t_tuple[0]
            amt = teacher.monthly_salary if teacher.monthly_salary else 3200000
            register_salary_payout(
                teacher=teacher,
                year=prev_month.year,
                month=prev_month.month,
                amount=amt,
                method="card",
                paid_date=prev_month.replace(day=25),
                comment="Ish haqi to'lovi",
                user=admin_user,
            )

        # Joriy oy uchun 3 ta o'qituvchiga avans
        for t_tuple in self.teachers[:3]:
            teacher = t_tuple[0]
            register_salary_payout(
                teacher=teacher,
                year=today.year,
                month=today.month,
                amount=1500000,
                method="cash",
                paid_date=today.replace(day=5),
                comment="Oylik avans",
                user=admin_user,
            )

        # 2. Xarajatlar (ExpenseCategory & ExpenseItem)
        cat_marketing, _ = ExpenseCategory.objects.get_or_create(
            name="Marketing (Target & SMM)",
            defaults={"category_type": ExpenseCategory.Type.EXPENSE, "is_system": False},
        )
        cat_rent, _ = ExpenseCategory.objects.get_or_create(
            name="Ijara va Kommunal",
            defaults={"category_type": ExpenseCategory.Type.EXPENSE, "is_system": True},
        )
        cat_supplies, _ = ExpenseCategory.objects.get_or_create(
            name="O'quv qurollari va kantselyariya",
            defaults={"category_type": ExpenseCategory.Type.EXPENSE, "is_system": True},
        )

        ExpenseItem.objects.create(
            category=cat_marketing,
            amount=4200000,
            entry_date=today.replace(day=2),
            comment="Instagram va Telegram reklama xarajatlari",
            created_by=admin_user,
        )
        ExpenseItem.objects.create(
            category=cat_rent,
            amount=8500000,
            entry_date=today.replace(day=1),
            comment="Bino oylik ijarasi",
            created_by=admin_user,
        )
        ExpenseItem.objects.create(
            category=cat_supplies,
            amount=1150000,
            entry_date=today.replace(day=4),
            comment="Doska markerlari va daftarlar",
            created_by=admin_user,
        )

        # 3. Bonus va Jarimalar (PayrollAdjustment)
        p_month, _ = PayrollMonth.objects.get_or_create(
            year=today.year,
            month=today.month,
            defaults={"closed_at": None, "closed_by": None},
        )

        # 2 ta bonus
        PayrollAdjustment.objects.create(
            month=p_month,
            teacher=self.teachers[0][0],
            amount=500000,
            comment="O'quvchilarning a'lo imtihon natijalari uchun bonus",
            created_by=admin_user,
        )
        PayrollAdjustment.objects.create(
            month=p_month,
            teacher=self.teachers[1][0],
            amount=300000,
            comment="Ochiq darsni yuqori darajada o'tkazganlik uchun bonus",
            created_by=admin_user,
        )

        # 1 ta jarima
        PayrollAdjustment.objects.create(
            month=p_month,
            teacher=self.teachers[2][0],
            amount=-200000,
            comment="Darsga sababsiz kechikib kelganlik uchun jarima",
            created_by=admin_user,
        )

        self.stdout.write("[OK] Ish haqi, xarajatlar, bonus va jarimalar to'liq shakllantirildi")
