"""Story Kit pages: start, ways to send, send, library, collections, stories.

Who: a program or comms lead at a small org, not technical, on a phone or a laptop.
Every page answers one question for them; anything else is a link away.
"""
import json
from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from campaigns.admin import user_orgs
from campaigns.models import Org, VolunteerProfile

from . import ai, spouts
from .intake import add_file, add_reference
from .models import (Artifact, ArtifactKind, ArtifactStatus, Collection, Source, SourceKind, Story,
                     StoryBlock, StoryStatus, StoryTemplate, Tag, TagGroup)

SESSION_ORG = "storykit_org"


# ---- who and which org -------------------------------------------------------

def current_org(request):
    orgs = user_orgs(request.user)
    picked = request.session.get(SESSION_ORG)
    org = orgs.filter(pk=picked).first() if picked else None
    return org or orgs.order_by("pk").first()


def org_required(view):
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


def home(request):
    if not request.user.is_authenticated:
        return render(request, "artifacts/welcome.html")
    org = current_org(request)
    if org is None:
        return redirect("artifacts:start")
    if not Artifact.objects.filter(org=org).exists():
        return redirect("artifacts:ways")
    return redirect("artifacts:library")


def _unique_slug(model, base, field="slug", size=46):
    base = slugify(base)
    if len(base) > size:  # cut at a word, not in the middle of one
        base = base[:size].rsplit("-", 1)[0]
    base = base or "org"
    slug, n = base, 2
    while model.objects.filter(**{field: slug}).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


def start(request):
    """Name your org. That is the whole sign-up; everything else has a default."""
    if not request.user.is_authenticated:
        return redirect(f"{reverse('artifacts:signin')}?{urlencode({'next': request.path})}")
    error = ""
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        website = (request.POST.get("website") or "").strip()
        if website and not website.startswith(("http://", "https://")):
            website = f"https://{website}"
        if not name:
            error = "Please give your organization's name."
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
            return redirect("artifacts:ways")
    return render(request, "artifacts/start.html", {"error": error, "orgs": user_orgs(request.user)})


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


def _store_uploads(request, org, files, words, when=None):
    source = _source(org, SourceKind.UPLOAD) or Source.objects.create(org=org, kind=SourceKind.UPLOAD,
                                                                      label="Phone and computer")
    who = request.user.email or request.user.get_username()
    new = 0
    for f in files:
        _, created = add_file(org, data=f.read(), filename=f.name, content_type=f.content_type or "",
                              source=source, words=words, sent_by=who, added_by=request.user,
                              happened_at=when)
        new += created
    return new


@org_required
@never_cache
def send(request):
    org = request.org
    if request.method == "POST":
        files = _files_from(request)
        words = (request.POST.get("words") or "").strip()
        day = parse_date(request.POST.get("happened_on") or "")
        when = timezone.make_aware(timezone.datetime(day.year, day.month, day.day, 12)) if day else None
        if not files and not words:
            messages.error(request, "Choose a photo, video or file, or write something.")
            return redirect("artifacts:send")
        if files:
            new = _store_uploads(request, org, files, words, when)
            same = len(files) - new
            messages.success(request, f"Saved {new} {'item' if new == 1 else 'items'}."
                             + (f" {same} {'was' if same == 1 else 'were'} already here." if same else ""))
        else:
            source = _source(org, SourceKind.UPLOAD)
            add_file(org, data=words.encode(), filename="note.txt", content_type="text/plain", source=source,
                     words=words, sent_by=request.user.email or request.user.get_username(),
                     added_by=request.user, happened_at=when)
            messages.success(request, "Saved your note.")
        return redirect("artifacts:send")
    return render(request, "artifacts/send.html", {"org": org, "nav": "send", "today": timezone.localdate().isoformat()})


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
        new = _store_uploads(request, org, files, words)
        messages.success(request, f"Saved {new} shared {'item' if new == 1 else 'items'}.")
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
def add_drive(request):
    org = request.org
    url = (request.POST.get("folder") or "").strip()
    folder = spouts.drive_folder_id(url)
    if not folder:
        messages.error(request, "Paste the folder's share link from Google Drive (it contains /folders/).")
        return redirect("artifacts:ways")
    source, _ = Source.objects.get_or_create(org=org, kind=SourceKind.DRIVE, config__folder_id=folder,
                                             defaults={"label": "Google Drive folder",
                                                       "config": {"folder_id": folder, "folder_url": url}})
    spouts.check_in_background(source)
    messages.info(request, "Copying the folder now. New items show up in What we have as they arrive.")
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

