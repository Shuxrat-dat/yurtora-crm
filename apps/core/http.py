"""HTTP yordamchilar: xavfsiz qaytish manzili (open redirect himoyasi)."""

from django.utils.http import url_has_allowed_host_and_scheme


def safe_next_url(request, param="next"):
    """So'rovdagi `next` manzilini qaytaradi, faqat shu saytning o'zi ichida bo'lsa; aks holda None.

    `startswith("/")` yetarli emas: "//evil.example" va "/\\evil.example" brauzerda tashqi saytga olib boradi.
    """
    candidate = request.POST.get(param) or request.GET.get(param) or ""
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return None
