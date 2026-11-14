"""Story Kit pages: start, ways to send, send, library, collections, stories.

Who: a program or comms lead at a small org, not technical, on a phone or a laptop.
Every page answers one question for them; anything else is a link away.
"""
import io
import json
import mimetypes
import re
import uuid
from datetime import timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import SuspiciousOperation
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.http import (Http404, HttpResponse, HttpResponseBadRequest, JsonResponse,
                         StreamingHttpResponse)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.views.decorators.cache import never_cache
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from campaigns.admin import user_orgs
from campaigns.models import Org, VolunteerProfile

from . import ai, demo, spouts
from .intake import BrokenFile, add_file, add_reference
from .models import (Artifact, ArtifactKind, ArtifactStatus, Collection, Source, SourceKind, Story,
                     StoryBlock, StoryStatus, StoryTemplate, Tag, TagGroup)

SESSION_ORG = "storykit_org"


# ---- who and which org -------------------------------------------------------

def current_org(request):
    orgs = user_orgs(request.user)
    picked = request.session.get(SESSION_ORG)
    org = orgs.filter(pk=picked).first() if picked else None
    return org or orgs.order_by("pk").first()


def own_or_switch(request, model, pk):
    """The record with this pk in the visitor's current org; when it belongs to another org the
    visitor is in, switch to that org first, so a shared link opens wherever the visitor stood."""
    found = model.objects.filter(pk=pk, org__in=user_orgs(request.user)).select_related("org").first()
    if found is None:
        raise Http404
    if found.org_id != request.org.pk:
        request.session[SESSION_ORG] = found.org_id
        request.org = found.org
    return found


def org_required(view):
    @never_cache
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('artifacts:signin')}?{urlencode({'next': request.get_full_path()})}")
        org = current_org(request)
        if org is None:
            return redirect("artifacts:start")
        request.org = org
        return view(request, *args, **kwargs)
    return wrapper


@never_cache
def signin(request):
    nxt = request.GET.get("next") or reverse("artifacts:home")
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = reverse("artifacts:home")
    if request.user.is_authenticated:
        return redirect(nxt)
    if settings.LINKEDTRUST_SSO_ENABLED:
        request.session["act_post_login_next"] = nxt
        return redirect(f"{settings.BASE_PATH}/api/v1/auth/linkedtrust/redirect")
    return redirect(f"{reverse('admin:login')}?{urlencode({'next': nxt})}")


@never_cache
def demo_open(request):
    """The live demo: your own copy of the sample org, no sign-in needed."""
    sample = demo.sample_org()
    if sample is None:
        raise Http404
    if request.user.is_authenticated:
        mine = user_orgs(request.user).filter(demo_copy__isnull=False).order_by("-pk").first()
        if mine:
            request.session[SESSION_ORG] = mine.pk
            return _landing(request, mine)
    demo.remove_old()
    guest, org = demo.make_copy(sample)
    if request.user.is_authenticated:  # someone signed in trying the demo keeps their login
        VolunteerProfile.objects.create(user=request.user, org=org, is_approver=True)
    else:
        from django.contrib.auth import login
        login(request, guest, backend="django.contrib.auth.backends.ModelBackend")
    request.session[SESSION_ORG] = org.pk
    return _landing(request, org)


@never_cache
def demo_leave(request):
    """From a demo copy to starting your own: a guest signs out and signs in for real."""
    from django.contrib.auth import logout
    start = reverse("artifacts:start")
    if request.user.is_authenticated:
        copy = current_org(request)
        if copy is not None and hasattr(copy, "demo_copy"):  # your practice work comes with you
            start = f"{start}?{urlencode({'keep': demo.keep_token(copy)})}"
        if request.user.demo_copies.exists():
            logout(request)
    return redirect(f"{reverse('artifacts:signin')}?{urlencode({'next': start})}")


def _landing(request, org):
    """Where someone starts: a phone opens on Capture, a laptop on what is waiting in the inbox."""
    ua = request.META.get("HTTP_USER_AGENT", "")
    if "Mobi" in ua or "iPhone" in ua or "Android" in ua:
        return redirect("artifacts:send")
    if Artifact.objects.filter(org=org, status=ArtifactStatus.NEW).exists():
        return redirect(f"{reverse('artifacts:library')}?show=inbox")
    return redirect("artifacts:library")


@never_cache
def home(request):
    if not request.user.is_authenticated:
        return render(request, "artifacts/welcome.html", {"demo_on": demo.sample_org() is not None})
    org = current_org(request)
    if org is None:
        return redirect("artifacts:start")
    if not Artifact.objects.filter(org=org).exists():
        return redirect("artifacts:send")   # a new organization starts by capturing its first moment
    return _landing(request, org)


def _unique_slug(model, base, field="slug", size=46):
    base = slugify(base)
    if len(base) > size:  # cut at a word, not in the middle of one
        base = base[:size].rsplit("-", 1)[0]
    base = base or "org"
    slug, n = base, 2
    while model.objects.filter(**{field: slug}).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


@never_cache
def start(request):
    """Name your org. That is the whole sign-up; everything else has a default."""
    if not request.user.is_authenticated:
        return redirect(f"{reverse('artifacts:signin')}?{urlencode({'next': request.get_full_path()})}")
    error = ""
    keep = request.POST.get("keep") or request.GET.get("keep") or ""
    copy = demo.copy_from_token(keep) if keep and not request.user.demo_copies.exists() else None
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        website = (request.POST.get("website") or "").strip()
        if website and not website.startswith(("http://", "https://")):
            website = f"https://{website}"
        if not name:
            error = "Please give your organization's name."
        elif copy is not None:
            org = demo.adopt(copy, request.user, name, website, slug=_unique_slug(Org, name))
            if not request.user.is_staff:
                request.user.is_staff = True
                request.user.save(update_fields=["is_staff"])
            request.session[SESSION_ORG] = org.pk
            messages.success(request, f"{org.name} is yours. Everything from the demo is here.")
            return redirect("artifacts:library")
        else:
            with transaction.atomic():
                org = Org.objects.create(slug=_unique_slug(Org, name), name=name[:200], website=website[:200])
                VolunteerProfile.objects.create(user=request.user, org=org, is_approver=True)
                if not request.user.is_staff:
                    request.user.is_staff = True
                    request.user.save(update_fields=["is_staff"])
                Source.objects.create(org=org, kind=SourceKind.UPLOAD, label="Phone and computer")
                Source.objects.create(org=org, kind=SourceKind.EMAIL, label="Email")
                if website:
                    Source.objects.create(org=org, kind=SourceKind.LINKEDCLAIMS, label="LinkedClaims about us",
                                          config={"subject": website})
            request.session[SESSION_ORG] = org.pk
            return redirect("artifacts:send")
    return render(request, "artifacts/start.html", {"error": error, "keep": keep if copy else "", "copy": copy,
                                                    "orgs": user_orgs(request.user).filter(demo_copy__isnull=True)})


