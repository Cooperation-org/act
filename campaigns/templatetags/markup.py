from django import template

from actsite.markup import render

register = template.Library()


@register.filter(name="markdown")
def markdown_filter(text):
    return render(text)
