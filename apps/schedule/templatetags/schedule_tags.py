"""Custom template tags and filters for schedule app."""

from django import template

register = template.Library()


@register.filter
def dict_get(d, key):
    """Returns d[key] for use in templates where key is a variable."""
    return d.get(key, [])