@org_required
def switch_org(request, pk):
    org = get_object_or_404(user_orgs(request.user), pk=pk)
    request.session[SESSION_ORG] = org.pk
    return redirect("artifacts:library")


# ---- ways in -----------------------------------------------------------------

def _source(org, kind, **match):
    return Source.objects.filter(org=org, kind=kind, **match).first()


def qr_svg(url):
    """A QR code for `url` as inline SVG, scannable by any phone camera."""
    import io

    import segno
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=5, border=2, dark="#1a1a1a", light="#ffffff",
                                    xmldecl=False, svgns=True, omitsize=True,
                                    title="Scan to open Story Kit on your phone")
    return mark_safe(buf.getvalue().decode())


@org_required
def ways(request):
    org = request.org
    email_source = _source(org, SourceKind.EMAIL)
    send_url = request.build_absolute_uri(reverse("artifacts:send"))
    return render(request, "artifacts/ways.html", {
        "org": org, "nav": "ways",
        "send_url": send_url, "send_qr": qr_svg(send_url),
        "email_address": (email_source.config.get("address") if email_source else "") or "",
        "drive_sources": Source.objects.filter(org=org, kind=SourceKind.DRIVE),
        "claim_sources": Source.objects.filter(org=org, kind=SourceKind.LINKEDCLAIMS),
        "count": Artifact.objects.filter(org=org).count(),
    })


def _files_from(request, field="files"):
    return [f for f in request.FILES.getlist(field) if f.size]


def _store_uploads(request, org, files, words, when=None, captions=()):
    """Store each file. Returns ([(artifact, created)] in the order sent, [why each file
    that could not be opened was left out]). captions[i] is what was said about file i
    alone; it follows the words said about all of them."""
    source = _source(org, SourceKind.UPLOAD) or Source.objects.create(org=org, kind=SourceKind.UPLOAD,
                                                                      label="Phone and computer")
    who = request.user.email or request.user.get_username()
    saved, broken = [], []
    for i, f in enumerate(files):
        own = (captions[i] if i < len(captions) else "").strip()
        said = "\n\n".join(x for x in (words, own) if x)
        try:
            saved.append(add_file(org, data=f.read(), filename=f.name, content_type=f.content_type or "",
                                  source=source, words=said, sent_by=who, added_by=request.user, happened_at=when))
        except BrokenFile as e:
            broken.append(str(e))
    return saved, broken


def _captions(request):
    try:
        out = json.loads(request.POST.get("captions") or "[]")
    except ValueError:
        return []
    return [str(c)[:5000] for c in out] if isinstance(out, list) else []


def _with_extras(request, org, saved):
    """Apply what was said on the way in: labels, and whether everyone shown agreed."""
    labels = _tags_from(org, request.POST.get("tags"))
    agreed = request.POST.get("consent") == "on"
    for a, _ in saved:
        if labels:
            a.tags.add(*labels)
        if agreed and not a.consent:
            a.consent = True
            a.save(update_fields=["consent"])


def _sent_row(a):
    """One piece as the page's script shows it after a send."""
    return {"kind": a.kind, "kind_label": a.get_kind_display() if a.kind != "data" else "Number",
            "title": a.title or (a.words[:90] if a.words else a.filename or a.source_url or a.get_kind_display()),
            "when": a.when.strftime("%a %d %b").replace(" 0", " "), "url": reverse("artifacts:artifact", args=[a.pk])}


def _sent_by_me(request, org, limit=3):
    """What this person sent this week, or their latest few when nothing is from this week."""
    mine = Artifact.objects.filter(org=org, added_by=request.user).exclude(status=ArtifactStatus.HIDDEN) \
        .annotate(in_story=Exists(StoryBlock.objects.filter(artifact=OuterRef("pk")))).order_by("-received_at")
    week = mine.filter(received_at__gte=timezone.now() - timedelta(days=7))
    this_week = week.exists()
    return list((week if this_week else mine)[:limit]), this_week


@org_required
def send(request):
    org = request.org
    if request.method == "POST":
        files = _files_from(request)
        words = (request.POST.get("words") or "").strip()
        day = parse_date(request.POST.get("happened_on") or "")
        when = timezone.make_aware(timezone.datetime(day.year, day.month, day.day, 12)) if day else None
        ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"  # the page's own script, for progress
        if not files and not words:
            if ajax:
                return JsonResponse({"ok": False, "error": "Choose a photo, video or file, or write something."}, status=400)
            messages.error(request, "Choose a photo, video or file, or write something.")
            return redirect("artifacts:send")
        if files:
            saved, broken = _store_uploads(request, org, files, words, when, _captions(request))
            new = sum(1 for _, created in saved if created)
            same = len(saved) - new
            text = " ".join([f"Saved {new} {'item' if new == 1 else 'items'}."]
                            + ([f"{same} {'was' if same == 1 else 'were'} already here."] if same else [])
                            + broken)
            if not saved:
                if ajax:
                    return JsonResponse({"ok": False, "error": " ".join(broken)}, status=400)
                messages.error(request, " ".join(broken))
                return redirect("artifacts:send")
        else:
            source = _source(org, SourceKind.UPLOAD)
            saved = [add_file(org, data=words.encode(), filename="note.txt", content_type="text/plain", source=source,
                              words=words, sent_by=request.user.email or request.user.get_username(),
                              added_by=request.user, happened_at=when)]
            new, text = 1, "Saved your note."
        _with_extras(request, org, saved)
        if ajax:
            return JsonResponse({"ok": True, "saved": new, "message": text, "library": reverse("artifacts:library"),
                                 "items": [_sent_row(a) for a, _ in saved][:3]})
        messages.success(request, text)
        return redirect("artifacts:send")
    sent, this_week = _sent_by_me(request, org)
    return render(request, "artifacts/send.html", {
        "org": org, "nav": "send", "today": timezone.localdate().isoformat(), "sent": sent, "this_week": this_week,
        "mine": request.user.email or request.user.get_username()})


@csrf_exempt
def share_target(request):
    """The phone's share sheet posts here (manifest share_target). Only accepted when the
    browser itself started the request, so another site cannot post into a library."""
    if request.method != "POST":
        return redirect("artifacts:send")
    if request.headers.get("Sec-Fetch-Site", "none") not in ("none", "same-origin"):
        return HttpResponseBadRequest("Share from your phone's share menu.")
    if not request.user.is_authenticated or current_org(request) is None:
        return redirect(f"{reverse('artifacts:signin')}?{urlencode({'next': reverse('artifacts:send')})}")
    org = current_org(request)
    words = "\n".join(x for x in (request.POST.get("title"), request.POST.get("text")) if x).strip()
    files = _files_from(request)
    link = (request.POST.get("url") or "").strip()
    if files:
        saved, broken = _store_uploads(request, org, files, words)
        new = sum(1 for _, created in saved if created)
        messages.success(request, " ".join([f"Saved {new} shared {'item' if new == 1 else 'items'}."] + broken))
    elif link.startswith(("http://", "https://")):
        add_reference(org, kind=ArtifactKind.LINK, url=link, words=words, source=_source(org, SourceKind.LINK),
                      sent_by=request.user.email, added_by=request.user, happened_at=timezone.now())
        messages.success(request, "Saved the shared link.")
    elif words:
        add_file(org, data=words.encode(), filename="note.txt", content_type="text/plain", words=words,
                 sent_by=request.user.email, added_by=request.user, source=_source(org, SourceKind.UPLOAD))
        messages.success(request, "Saved the shared text.")
    return redirect("artifacts:send")


