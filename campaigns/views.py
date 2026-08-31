from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .card import render_card
from .donation_text import DONATION_DISCLOSURE
from .forms import ResponseForm, TestimonialForm
from .models import CTA, Campaign, ShareLink


def home(request):
    campaigns = Campaign.objects.filter(status="published").select_related("org")
    return render(request, "campaigns/home.html", {"campaigns": campaigns})


def why(request):
    return render(request, "campaigns/why.html")


def _campaign_or_404(slug, request):
    c = get_object_or_404(Campaign, slug=slug)
    if c.status != "published" and not request.user.is_staff:
        raise Http404
    return c


def campaign(request, slug):
    c = _campaign_or_404(slug, request)
    share_url = f"{settings.PUBLIC_URL}/c/{c.slug}/"
    via = request.GET.get("via", "")
    return render(request, "campaigns/campaign.html", {
        "c": c,
        "ctas": c.ctas.filter(enabled=True),
        "testimonials": c.testimonials.filter(status="published"),
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
    return render(request, "campaigns/testimony.html", {"c": c, "form": form})


@login_required
def my_share_link(request, slug):
    c = _campaign_or_404(slug, request)
    link, _ = ShareLink.objects.get_or_create(campaign=c, volunteer=request.user)
    return render(request, "campaigns/share.html",
                  {"c": c, "link": link, "share_url": f"{settings.PUBLIC_URL}/s/{link.code}"})


def share_redirect(request, code):
    link = get_object_or_404(ShareLink, code=code)
    ShareLink.objects.filter(pk=link.pk).update(clicks=F("clicks") + 1)
    return redirect(f"/c/{link.campaign.slug}/?via={link.code}")


def share_card(request, slug, kind):
    c = _campaign_or_404(slug, request)
    if kind not in ("story", "post"):
        raise Http404
    url = f"{settings.PUBLIC_URL}/c/{c.slug}/"
    png = render_card(c, url, kind)
    resp = HttpResponse(png, content_type="image/png")
    resp["Content-Disposition"] = f'attachment; filename="{c.slug}-{kind}.png"'
    return resp
