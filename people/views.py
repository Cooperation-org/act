from django.shortcuts import get_object_or_404, render

from campaigns.context import site_org

from .models import Person


def _published():
    qs = Person.objects.filter(status="published", consent_on_record=True).select_related("org")
    org = site_org()
    return qs.filter(org=org) if org else qs


def index(request):
    return render(request, "people/index.html", {"people": _published()})


def person(request, slug):
    org = site_org()
    qs = _published().filter(slug=slug)
    if org is None:
        qs = qs.order_by("pk")  # platform mode: slug may repeat across orgs; first wins
    p = get_object_or_404(qs[:1]) if org is None else get_object_or_404(qs)
    posts = p.posts.filter(status="published").order_by("-published_at")[:10]
    return render(request, "people/person.html", {"p": p, "posts": posts})