@org_required
@require_POST
def add_link(request):
    org = request.org
    url = (request.POST.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        messages.error(request, "That does not look like a web address. It should start with https://")
        return redirect(request.POST.get("back") or "artifacts:ways")
    source = _source(org, SourceKind.LINK) or Source.objects.create(org=org, kind=SourceKind.LINK, label="Links")
    _, created = add_reference(org, kind=ArtifactKind.LINK, url=url, words=(request.POST.get("words") or "").strip(),
                               source=source, sent_by=request.user.email or request.user.get_username(),
                               added_by=request.user, happened_at=timezone.now())
    messages.success(request, "Saved the link." if created else "That link is already here.")
    return redirect(request.POST.get("back") or "artifacts:ways")


@org_required
@require_POST
def add_number(request):
    """A figure someone can quote, kept with what it counts and where it came from."""
    org = request.org
    value = (request.POST.get("value") or "").strip()[:60]
    what = (request.POST.get("what") or "").strip()[:300]
    where = (request.POST.get("where") or "").strip()[:300]
    back = request.POST.get("back") or "artifacts:send"
    if not value or not what or not where:
        messages.error(request, "Give the number, say what it counts, and say where it came from.")
        return redirect(back)
    source = _source(org, SourceKind.UPLOAD) or Source.objects.create(org=org, kind=SourceKind.UPLOAD,
                                                                      label="Phone and computer")
    a, _ = add_reference(org, kind=ArtifactKind.DATA, url=f"number:{uuid.uuid4().hex}", title=value, source=source,
                         sent_by=request.user.email or request.user.get_username(), added_by=request.user,
                         happened_at=timezone.now(), source_ref=where)
    a.about, a.source_url = what, ""   # the web address was only a way to tell two numbers apart
    a.save(update_fields=["about", "source_url"])
    messages.success(request, "Saved the number.")
    return redirect(back)


@org_required
@require_POST
def add_drive(request):
    org = request.org
    url = (request.POST.get("folder") or "").strip()
    folder = spouts.drive_folder_id(url)
    if not folder:
        messages.error(request, "Paste the folder's share link from Google Drive (it contains /folders/).")
        return redirect("artifacts:ways")
    problem = spouts.drive_folder_problem(folder)
    if problem:
        messages.error(request, problem)
        return redirect("artifacts:ways")
    source, _ = Source.objects.get_or_create(org=org, kind=SourceKind.DRIVE, config__folder_id=folder,
                                             defaults={"label": "Google Drive folder",
                                                       "config": {"folder_id": folder, "folder_url": url}})
    spouts.check_in_background(source)
    messages.info(request, "Copying the folder now. New items show up in your Library as they arrive.")
    return redirect("artifacts:ways")


@org_required
@require_POST
def add_claims(request):
    org = request.org
    url = (request.POST.get("subject") or "").strip()
    if not url.startswith(("http://", "https://")):
        messages.error(request, "Give the web address the claims are about, starting with https://")
        return redirect("artifacts:ways")
    source, _ = Source.objects.get_or_create(org=org, kind=SourceKind.LINKEDCLAIMS, config__subject=url,
                                             defaults={"label": "LinkedClaims", "config": {"subject": url}})
    messages.info(request, f"LinkedClaims about {url}: {spouts.check(source)}")
    return redirect("artifacts:ways")


@org_required
@require_POST
def check_source(request, pk):
    source = get_object_or_404(Source, pk=pk, org=request.org)
    if source.kind == SourceKind.DRIVE:
        spouts.check_in_background(source)
        messages.info(request, "Copying new files from the folder now.")
    else:
        messages.info(request, f"{source.label}: {spouts.check(source) or 'nothing to check'}")
    return redirect("artifacts:ways")


# ---- library -----------------------------------------------------------------

FILTER_KEYS = ("kind", "tag", "part", "source", "person", "q", "since", "until", "show", "sort", "signed", "consent")
MULTI_KEYS = ("tag", "part")
SORTS = {"": ("-received_at",), "happened": ("-happened_at", "-received_at"),
         "oldest": ("happened_at", "received_at"), "title": ("title", "filename")}


def filtered(org, f):
    qs = Artifact.objects.filter(org=org).select_related("source").prefetch_related("tags")
    qs = qs.annotate(in_story=Exists(StoryBlock.objects.filter(artifact=OuterRef("pk"))))
    if f.get("show") == "hidden":
        qs = qs.filter(status=ArtifactStatus.HIDDEN)
    else:
        qs = qs.exclude(status=ArtifactStatus.HIDDEN)
        if f.get("show") == "inbox":
            qs = qs.filter(status=ArtifactStatus.NEW)
    if f.get("kind"):
        qs = qs.filter(kind=f["kind"])
    for key in MULTI_KEYS:  # every chosen label must be on the item
        names = f.get(key) or []
        for name in ([names] if isinstance(names, str) else names):
            qs = qs.filter(pk__in=Artifact.objects.filter(org=org, tags__name=name).values("pk"))
    if f.get("source"):
        qs = qs.filter(source__kind=f["source"])
    if f.get("signed") == "yes":
        qs = qs.filter(claim_id__isnull=False)
    if f.get("consent") == "missing":
        qs = qs.filter(consent=False)
    if f.get("person"):
        qs = qs.filter(sent_by=f["person"])
    if f.get("q"):
        q = f["q"]
        qs = qs.filter(Q(words__icontains=q) | Q(title__icontains=q) | Q(about__icontains=q)
                       | Q(filename__icontains=q) | Q(tags__name__icontains=q))
    since, until = parse_date(f.get("since") or ""), parse_date(f.get("until") or "")
    if since:
        qs = qs.filter(Q(happened_at__date__gte=since) | Q(happened_at__isnull=True, received_at__date__gte=since))
    if until:
        qs = qs.filter(Q(happened_at__date__lte=until) | Q(happened_at__isnull=True, received_at__date__lte=until))
    return qs.distinct()


def _filter_from(request):
    f = {}
    for k in FILTER_KEYS:
        if k in MULTI_KEYS:
            vals = [v.strip() for v in request.GET.getlist(k) if v.strip()]
            if vals:
                f[k] = vals
        elif request.GET.get(k, "").strip():
            f[k] = request.GET[k].strip()
    return f


def _tags_from(org, text, group=TagGroup.LABEL):
    """Labels named in a comma-separated text. A new name is made in `group`; an existing
    label keeps its group."""
    names = [t.strip().lower()[:60] for t in (text or "").replace(";", ",").split(",") if t.strip()]
    return [Tag.objects.get_or_create(org=org, name=n, defaults={"group": group})[0] for n in names]


CHIPS_SHOWN = 6


def _chips(f, key, tags):
    """(shown, more): (tag, address, chosen) per label, most used first. The address adds
    the label to the filter, or takes it off when chosen. Chosen labels and the most used
    few are shown; the rest wait behind "+N more"."""
    out = []
    for t in tags:
        on = t.name in f.get(key, [])
        out.append((t, _without(f, key, t.name) if on else _with(f, key, t.name), on))
    shown = [c for c in out if c[2]]
    for c in out:
        if len(shown) >= CHIPS_SHOWN:
            break
        if not c[2]:
            shown.append(c)
    return shown, [c for c in out if c not in shown]


def _with(f, key, value):
    rest = dict(f)
    rest[key] = list(f.get(key, [])) + [value]
    return f"{reverse('artifacts:library')}?{urlencode(rest, doseq=True)}"


def _set(f, key, value):
    """The library address with one single-valued filter set, or cleared when value is empty."""
    rest = {k: x for k, x in f.items() if k != key}
    if value:
        rest[key] = value
    q = urlencode(rest, doseq=True)
    return reverse("artifacts:library") + (f"?{q}" if q else "")


KIND_PLURAL = {"photo": "Photos", "video": "Videos", "voice": "Voice", "text": "Notes", "file": "Files",
               "link": "Links", "claim": "Claims", "data": "Numbers"}
KIND_SINGLE = {"photo": "Photo", "video": "Video", "voice": "Voice", "text": "Note", "file": "File",
               "link": "Link", "claim": "Claim", "data": "Number"}
SEGMENTS = 4  # All plus the four most used kinds fit a phone; the rest are in the filter sheet


def _active(f):
    """(words, address that takes it off) for each filter in force, so each can be removed in one tap."""
    out = [(v, _without(f, k, v)) for k in MULTI_KEYS for v in f.get(k, [])]
    if f.get("q"):
        out.append((f"\u201c{f['q']}\u201d", _set(f, "q", "")))
    for key, label in (("person", ""), ("since", "From "), ("until", "To ")):
        if f.get(key):
            out.append((f"{label}{f[key]}", _set(f, key, "")))
    if f.get("source"):
        out.append((dict(SourceKind.choices).get(f["source"], f["source"]), _set(f, "source", "")))
    if f.get("show") == "hidden":
        out.append(("Archived", _set(f, "show", "")))
    if f.get("show") == "inbox":
        out.append(("Not looked at yet", _set(f, "show", "")))
    if f.get("signed") == "yes":
        out.append(("Signed", _set(f, "signed", "")))
    if f.get("consent") == "missing":
        out.append(("Missing consent", _set(f, "consent", "")))
    return out


def _checked(f):
    """The three questions the inbox is read by: (words, address, on). Each is one tap to set or clear."""
    out = []
    for label, key, value in (("Not looked at yet", "show", "inbox"), ("Signed", "signed", "yes"),
                              ("Missing consent", "consent", "missing")):
        on = f.get(key) == value
        out.append((label, _set(f, key, "" if on else value), on))
    return out


def _date_options(f):
    """Recent-time choices for the rail: (words, since date, chosen)."""
    today = timezone.localdate()
    options = [("Any time", ""), ("Last 7 days", (today - timedelta(days=7)).isoformat()),
               ("Last 30 days", (today - timedelta(days=30)).isoformat()), ("This year", today.replace(month=1, day=1).isoformat())]
    return [(label, value, f.get("since", "") == value) for label, value in options]


def _without(f, key, value):
    """The library address with one chosen label taken off."""
    rest = {k: ([x for x in v if x != value] if k == key else v) for k, v in f.items()}
    return f"{reverse('artifacts:library')}?{urlencode(rest, doseq=True)}"


@org_required
def library(request):
    org = request.org
    if request.method == "POST":
        ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
        picked = Artifact.objects.filter(org=org, pk__in=ids)
        action = request.POST.get("action")
        back = request.POST.get("back") or reverse("artifacts:library")
        if action == "sort":
            if not ai.enabled():
                messages.error(request, "The AI helper is not switched on.")
                return redirect(back)
            todo = ids or list(Artifact.objects.filter(org=org, tags__isnull=True)
                               .exclude(status=ArtifactStatus.HIDDEN).values_list("pk", flat=True))
            if not todo:
                messages.info(request, "Everything already has labels. Tick items to sort them again.")
                return redirect(back)
            ai.sort_in_background(org, todo)
            messages.success(request, f"Sorting {len(todo)} {'item' if len(todo) == 1 else 'items'}. "
                             "Labels appear as each batch is done; reload in a minute.")
            return redirect(back)
        if not ids and action != "collect_filter":
            messages.error(request, "Tick at least one item first.")
            return redirect(back)
        if action == "tag":
            group = TagGroup.PART if request.POST.get("group") == TagGroup.PART else TagGroup.LABEL
            tags = _tags_from(org, request.POST.get("tags"), group)
            for a in picked:
                a.tags.add(*tags)
            messages.success(request, f"Labeled {picked.count()}.")
        elif action == "hide":
            picked.update(status=ArtifactStatus.HIDDEN)
            messages.success(request, f"Archived {len(ids)}. Filters → Show archived items brings them back.")
        elif action == "unhide":
            picked.update(status=ArtifactStatus.KEPT)
            messages.success(request, f"Brought back {len(ids)}.")
        elif action == "delete":
            n = delete_artifacts(org, picked)
            messages.success(request, f"Deleted {n} for good.")
        elif action in ("collect", "collect_filter"):
            name = (request.POST.get("name") or "").strip()
            f = json.loads(request.POST.get("filter") or "{}") if action == "collect_filter" else {}
            items = filtered(org, f) if action == "collect_filter" else picked
            if not name:
                messages.error(request, "Give the collection a name.")
                return redirect(back)
            c = Collection.objects.create(org=org, name=name[:200], filter=f)
            c.artifacts.add(*items)
            return redirect("artifacts:collection", pk=c.pk)
        elif action == "story":
            s = _new_story(org, "Untitled story", list(picked.order_by("happened_at", "received_at")))
            return redirect("artifacts:story_edit", slug=s.slug)
        return redirect(back)
    f = _filter_from(request)
    qs = filtered(org, f).order_by(*SORTS.get(f.get("sort", ""), SORTS[""]))
    everything = Artifact.objects.filter(org=org)
    in_use = Tag.objects.filter(org=org).annotate(
        n=Count("artifacts", filter=~Q(artifacts__status=ArtifactStatus.HIDDEN), distinct=True)) \
        .filter(n__gt=0).order_by("-n", "name")
    chosen = [(k, v, _without(f, k, v)) for k in MULTI_KEYS for v in f.get(k, [])]
    present = [k for k, _ in ArtifactKind.choices if everything.filter(kind=k).exists()]
    shown = present[:SEGMENTS]
    if f.get("kind") in present and f["kind"] not in shown:
        shown = shown[:-1] + [f["kind"]]  # the one chosen is always on show
    kind_links = [("All", _set(f, "kind", ""), not f.get("kind"))] + [
        (KIND_PLURAL.get(k, k.title()), _set(f, "kind", k), f.get("kind") == k) for k in shown]
    all_kind_links = [("All", _set(f, "kind", ""), not f.get("kind"))] + [
        (KIND_SINGLE.get(k, k.title()), _set(f, "kind", k), f.get("kind") == k) for k in present]
    me = request.user.email or request.user.get_username()
    email_source = _source(org, SourceKind.EMAIL)
    return render(request, "artifacts/library.html", {
        "title": "Inbox" if f.get("show") == "inbox" else "My uploads" if f.get("person") == me and len(f) == 1 else "Library",
        "all_kind_links": all_kind_links, "checked": _checked(f), "date_options": _date_options(f),
        "inbox_count": everything.filter(status=ArtifactStatus.NEW).count(),
        "email_address": (email_source.config.get("address") if email_source else "") or "",
        "rail_keep": [(k, x) for k, val in f.items() if k not in ("person", "since") for x in (val if isinstance(val, list) else [val])],
        "kind_links": kind_links, "active": _active(f),
        "keep_all": [(k, x) for k, val in f.items() if k != "q" for x in (val if isinstance(val, list) else [val])],
        "org": org, "items": qs[:300], "total": qs.count(), "f": f, "filter_json": json.dumps(f),
        "nav": "inbox" if f.get("show") == "inbox" else
               "mine" if f.get("person") in (request.user.email, request.user.get_username()) and len(f) == 1 else "library",
        "any_at_all": everything.exists(),
        "kinds": [(k, label) for k, label in ArtifactKind.choices if everything.filter(kind=k).exists()],
        "parts": _chips(f, "part", [t for t in in_use if t.group == TagGroup.PART]),
        "labels": _chips(f, "tag", [t for t in in_use if t.group != TagGroup.PART]),
        "keep": [(k, x) for k in MULTI_KEYS for x in f.get(k, [])],
        "chosen": chosen, "here": request.get_full_path(), "ai_on": ai.enabled(),
        "unlabeled": everything.filter(tags__isnull=True).exclude(status=ArtifactStatus.HIDDEN).count(),
        "source_kinds": [(k, label) for k, label in SourceKind.choices if everything.filter(source__kind=k).exists()],
        "people": everything.exclude(sent_by="").values_list("sent_by", flat=True).distinct().order_by("sent_by"),
        "collections": Collection.objects.filter(org=org),
        "back": request.get_full_path(),
    })


@org_required
def artifact(request, pk):
    a = own_or_switch(request, Artifact, pk)
    org = request.org
    if request.method == "POST":
        if request.POST.get("action") == "delete":
            delete_artifacts(org, Artifact.objects.filter(pk=a.pk))
            messages.success(request, "Deleted for good.")
            return redirect("artifacts:library")
        a.title = (request.POST.get("title") or "").strip()[:300]
        a.about = (request.POST.get("about") or "").strip()[:300]
        if "words" in request.POST:
            a.words = request.POST["words"].strip()
        a.consent = request.POST.get("consent") == "on"
        if a.status == ArtifactStatus.NEW:
            a.status = ArtifactStatus.KEPT
        a.save()
        a.tags.set(_tags_from(org, request.POST.get("parts"), TagGroup.PART)
                   + _tags_from(org, request.POST.get("tags")))
        messages.success(request, "Saved.")
        return redirect("artifacts:artifact", pk=a.pk)
    if request.GET.get("panel"):
        return render(request, "artifacts/_panel.html", {"a": a, "org": org})
    return render(request, "artifacts/artifact.html", {
        "org": org, "nav": "library", "a": a,
        "part_text": ", ".join(t.name for t in a.tags.all() if t.group == TagGroup.PART),
        "tag_text": ", ".join(t.name for t in a.tags.all() if t.group != TagGroup.PART),
        "ai_labels": {x.get("label") for x in a.ai_labels if x.get("by") == "ai"},
        "lt_embed": settings.LT_EMBED})


LABEL_UNDO = "storykit_label_undo"  # the last label deleted or merged, so it can be brought back


@org_required
def labels(request):
    """Every label, with how many items carry it: rename, merge, move between
    'part of our org' and plain label, or delete."""
    org = request.org
    if request.method == "POST" and request.POST.get("action") == "undo":
        undo = request.session.pop(LABEL_UNDO, None)
        if undo and undo.get("org") == org.pk:
            back, _ = Tag.objects.get_or_create(org=org, name=undo["name"], defaults={"group": undo["group"]})
            items = Artifact.objects.filter(org=org, pk__in=undo["ids"])
            back.artifacts.add(*items)
            into = Tag.objects.filter(org=org, pk=undo.get("into")).first()
            if into:  # a merge: the label it went into comes off the items that did not have it before
                into.artifacts.remove(*items.exclude(pk__in=undo["had"]))
            messages.success(request, f"Brought back “{back.name}”.")
        return redirect("artifacts:labels")
    if request.method == "POST":
        t = get_object_or_404(Tag, pk=request.POST.get("pk"), org=org)
        action = request.POST.get("action")
        ids = list(t.artifacts.values_list("pk", flat=True))
        if action == "delete":
            request.session[LABEL_UNDO] = {"org": org.pk, "name": t.name, "group": t.group, "ids": ids}
            t.delete()
            messages.success(request, f"Took the label “{t.name}” off everything.")
        elif action == "save":
            t.group = TagGroup.PART if request.POST.get("group") == TagGroup.PART else TagGroup.LABEL
            name = (request.POST.get("name") or "").strip().lower()[:60] or t.name
            same = Tag.objects.filter(org=org, name=name).exclude(pk=t.pk).first()
            if same:  # renamed onto an existing label: merge into it
                request.session[LABEL_UNDO] = {"org": org.pk, "name": t.name, "group": t.group, "ids": ids,
                                               "into": same.pk,
                                               "had": list(same.artifacts.filter(pk__in=ids).values_list("pk", flat=True))}
                for a in t.artifacts.all():
                    a.tags.add(same)
                t.delete()
                messages.success(request, f"Merged “{t.name}” into “{same.name}”.")
            else:
                t.name = name
                t.save()
                request.session.pop(LABEL_UNDO, None)
                messages.success(request, "Saved.")
        return redirect("artifacts:labels")
    tags = Tag.objects.filter(org=org).annotate(n=Count("artifacts", distinct=True)).order_by("group", "name")
    undo = request.session.get(LABEL_UNDO)
    return render(request, "artifacts/labels.html", {
        "org": org, "nav": "labels", "tags": tags, "PART": TagGroup.PART,
        "undo": undo if undo and undo.get("org") == org.pk else None})


@org_required
@require_POST
def artifact_quick(request, pk):
    """One edit from a tile, saved as made: {"title": ...} or {"status": "hidden"|"kept"}."""
    a = get_object_or_404(Artifact, pk=pk, org=request.org)
    try:
        data = json.loads(request.body or "{}")
    except ValueError:
        return HttpResponseBadRequest("json")
    fields = []
    if "title" in data:
        a.title = str(data["title"]).strip()[:300]
        fields.append("title")
    if data.get("status") in (ArtifactStatus.HIDDEN, ArtifactStatus.KEPT):
        a.status = data["status"]
        fields.append("status")
    if fields:
        a.save(update_fields=fields)
    return JsonResponse({"ok": True, "status": a.status, "title": a.title})


def delete_artifacts(org, qs):
    """Delete for good: the record, its file and its thumbnails, and every place a story
    showed it. A file another record still points at (a demo copy shares the sample's
    files) stays on disk. Returns how many were deleted."""
    n = 0
    for a in list(qs.filter(org=org)):
        StoryBlock.objects.filter(artifact=a, text="").delete()
        name = a.file.name if a.file else ""
        a.delete()
        if name and not Artifact.objects.filter(file=name).exists():
            for path in [name] + [thumb_path(name, w) for w in THUMB_WIDTHS]:
                try:
                    default_storage.delete(path)
                except (OSError, SuspiciousOperation):
                    pass
        n += 1
    return n


# ---- collections -------------------------------------------------------------

@org_required
def collections(request):
    return render(request, "artifacts/collections.html", {
        "org": request.org, "nav": "collections", "collections": Collection.objects.filter(org=request.org)})


@org_required
def collection(request, pk):
    c = own_or_switch(request, Collection, pk)
    org = request.org
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "remove":
            c.artifacts.remove(*Artifact.objects.filter(org=org, pk__in=request.POST.getlist("ids")))
        elif action == "refresh" and c.filter:
            c.artifacts.add(*filtered(org, c.filter))
        elif action == "rename":
            c.name = (request.POST.get("name") or c.name).strip()[:200]
            c.description = (request.POST.get("description") or "").strip()
            c.save()
            messages.success(request, "Saved.")
        elif action == "delete":
            c.delete()
            messages.success(request, f"Deleted the collection “{c.name}”. Its items are still in your Library.")
            return redirect("artifacts:collections")
        elif action == "story":
            s = _new_story(org, c.name, list(c.artifacts.order_by("happened_at", "received_at")), collection=c)
            return redirect("artifacts:story_edit", slug=s.slug)
        return redirect("artifacts:collection", pk=c.pk)
    return render(request, "artifacts/collection.html", {
        "org": org, "nav": "collections", "c": c, "items": c.artifacts.prefetch_related("tags").order_by("happened_at", "received_at"),
        "stories": c.stories.all()})


# ---- stories -----------------------------------------------------------------

def _new_story(org, title, artifacts, collection=None, template=StoryTemplate.STORY):
    s = Story.objects.create(org=org, title=title[:200], slug=_unique_slug(Story, f"{org.slug}-{title}"),
                             collection=collection, template=template)
    StoryBlock.objects.bulk_create([StoryBlock(story=s, order=i, artifact=a) for i, a in enumerate(artifacts)])
    return s


ONEPAGE_PIECES = 9


def _once(blocks):
    """(artifact id, text, by_ai) blocks with each piece shown once: a piece that comes
    back later keeps only its text, as a paragraph. Empty blocks are dropped."""
    seen = set()
    for aid, text, by_ai in blocks:
        if aid in seen:
            aid = None
        if aid is None and not text:
            continue
        if aid is not None:
            seen.add(aid)
        yield aid, text, by_ai


def _by_date(qs):
    return sorted(qs, key=lambda a: a.when)


@org_required
def stories(request):
    """The org's stories, and two ways to start one: a timeline over dates you pick,
    or a one-page summary. Either can be narrowed to one label."""
    org = request.org
    if request.method == "POST":
        template = request.POST.get("template")
        if template == "blank":  # an empty story: pick its pieces in the editor
            s = _new_story(org, "Untitled story", [])
            return redirect("artifacts:story_edit", slug=s.slug)
        label = request.POST.get("label") or ""
        key, _, name = label.partition(":")
        f = {key: [name]} if key in MULTI_KEYS and name else {}
        if template == StoryTemplate.TIMELINE:
            f.update({k: request.POST.get(k, "") for k in ("since", "until") if request.POST.get(k)})
            items = _by_date(filtered(org, f)[:300])
            if not items:
                messages.error(request, "Nothing falls between those dates.")
                return redirect("artifacts:stories")
            first, last = items[0].when, items[-1].when
            span = first.strftime("%B %Y") if first.strftime("%Y%m") == last.strftime("%Y%m") \
                else f"{first:%B %Y} to {last:%B %Y}"
            title = f"{name.title() if name else org.name}, {span}"
        elif template == StoryTemplate.ONEPAGE:
            qs = filtered(org, f)
            pics = list(qs.filter(kind=ArtifactKind.PHOTO).order_by("-happened_at", "-received_at")[:ONEPAGE_PIECES])
            rest = list(qs.exclude(kind=ArtifactKind.PHOTO).order_by("-happened_at", "-received_at")
                        [:max(0, ONEPAGE_PIECES - len(pics))])
            items = _by_date(pics + rest)
            if not items:
                messages.error(request, "Nothing to put on the page yet.")
                return redirect("artifacts:stories")
            title = name.title() if name else org.name
        else:
            return HttpResponseBadRequest("template")
        s = _new_story(org, title, items, template=template)
        return redirect("artifacts:story_edit", slug=s.slug)
    everything = Artifact.objects.filter(org=org).exclude(status=ArtifactStatus.HIDDEN)
    whens = [a.when for a in everything.only("happened_at", "received_at")]
    tags = Tag.objects.filter(org=org, artifacts__isnull=False).distinct().order_by("group", "name")
    return render(request, "artifacts/stories.html", {
        "org": org, "nav": "stories", "stories": Story.objects.filter(org=org),
        "since": min(whens).date().isoformat() if whens else "", "until": max(whens).date().isoformat() if whens else "",
        "labels": [(f"{'part' if t.group == TagGroup.PART else 'tag'}:{t.name}", t.name) for t in tags],
        "any": bool(whens)})


def embed_code(s, public_url):
    """What an org pastes into its own website: the story in a frame that grows to fit it."""
    src = f"{public_url}?embed=1"
    return (f'<iframe src="{src}" title="{escape(s.title)}" style="width:100%;border:0" height="900" loading="lazy"></iframe>\n'
            "<script>addEventListener('message',function(e){var h=e.data&&e.data.storykitHeight;"
            "if(h)document.querySelectorAll('iframe').forEach(function(f){if(f.contentWindow===e.source)"
            "f.style.height=h+'px'})})</script>")


@org_required
def story_edit(request, slug):
    org = request.org
    s = get_object_or_404(Story, slug=slug, org=org)
    if request.method == "POST":
        action = request.POST.get("action", "save")
        s.title = (request.POST.get("title") or s.title).strip()[:200]
        s.statement = (request.POST.get("statement") or "").strip()
        if request.POST.get("template") in StoryTemplate.values:
            s.template = request.POST["template"]
        try:
            blocks = json.loads(request.POST.get("blocks") or "[]")
        except ValueError:
            return HttpResponseBadRequest("blocks")
        valid = set(Artifact.objects.filter(org=org).values_list("pk", flat=True))
        with transaction.atomic():
            s.blocks.all().delete()
            StoryBlock.objects.bulk_create([
                StoryBlock(story=s, order=i, artifact_id=aid, text=text, by_ai=by_ai)
                for i, (aid, text, by_ai) in enumerate(_once(
                    (b.get("artifact") if b.get("artifact") in valid else None,
                     (b.get("text") or "").strip(), bool(b.get("by_ai"))) for b in blocks))])
            if action == "publish" and request.POST.get("confirmed") == "1":
                if s.published_at is None and s.title and s.title != "Untitled story":
                    s.slug = _unique_slug(Story, f"{org.slug}-{s.title}")
                s.status = StoryStatus.PUBLISHED
                s.published_at = s.published_at or timezone.now()
                s.published = s.snapshot()
            elif action == "unpublish":
                s.status = StoryStatus.DRAFT
            s.save()
        if request.headers.get("Accept") == "application/json" and action == "save":
            return JsonResponse({"ok": True})
        if action == "ai":
            pieces = [b.artifact for b in s.blocks.select_related("artifact") if b.artifact]
            if s.collection:
                have = {a.pk for a in pieces}
                pieces += [a for a in s.collection.artifacts.all() if a.pk not in have]
            try:
                statement, drafted = ai.draft_story(pieces)
            except RuntimeError as e:
                messages.error(request, str(e))
                return redirect("artifacts:story_edit", slug=s.slug)
            with transaction.atomic():
                s.blocks.all().delete()
                StoryBlock.objects.bulk_create([StoryBlock(story=s, order=i, artifact_id=aid, text=text, by_ai=True)
                                                for i, (aid, text, _) in enumerate(_once((aid, text, True)
                                                                                         for aid, text in drafted))])
                if statement and not s.statement:
                    s.statement = statement
                    s.save(update_fields=["statement"])
            messages.success(request, "Draft written. Every AI line is marked; read and edit each one.")
        elif action == "publish" and (s.status != StoryStatus.PUBLISHED or request.POST.get("confirmed") != "1"):
            messages.error(request, "Not published. Publish asks you to confirm first.")
        elif action == "publish":
            messages.success(request, "Published. Anyone with the link can read it as it is now.")
        else:
            messages.success(request, "Saved.")
        if request.headers.get("Accept") == "application/json":
            return JsonResponse({"ok": True})
        return redirect("artifacts:story_edit", slug=s.slug)
    public_url = request.build_absolute_uri(reverse("artifacts:story", args=[s.slug]))
    in_story = {b.artifact_id for b in s.blocks.all() if b.artifact_id}
    pool = (s.collection.artifacts.all() if s.collection else Artifact.objects.filter(org=org)) \
        .exclude(status=ArtifactStatus.HIDDEN).exclude(pk__in=in_story).order_by("-received_at")[:200]
    return render(request, "artifacts/story_edit.html", {
        "org": org, "nav": "stories", "s": s, "blocks": s.blocks.select_related("artifact"), "pool": pool,
        "ai_on": ai.enabled(), "templates": StoryTemplate.choices, "public_url": public_url,
        "embed_code": embed_code(s, public_url)})


class _Shown:
    """One piece of a published story, read from the story's `published` record."""
    def __init__(self, artifact, text):
        self.artifact, self.text, self.by_ai = artifact, text, False


def _story_for(request, slug):
    """(story, is_member, blocks, draft) for a story this visitor may read. Readers get the
    story as it was last published. Its own team gets the same, or the edits not yet
    published with ?preview=1 (and always, for a story never published)."""
    s = get_object_or_404(Story.objects.select_related("org"), slug=slug)
    is_member = request.user.is_authenticated and user_orgs(request.user).filter(pk=s.org_id).exists()
    if s.status != StoryStatus.PUBLISHED and not is_member:
        raise Http404
    draft = s.status != StoryStatus.PUBLISHED or (is_member and request.GET.get("preview") == "1")
    if draft or s.published is None:  # (a story published before published versions were kept: as it is)
        blocks = list(s.blocks.select_related("artifact", "artifact__source").filter(
            Q(artifact__isnull=True) | ~Q(artifact__status=ArtifactStatus.HIDDEN)))
        return s, is_member, blocks, draft
    shown = s.published
    s.has_edits = is_member and s.has_changes
    s.title, s.statement, s.template = shown.get("title", s.title), shown.get("statement", ""), shown.get("template", "")
    ids = [b.get("artifact") for b in shown.get("blocks", []) if b.get("artifact")]
    found = Artifact.objects.filter(org=s.org, pk__in=ids).exclude(status=ArtifactStatus.HIDDEN).select_related("source").in_bulk()
    blocks = []
    for b in shown.get("blocks", []):
        a = found.get(b.get("artifact")) if b.get("artifact") else None
        if a is None and (b.get("artifact") or not b.get("text")):
            continue  # a piece since archived or deleted
        blocks.append(_Shown(a, b.get("text") or ""))
    return s, is_member, blocks, False


@xframe_options_exempt
def story(request, slug):
    """The published story: the one page people outside the org see. ?embed=1 is the
    same story without the site's frame, for an iframe on the org's own website."""
    s, is_member, blocks, draft = _story_for(request, slug)
    embed = request.GET.get("embed") == "1"
    if embed and draft:
        raise Http404  # only a published story goes on another site
    first_image = next((b.artifact for b in blocks if b.artifact and b.artifact.is_image), None)
    # Timeline: a month heading wherever the month changes; text blocks belong to the piece before them.
    month = None
    for b in blocks:
        b.month = ""
        if b.artifact:
            m = b.artifact.when.strftime("%B %Y")
            if m != month:
                b.month = month = m
    page_url = request.build_absolute_uri(reverse("artifacts:story", args=[s.slug]))
    resp = render(request, "artifacts/story.html", {
        "s": s, "blocks": blocks, "first_image": first_image, "is_member": is_member, "lt_embed": settings.LT_EMBED,
        "pieces": [b.artifact for b in blocks if b.artifact], "embed": embed, "page_url": page_url, "draft": draft,
        "og_image": request.build_absolute_uri(f"{first_image.file.url}?w=1200") if first_image else "",
        "signed_n": sum(1 for b in blocks if b.artifact and b.artifact.claim_id)})
    if not embed:
        resp["X-Frame-Options"] = "DENY"
    return resp


def story_pdf(request, slug):
    """The story as a PDF file, laid out for paper: photos, words, numbers, and where
    each piece came from. Videos and voice notes are listed with their words."""
    from .pdf import story_pdf as make
    s, is_member, blocks, draft = _story_for(request, slug)
    data = make(s, blocks, request.build_absolute_uri(reverse("artifacts:story", args=[s.slug])))
    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{s.slug}.pdf"'
    resp["Cache-Control"] = "private, no-store"
    return resp


# ---- files ---------------------------------------------------------------------
# A photo is not public because it sits in a folder. It is served to the org that owns
# it, and to anyone reading a published story that shows it. To nobody else.

INLINE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp",
                "audio/wav", "audio/mpeg", "video/mp4", "video/x-m4v", "video/webm",
                "video/quicktime", "text/plain", "application/pdf"}


