from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def ic(name, cls=""):
    """A line icon from the sprite in _icons.html. Decorative: the words beside it carry the meaning."""
    return format_html('<svg class="ic{}" aria-hidden="true" focusable="false"><use href="#i-{}"></use></svg>',
                       f" {cls}" if cls else "", name)
