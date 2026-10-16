"""Interfeys tashqi CDN'larga bog'liq bo'lmasligi kerak (offline va xavfsizlik uchun)."""

from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
FORBIDDEN = ("cdn.tailwindcss.com", "unpkg.com", "cdn.jsdelivr.net", "cdnjs.cloudflare.com")


def test_templates_do_not_load_external_cdn_scripts():
    offenders = [
        f"{path.relative_to(TEMPLATES)}: {host}"
        for path in TEMPLATES.rglob("*.html")
        for host in FORBIDDEN
        if host in path.read_text(encoding="utf-8")
    ]
    assert not offenders, offenders