def _may_see(request, path):
    """(may see it, is it public) for one stored file."""
    if request.user.is_authenticated and (
            request.user.is_superuser
            or user_orgs(request.user).filter(artifacts__file=path).exists()):
        return True, False
    # Public only while a published story shows it as published: a piece added to a
    # draft of the story stays private until Publish changes.
    live = Story.objects.filter(status=StoryStatus.PUBLISHED)
    for pk, org_id in Artifact.objects.filter(file=path, status__in=(ArtifactStatus.NEW, ArtifactStatus.KEPT)) \
            .values_list("pk", "org_id"):
        if live.filter(org_id=org_id, published__blocks__contains=[{"artifact": pk}]).exists() \
                or live.filter(org_id=org_id, published__isnull=True, blocks__artifact_id=pk).exists():
            return True, True
    return False, False


def _byte_range(request, size):
    """(start, end) of what the browser asked for, or None when it asked for the whole
    file. Raises ValueError for a range that cannot be served."""
    m = re.match(r"bytes=(\d*)-(\d*)$", request.headers.get("Range", "").strip())
    if not m:
        return None
    first, last = m.groups()
    if not first and not last:
        return None
    if not first:  # bytes=-500: the last 500 bytes
        start, end = max(0, size - int(last)), size - 1
    else:
        start = int(first)
        end = int(last) if last else size - 1
    if size <= 0 or start >= size or start > end:
        raise ValueError("range not satisfiable")
    return start, min(end, size - 1)


