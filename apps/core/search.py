"""Yuqori paneldagi umumiy qidiruv: o'quvchilar, guruhlar, o'qituvchilar (rol huquqlariga qarab)."""

from django.db.models import Prefetch, Q

from apps.core.filters import _PHONE_NOISE, person_search_q, search_tokens
from apps.groups.models import Enrollment, Group
from apps.students.models import Student
from apps.teachers.models import Teacher

LIMIT = 6
MIN_LENGTH = 2
OPEN = [Enrollment.Status.ACTIVE, Enrollment.Status.TRIAL, Enrollment.Status.FROZEN]


def global_search(user, query):
    """
    Natija: {"q", "students", "groups", "teachers"} yoki None (so'rov juda qisqa).
    - o'qituvchi: faqat o'z guruhlaridagi o'quvchilar va o'z guruhlari, telefon bo'yicha qidirmaydi;
    - buxgalter: faqat o'quvchilar;
    - o'qituvchilar ro'yxati: direktor va administrator.
    """
    q = (query or "").strip()
    if len(q) < MIN_LENGTH:
        return None
    role = getattr(user, "role", "")
    is_teacher = role == "teacher"
    staff = role in ("director", "administrator")

    open_qs = Enrollment.objects.filter(status__in=OPEN).select_related("group")
    if is_teacher:
        open_qs = open_qs.filter(group__teacher__user=user)
    students = Student.objects.filter(person_search_q(q, include_phone=not is_teacher))
    if is_teacher:
        students = students.filter(enrollments__group__teacher__user=user, enrollments__status__in=OPEN).distinct()
    students = list(
        students.order_by("last_name", "first_name").prefetch_related(
            Prefetch("enrollments", queryset=open_qs, to_attr="open_list")
        )[:LIMIT]
    )
    for s in students:
        s.groups_label = ", ".join(e.group.name for e in s.open_list)

    groups = []
    if role != "accountant":
        cond = Q()
        for token in search_tokens(q):
            cond &= Q(name__icontains=token) | Q(course__name__icontains=token) | Q(teacher__full_name__icontains=token)
        groups_qs = Group.objects.filter(status=Group.Status.ACTIVE).filter(cond).select_related("course", "teacher")
        if is_teacher:
            groups_qs = groups_qs.filter(teacher__user=user)
        groups = list(groups_qs.order_by("name")[:LIMIT])

    teachers = []
    if staff:
        cond = Q()
        for token in search_tokens(q):
            token_q = Q(full_name__icontains=token)
            digits = _PHONE_NOISE.sub("", token)
            if digits:
                token_q |= Q(phone__icontains=digits)
            cond &= token_q
        teachers = list(Teacher.objects.filter(is_active=True).filter(cond).order_by("full_name")[:LIMIT])

    return {"q": q, "students": students, "groups": groups, "teachers": teachers}
