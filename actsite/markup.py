"""Markdown → HTML for org-written text (letters, posts, bios, about pages)."""
import markdown
from django.utils.safestring import mark_safe


def render(text):
    html = markdown.markdown(text or "", extensions=["extra", "sane_lists", "smarty"], output_format="html")
    return mark_safe(html)
