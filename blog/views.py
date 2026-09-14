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
    token = request.GET.get("preview", "")
    if token or request.user.is_staff:
        qs = Post.objects.filter(slug=slug)
        org = site_org()
        if org:
            qs = qs.filter(org=org)
        if token:
            qs = qs.filter(preview_token=token)
    else:
        qs = published().filter(slug=slug)
    p = get_object_or_404(qs.order_by("-published_at")[:1]) if site_org() is None else get_object_or_404(qs)
    # Reader tips split between the writer and the org's campaign (SimpleTip), when both are set up.
    tip = None
    a = p.public_author
    camp = p.org.campaigns.filter(status="published").exclude(simpletip_receiver="").order_by("created").first()
    if a and a.simpletip_receiver and camp:
        tip = {"writer": a, "campaign": camp}
    return render(request, "blog/post.html", {"p": p, "tip": tip})
