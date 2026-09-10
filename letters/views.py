from django.views.decorators.cache import never_cache
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import SignForm
from .mail import confirm_url, send_confirmation
from .markup import render as render_markdown
from .models import Letter, Signature
from .pdf import letter_pdf


def _letter_or_404(slug, request):
    letter = get_object_or_404(Letter.objects.select_related("org"), slug=slug)
    if not letter.is_published and not request.user.is_staff:
        raise Http404
    return letter


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")) or None


@never_cache
def letter(request, slug):
    letter = _letter_or_404(slug, request)
    if request.method == "POST":
        form = SignForm(letter, request.POST)
        if form.is_valid():
            email = form.cleaned_data.get("email", "").strip().lower()
            existing = letter.signatures.filter(email=email).first() if email else None
            if existing and existing.is_valid:
                return render(request, "letters/signed.html", {"letter": letter, "s": existing, "already": True})
            if existing:
                existing.delete()
            sig = form.save(ip=_client_ip(request))
            log = None
            if sig.email:
                log = send_confirmation(sig, confirm_url(sig, request))
            return render(request, "letters/signed.html", {"letter": letter, "s": sig, "mail_failed": bool(log and log.error)})
    else:
        form = SignForm(letter)
    return render(request, "letters/letter.html", {
        "letter": letter,
        "body_html": render_markdown(letter.body),
        "count": letter.count(),
        "signatures": letter.valid_signatures().select_related("letter").order_by("-created") if letter.show_signatures else [],
        "form": form,
    })


def confirm(request, slug, token):
    letter = _letter_or_404(slug, request)
    sig = get_object_or_404(Signature, letter=letter, token=token)
    if not sig.confirmed_at:
        sig.confirmed_at = timezone.now()
        sig.save(update_fields=["confirmed_at"])
    return render(request, "letters/confirmed.html", {"letter": letter, "s": sig})


def pdf(request, slug):
    letter = _letter_or_404(slug, request)
    data = letter_pdf(letter, letter.signatures.for_pdf(), request.build_absolute_uri("/"))
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{letter.slug}.pdf"'
    return response


def index(request):
    letters = Letter.objects.filter(status="published").select_related("org")
    if letters.count() == 1:
        return redirect("letters:letter", slug=letters.first().slug)
    return render(request, "letters/index.html", {"letters": letters})