def _chunks(handle, length):
    """The bytes asked for, in pieces, and the file closed when done."""
    try:
        left = length
        while left > 0:
            data = handle.read(min(65536, left))
            if not data:
                break
            left -= len(data)
            yield data
    finally:
        handle.close()


THUMB_WIDTHS = (480, 1200)  # grids and lists; link previews and the PDF


def thumb_path(name, width):
    return f"thumbs/{width}/{name}.jpg"


def _thumb(path, width):
    """A small JPEG of a stored photo, made the first time it is asked for and kept.
    None when the photo cannot be read (it is then served whole)."""
    small = thumb_path(path, width)
    if default_storage.exists(small):
        return small
    try:
        from PIL import Image, ImageOps
        with default_storage.open(path, "rb") as f, Image.open(f) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((width, width * 3))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80, optimize=True)
        default_storage.save(small, ContentFile(buf.getvalue()))
        return small
    except Exception:
        return None


def media_file(request, path):
    """One stored file: the org that owns it, or anyone reading a published story that
    shows it. Anyone else is answered with nothing. ?w=480 asks for a photo's thumbnail."""
    allowed, public = _may_see(request, path)
    if not allowed:
        raise Http404
    artifact = Artifact.objects.filter(file=path).first()
    serve = path
    width = request.GET.get("w", "")
    if width.isdigit() and int(width) in THUMB_WIDTHS and artifact and artifact.kind == ArtifactKind.PHOTO:
        serve = _thumb(path, int(width)) or path
    try:
        handle = default_storage.open(serve, "rb")
    except (FileNotFoundError, SuspiciousOperation, OSError):
        raise Http404
    size = getattr(handle, "size", 0)
    try:
        wanted = _byte_range(request, size)
    except ValueError:
        resp = HttpResponse(status=416)
        resp["Content-Range"] = f"bytes */{size}"
        return resp
    if serve != path:
        ctype = "image/jpeg"
    else:
        ctype = ((artifact.content_type if artifact else "") or mimetypes.guess_type(path)[0]
                 or "application/octet-stream").split(";")[0].strip().lower()
    inline = ctype in INLINE_TYPES
    start, end = wanted if wanted else (0, max(0, size - 1))
    length = end - start + 1
    if wanted:
        handle.seek(start)
    resp = StreamingHttpResponse(_chunks(handle, length),
                                 content_type=ctype if inline else "application/octet-stream")
    resp["Content-Length"] = str(length)
    resp["Accept-Ranges"] = "bytes"
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Vary"] = "Cookie"
    if wanted:
        resp.status_code = 206
        resp["Content-Range"] = f"bytes {start}-{end}/{size}"
    if inline:
        resp["Cache-Control"] = "public, max-age=300" if public else "private, no-store"
    else:
        name = "".join(ch for ch in Path(path).name if ch.isprintable() and ch not in '"\\')
        resp["Content-Disposition"] = f'attachment; filename="{name}"'
        resp["Cache-Control"] = "private, no-store"
    return resp