FILTER_KEYS = ("kind", "tag", "part", "source", "person", "q", "since", "until", "show", "sort")
MULTI_KEYS = ("tag", "part")
SORTS = {"": ("-received_at",), "happened": ("-happened_at", "-received_at"),
         "oldest": ("happened_at", "received_at"), "title": ("title", "filename")}


def filtered(org, f):
    qs = Artifact.objects.filter(org=org).select_related("source").prefetch_related("tags")
    if f.get("show") != "hidden":
        qs = qs.exclude(status=ArtifactStatus.HIDDEN)
    else:
        qs = qs.filter(status=ArtifactStatus.HIDDEN)
    if f.get("kind"):
        qs = qs.filter(kind=f["kind"])
    for key in MULTI_KEYS:  # every chosen label must be on the item
        names = f.get(key) or []
        for name in ([names] if isinstance(names, str) else names):
            qs = qs.filter(pk__in=Artifact.objects.filter(org=org, tags__name=name).values("pk"))
    if f.get("source"):
        qs = qs.filter(source__kind=f["source"])
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
            messages.success(request, f"Archived {len(ids)}. More filters → Archived shows them.")
        elif action == "unhide":
            picked.update(status=ArtifactStatus.KEPT)
            messages.success(request, f"Brought back {len(ids)}.")
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
    return render(request, "artifacts/library.html", {
        "org": org, "nav": "library", "items": qs[:300], "total": qs.count(), "f": f, "filter_json": json.dumps(f),
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
    org = request.org
    a = get_object_or_404(Artifact, pk=pk, org=org)
    if request.method == "POST":
        a.title = (request.POST.get("title") or "").strip()[:300]
        a.about = (request.POST.get("about") or "").strip()[:300]
        a.consent = request.POST.get("consent") == "on"
        if a.status == ArtifactStatus.NEW:
            a.status = ArtifactStatus.KEPT
        a.save()
        a.tags.set(_tags_from(org, request.POST.get("parts"), TagGroup.PART)
                   + _tags_from(org, request.POST.get("tags")))
        messages.success(request, "Saved.")
        return redirect("artifacts:artifact", pk=a.pk)
    return render(request, "artifacts/artifact.html", {
        "org": org, "nav": "library", "a": a,
        "part_text": ", ".join(t.name for t in a.tags.all() if t.group == TagGroup.PART),
        "tag_text": ", ".join(t.name for t in a.tags.all() if t.group != TagGroup.PART),
        "ai_labels": {x.get("label") for x in a.ai_labels if x.get("by") == "ai"},
        "lt_embed": settings.LT_EMBED})


@org_required
def labels(request):
    """Every label, with how many items carry it: rename, merge, move between
    'part of our org' and plain label, or delete."""
    org = request.org
    if request.method == "POST":
        t = get_object_or_404(Tag, pk=request.POST.get("pk"), org=org)
        action = request.POST.get("action")
        if action == "delete":
            t.delete()
            messages.success(request, f"Took the label “{t.name}” off everything.")
        elif action == "save":
            t.group = TagGroup.PART if request.POST.get("group") == TagGroup.PART else TagGroup.LABEL
            name = (request.POST.get("name") or "").strip().lower()[:60] or t.name
            same = Tag.objects.filter(org=org, name=name).exclude(pk=t.pk).first()
            if same:  # renamed onto an existing label: merge into it
                for a in t.artifacts.all():
                    a.tags.add(same)
                t.delete()
                messages.success(request, f"Merged into “{same.name}”.")
            else:
                t.name = name
                t.save()
                messages.success(request, "Saved.")
        return redirect("artifacts:labels")
    tags = Tag.objects.filter(org=org).annotate(n=Count("artifacts", distinct=True)).order_by("group", "name")
    return render(request, "artifacts/labels.html", {
        "org": org, "nav": "labels", "tags": tags, "PART": TagGroup.PART})


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


# ---- collections -------------------------------------------------------------

@org_required
def collections(request):
    return render(request, "artifacts/collections.html", {
        "org": request.org, "nav": "collections", "collections": Collection.objects.filter(org=request.org)})


@org_required
def collection(request, pk):
    org = request.org
    c = get_object_or_404(Collection, pk=pk, org=org)
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


def _by_date(qs):
    return sorted(qs, key=lambda a: a.when)


@org_required
def stories(request):
    """The org's stories, and two ways to start one: a timeline over dates you pick,
    or a one-page summary. Either can be narrowed to one label."""
    org = request.org
    if request.method == "POST":
        template = request.POST.get("template")
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
                StoryBlock(story=s, order=i, artifact_id=b.get("artifact") if b.get("artifact") in valid else None,
                           text=(b.get("text") or "").strip(), by_ai=bool(b.get("by_ai")))
                for i, b in enumerate(blocks) if b.get("artifact") in valid or (b.get("text") or "").strip()])
            if action == "publish":
                s.status = StoryStatus.PUBLISHED
                s.published_at = s.published_at or timezone.now()
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
                                                for i, (aid, text) in enumerate(drafted)])
                if statement and not s.statement:
                    s.statement = statement
                    s.save(update_fields=["statement"])
            messages.success(request, "Draft written. Every AI line is marked; read and edit each one.")
        elif action == "publish":
            messages.success(request, "Published.")
        else:
            messages.success(request, "Saved.")
        if request.headers.get("Accept") == "application/json":
            return JsonResponse({"ok": True})
        return redirect("artifacts:story_edit", slug=s.slug)
    in_story = {b.artifact_id for b in s.blocks.all() if b.artifact_id}
    pool = (s.collection.artifacts.all() if s.collection else Artifact.objects.filter(org=org)) \
        .exclude(status=ArtifactStatus.HIDDEN).exclude(pk__in=in_story).order_by("-received_at")[:200]
    return render(request, "artifacts/story_edit.html", {
        "org": org, "nav": "stories", "s": s, "blocks": s.blocks.select_related("artifact"), "pool": pool,
        "ai_on": ai.enabled(), "templates": StoryTemplate.choices, "public_url": request.build_absolute_uri(reverse("artifacts:story", args=[s.slug]))})


