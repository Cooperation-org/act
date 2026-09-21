from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured
import math

from django.db.models import Count, F, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

import secrets

from django.utils import timezone
from django.utils.text import slugify

from .card import render_card
from .context import site_campaigns, site_org
from .donation_text import DONATION_DISCLOSURE
from .forms import ResponseForm, TestimonialForm
from .linkedtrust import claim_url, create_endorsement
from .models import CTA, Campaign, ShareLink, Testimonial, Update
from .vouch_sync import notify_new_vouches, subject_uri


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
    form = ResponseForm(request.POST or None, cta_kind=cta.kind)
    if request.method == "POST" and form.is_valid():
        r = form.save(commit=False)
        r.cta = cta
        r.save()
        return render(request, "campaigns/thanks.html", {"c": c, "cta": cta})
    return render(request, "campaigns/respond.html", {"c": c, "cta": cta, "form": form})


def _voucher_identity(request):
    """(source_uri, default_name) for whoever is vouching.

    Signed in: their LinkedTrust user URI (a real, checkable source) and profile name.
    Walk-up: no source yet — the caller falls back to a link they gave or an anonymous
    anchor. Nobody is required to sign in; life is hard enough."""
    user = request.user
    if user.is_authenticated:
        ident = user.sso_identities.first()
        source = f"{settings.LT_API}/users/{ident.sub}" if ident else ""
        return source, (user.get_full_name() or user.first_name or "")
    return "", ""


def vouch(request, slug):
    """act's own vouch form: write a few words and/or record a short video, and act posts
    an ENDORSES claim about the campaign with the person as its source (workers.vc's
    plumbing, our front end). Works walk-up on a phone; signing in is encouraged, not
    required, and only strengthens the source. The video recorder uploads to LinkedTrust
    storage and hands back a URL we attach to the claim."""
    c = _campaign_or_404(slug, request)
    source_uri, default_name = _voucher_identity(request)
    form = {"statement": "", "name": default_name, "link": "", "video_url": "", "show_identity": True}
    errors = []
    if request.method == "POST":
        for k in ("statement", "name", "link", "video_url"):
            form[k] = (request.POST.get(k) or "").strip()
        form["show_identity"] = bool(request.POST.get("show_identity"))
        if not form["statement"] and not form["video_url"]:
            errors.append("Add a few words or record a short video.")
        link = form["link"]
        if link and not link.startswith(("http://", "https://")):
            link = "https://" + link
        # Source: signed-in identity wins; else a link they gave; else an anonymous anchor
        # under the campaign, so the claim always has a valid source URI and no name leaks.
        source = source_uri or link or f"{subject_uri(c)}#voucher-{secrets.token_urlsafe(6)}"
        name = form["name"] or default_name
        if not errors:
            claim_id, err = create_endorsement(
                campaign_url=subject_uri(c),
                statement=form["statement"] or "Vouches for this work.",
                source_uri=source,
                name=name if form["show_identity"] else "",  # public claim: name only with consent
                video_url=form["video_url"],
            )
            if claim_id:
                status = "pending" if c.moderate_vouches else "published"
                t = Testimonial.objects.create(
                    campaign=c, author=request.user if request.user.is_authenticated else None,
                    quote=form["statement"], display_name=name, show_identity=form["show_identity"],
                    video_url=form["video_url"], source_uri=source, claim_id=claim_id,
                    linkedclaim_uri=claim_url(claim_id), signed_at=timezone.now(), status=status)
                notify_new_vouches(c, [t], status)
                return render(request, "campaigns/vouched.html",
                              {"c": c, "count": 1, "held": c.moderate_vouches})
            errors.append(f"We could not post your vouch just now. Please try again. ({err})")
    return render(request, "campaigns/vouch.html",
                  {"c": c, "form": form, "errors": errors,
                   "signed_in_as": default_name if request.user.is_authenticated else "",
                   "sso_enabled": settings.LINKEDTRUST_SSO_ENABLED,
                   "lt_api": settings.LT_API, "lt_embed": settings.LT_EMBED})


def vouch_signin(request, slug):
    """Start LinkedTrust sign-in and come back to this campaign's vouch page.

    Optional convenience: stashes the return path so the callback (campaigns.sso) lands
    the person back on the form, not in /admin. Only reachable when SSO is configured."""
    c = _campaign_or_404(slug, request)
    if not settings.LINKEDTRUST_SSO_ENABLED:
        return redirect("vouch", slug=c.slug)
    request.session["act_post_login_next"] = reverse("vouch", args=[c.slug])
    return redirect("/api/v1/auth/linkedtrust/redirect")


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
                      {"c": c, "note": "Your testimony is in. It appears after review."})
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
