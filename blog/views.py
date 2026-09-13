from django.shortcuts import get_object_or_404, render

from campaigns.context import site_org

from .models import Post


def published():
    qs = Post.objects.filter(status="published").select_related("org", "author")
    org = site_org()
    return qs.filter(org=org) if org else qs


def index(request):
    return render(request, "blog/index.html", {"posts": published()[:50]})


def post(request, slug):
    qs = published().filter(slug=slug)
    p = get_object_or_404(qs.order_by("-published_at")[:1]) if site_org() is None else get_object_or_404(qs)
    return render(request, "blog/post.html", {"p": p})
