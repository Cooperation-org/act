import base64
import binascii

from django import forms
from django.core.files.base import ContentFile

from .models import Signature, SignatureField

MAX_DRAWN_BYTES = 200_000


class SignForm(forms.Form):
    first_name = forms.CharField(max_length=100, label="First name")
    last_name = forms.CharField(max_length=100, label="Last name")
    email = forms.EmailField(required=False, label="Email")
    city = forms.CharField(max_length=100, label="City, State/Province")
    country = forms.CharField(max_length=100, label="Country")
    drawn = forms.CharField(required=False, widget=forms.HiddenInput)
    keep_updated = forms.BooleanField(required=False)
    website = forms.CharField(required=False, widget=forms.HiddenInput)  # honeypot

    def __init__(self, letter, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.letter = letter
        self.fields["keep_updated"].label = letter.updates_label
        for f in letter.fields.all():
            name = f"extra_{f.key}"
            if f.kind == SignatureField.Kind.CHECKBOX:
                self.fields[name] = forms.BooleanField(required=f.required, label=f.label)
            elif f.kind == SignatureField.Kind.LONG:
                self.fields[name] = forms.CharField(required=f.required, label=f.label,
                                                    widget=forms.Textarea(attrs={"rows": 4}), max_length=2000)
            else:
                self.fields[name] = forms.CharField(required=f.required, label=f.label, max_length=200)

    def extra_fields(self):
        return [self[n] for n in self.fields if n.startswith("extra_")]

    def clean_drawn(self):
        data = self.cleaned_data["drawn"]
        if not data:
            return None
        prefix = "data:image/png;base64,"
        if not data.startswith(prefix):
            raise forms.ValidationError("The signature could not be read.")
        try:
            raw = base64.b64decode(data[len(prefix):], validate=True)
        except (binascii.Error, ValueError):
            raise forms.ValidationError("The signature could not be read.")
        if len(raw) > MAX_DRAWN_BYTES or raw[:8] != b"\x89PNG\r\n\x1a\n":
            raise forms.ValidationError("The signature could not be read.")
        return raw

    def clean(self):
        data = super().clean()
        if data.get("website"):
            raise forms.ValidationError("Could not sign.")
        if not data.get("email") and not data.get("drawn"):
            self.add_error("email", "Enter your email or draw your signature.")
        if data.get("keep_updated") and not data.get("email"):
            self.add_error("email", "Enter your email to get updates.")
        return data

    def save(self, ip=None):
        d = self.cleaned_data
        extras = {}
        for f in self.letter.fields.all():
            value = d.get(f"extra_{f.key}")
            if value not in (None, "", False):
                extras[f.key] = value
        sig = Signature(
            letter=self.letter,
            first_name=d["first_name"].strip(),
            last_name=d["last_name"].strip(),
            email=d.get("email", "").strip().lower(),
            city=d["city"].strip(),
            country=d["country"].strip(),
            extras=extras,
            keep_updated=bool(d.get("keep_updated")),
            ip=ip,
        )
        if d.get("drawn"):
            sig.drawn.save(f"{self.letter.slug}-{sig.token[:12]}.png", ContentFile(d["drawn"]), save=False)
        sig.save()
        return sig