# ---- install as an app (phone share sheet) -----------------------------------

def manifest(request):
    base = settings.BASE_PATH or ""
    data = {
        "name": "Story Kit", "short_name": "Story Kit", "start_url": f"{base}/send/", "scope": f"{base}/",
        "display": "standalone", "background_color": "#f4f5f1", "theme_color": "#f4f5f1",
        "icons": [{"src": f"{settings.STATIC_URL}artifacts/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": f"{settings.STATIC_URL}artifacts/icon-512.png", "sizes": "512x512", "type": "image/png"}],
        "share_target": {
            "action": f"{base}/share/", "method": "POST", "enctype": "multipart/form-data",
            "params": {"title": "title", "text": "text", "url": "url",
                       "files": [{"name": "files", "accept": ["image/*", "video/*", "audio/*", "application/pdf"]}]},
        },
    }
    return HttpResponse(json.dumps(data), content_type="application/manifest+json")


SERVICE_WORKER = """// Story Kit: present so the phone offers "Install" and the share sheet.
// It caches nothing; every request goes to the network.
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
"""


def service_worker(request):
    resp = HttpResponse(SERVICE_WORKER, content_type="application/javascript")
    resp["Service-Worker-Allowed"] = f"{settings.BASE_PATH or ''}/"
    return resp
