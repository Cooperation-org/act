from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured
import math

from django.db.models import Count, F, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .card import render_card
from .context import site_campaigns, site_org
from .donation_text import DONATION_DISCLOSURE
from .forms import ResponseForm, TestimonialForm
from .models import CTA, Campaign, ShareLink, Update


def home(request):
    org = site_org()
    if org:
        return org_home(request, org)
    if settings.SITE_ORG_SLUG:
        raise ImproperlyConfigured(f"ACT_SITE_ORG={settings.SITE_ORG_SLUG!r}: no such org. Create it in /admin.")
    return render(request, "campaigns/home.html", {"campaigns": _with_counts(site_campaigns())})


def _with_counts(campaigns):
    return campaigns.annotate(
        vouches=Count("testimonials", filter=Q(testimonials__status="published"), distinct=True),
        videos=Count("testimonials", filter=Q(testimonials__status="published") & ~Q(testimonials__video=""),
                     distinct=True))


def org_home(request, org):
    """Single-org site home: the org in its own words, its give rail, latest, people."""
    campaigns = _with_counts(site_campaigns(org)).order_by("created")
    give = campaigns.first()
    people = org.people.filter(status="published", consent_on_record=True)[:8] if "people" in settings.ENABLED_APPS else []
    return render(request, "campaigns/org_home.html", {
        "org": org,
        "campaigns": campaigns,
        "give": give,
        "stream": stream(org)[:6],
        "people": people,
        "donation_disclosure": DONATION_DISCLOSURE,
    })


def stream(org=None):
    """Published campaign updates and blog posts, newest first, as one list."""
    updates = Update.objects.filter(status="published", campaign__status="published").select_related("campaign")
    if org:
        updates = updates.filter(campaign__org=org)
    items = [{"date": u.date, "kind": "update", "campaign": u.campaign, "text": u.text} for u in updates[:100]]
    if "blog" in settings.ENABLED_APPS:
        from blog.views import published
        for p in published()[:100]:
            items.append({"date": p.published_at.date(), "kind": "post", "post": p, "text": p.summary or p.title})
    items.sort(key=lambda i: i["date"], reverse=True)
    return items


def updates(request):
    return render(request, "campaigns/updates.html", {"stream": stream(site_org())[:100]})


def governance(request):
    org = site_org()
    if not org or not org.has_governance:
        raise Http404
    return render(request, "campaigns/governance.html", {"org": org})


def why(request):
    return render(request, "campaigns/why.html")


def _campaign_or_404(slug, request):
    c = get_object_or_404(Campaign, slug=slug)
    org = site_org()
    if org and c.org_id != org.pk:
        raise Http404
    if c.status != "published" and not request.user.is_staff \
            and request.GET.get("preview", "") != c.preview_token:
        raise Http404
    return c


def campaign(request, slug):
    c = _campaign_or_404(slug, request)
    share_url = f"{settings.PUBLIC_URL}/c/{c.slug}/"
    via = request.GET.get("via", "")
    testimonials = list(c.testimonials.filter(status="published"))
    return render(request, "campaigns/campaign.html", {
        "c": c,
        "ctas": c.ctas.filter(enabled=True),
        "testimonials": testimonials,
        "graph": trust_graph(c, testimonials),
        "lt_embed": settings.LT_EMBED,
        "updates": c.updates.filter(status="published")[:10],
        "share_url": share_url,
        "via": via,
        "donation_disclosure": DONATION_DISCLOSURE,
    })


def respond(request, slug, cta_id):
    c = _campaign_or_404(slug, request)
    cta = get_object_or_404(CTA, pk=cta_id, campaign=c, enabled=True)
    form = ResponseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        r = form.save(commit=False)
        r.cta = cta
        r.save()
        return render(request, "campaigns/thanks.html", {"c": c, "cta": cta})
    return render(request, "campaigns/respond.html", {"c": c, "cta": cta, "form": form})


@login_required
def add_testimony(request, slug):
    c = _campaign_or_404(slug, request)
    form = TestimonialForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        t = form.save(commit=False)
        t.campaign = c
        t.author = request.user
        t.status = "pending"
        t.save()
        return render(request, "campaigns/thanks.html",
                      {"c": c, "note": "Your testimony is in — it appears after review."})
    return render(request, "campaigns/testimony.html",
                  {"c": c, "form": form, "lt_api": settings.LT_API, "lt_embed": settings.LT_EMBED})


@login_required
def my_share_link(request, slug):
    c = _campaign_or_404(slug, request)
    link, _ = ShareLink.objects.get_or_create(campaign=c, volunteer=request.user)
    return render(request, "campaigns/share.html",
                  {"c": c, "link": link, "share_url": f"{settings.PUBLIC_URL}/s/{link.code}"})


def share_redirect(request, code):
    link = get_object_or_404(ShareLink, code=code)
    ShareLink.objects.filter(pk=link.pk).update(clicks=F("clicks") + 1)
    return redirect(reverse("campaign", args=[link.campaign.slug]) + f"?via={link.code}")


def share_card(request, slug, kind):
    c = _campaign_or_404(slug, request)
    if kind not in ("story", "post"):
        raise Http404
    url = f"{settings.PUBLIC_URL}/c/{c.slug}/"
    png = render_card(c, url, kind)
    resp = HttpResponse(png, content_type="image/png")
    resp["Content-Disposition"] = f'attachment; filename="{c.slug}-{kind}.png"'
    return resp


def trust_graph(campaign, testimonials):
    """Nodes and edges for the trust-network SVG: the campaign in the middle, one node
    per published testimonial around it. Drawn from rows that exist; nothing invented."""
    n = len(testimonials)
    if not n:
        return None
    cx, cy, r = 280, 120, 90
    nodes = []
    for i, t in enumerate(testimonials):
        a = 2 * math.pi * i / n - math.pi / 2
        label = t.display_name if (t.show_identity and t.display_name) else (t.relationship or "vouch")
        nodes.append({"x": round(cx + r * math.cos(a)), "y": round(cy + r * math.sin(a)),
                      "label": label[:14], "href": t.linkedclaim_uri})
    return {"cx": cx, "cy": cy, "label": campaign.title[:12], "nodes": nodes}