def story(request, slug):
    """The published story: the one page people outside the org see."""
    s = get_object_or_404(Story.objects.select_related("org"), slug=slug)
    is_member = request.user.is_authenticated and user_orgs(request.user).filter(pk=s.org_id).exists()
    if s.status != StoryStatus.PUBLISHED and not is_member:
        raise Http404
    blocks = list(s.blocks.select_related("artifact").filter(Q(artifact__isnull=True)
                                                             | ~Q(artifact__status=ArtifactStatus.HIDDEN)))
    first_image = next((b.artifact for b in blocks if b.artifact and b.artifact.is_image), None)
    # Timeline: a month heading wherever the month changes; text blocks belong to the piece before them.
    month = None
    for b in blocks:
        b.month = ""
        if b.artifact:
            m = b.artifact.when.strftime("%B %Y")
            if m != month:
                b.month = month = m
    return render(request, "artifacts/story.html", {
        "s": s, "blocks": blocks, "first_image": first_image, "is_member": is_member, "lt_embed": settings.LT_EMBED})


# ---- install as an app (phone share sheet) -----------------------------------

def manifest(request):
    base = settings.BASE_PATH or ""
    data = {
        "name": "Story Kit", "short_name": "Story Kit", "start_url": f"{base}/send/", "scope": f"{base}/",
        "display": "standalone", "background_color": "#ffffff", "theme_color": "#00718f",
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
