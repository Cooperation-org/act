from django import forms

from .models import Response, Testimonial

# Per-CTA extra fields. DRAFT (agent-written) — adjust labels/fields here as JREAS prefers.
# Each entry: (name, label, type, required); type = text | textarea | bool | choices:a|b|c
KIND_FIELDS = {
    "mentor": [
        ("offer", "What you can offer (English practice, code review, a skill…)", "text", True),
        ("availability", "Your availability & time zone", "text", False),
        ("languages", "Languages you speak", "text", False),
    ],
    "networking": [
        ("field", "Industry or roles you can open doors in", "text", True),
        ("intro", "Who could you introduce, or what can you offer?", "textarea", False),
    ],
    "hire": [
        ("organization", "Your organization", "text", True),
        ("role", "Role or skills you're hiring for", "text", True),
        ("work_type", "Type of work", "choices:Full-time|Part-time|Contract / freelance", False),
        ("remote_ok", "Remote work is fine", "bool", False),
    ],
    "event": [
        ("event_type", "What kind of event", "text", True),
        ("when", "Proposed date or timeframe", "text", False),
        ("audience", "Expected audience size", "text", False),
    ],
    "ama": [
        ("question", "A question you'd like answered (optional)", "textarea", False),
    ],
    "podcast": [
        ("show", "Your show or channel", "text", True),
        ("audience", "Audience size", "text", False),
        ("when", "Proposed date or timeframe", "text", False),
    ],
    "subscribe": [],
    "give": [],
}


def _build_field(label, ftype, required):
    if ftype == "textarea":
        return forms.CharField(label=label, required=required, widget=forms.Textarea(attrs={"rows": 3}))
    if ftype == "bool":
        return forms.BooleanField(label=label, required=False)
    if ftype.startswith("choices:"):
        opts = ftype.split(":", 1)[1].split("|")
        return forms.ChoiceField(label=label, required=required,
                                 choices=[(o, o) for o in opts], widget=forms.RadioSelect)
    return forms.CharField(label=label, required=required, max_length=300)


class ResponseForm(forms.ModelForm):
    """One form, shaped per CTA: base name/email plus the kind's own fields, which are
    stored in Response.details."""

    class Meta:
        model = Response
        fields = ["name", "email", "message"]
        widgets = {"message": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, cta_kind=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._extra = []
        self.fields["message"].required = False
        self.fields["message"].label = "Anything else? (optional)"
        if cta_kind == "subscribe":
            # Just who to reach — no message, name optional.
            self.fields.pop("message", None)
            self.fields["name"].required = False
        for name, label, ftype, required in KIND_FIELDS.get(cta_kind, []):
            self.fields[name] = _build_field(label, ftype, required)
            self._extra.append(name)

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.details = {n: self.cleaned_data.get(n) for n in self._extra
                       if self.cleaned_data.get(n) not in (None, "", False)}
        if commit:
            obj.save()
        return obj


class TestimonialForm(forms.ModelForm):
    class Meta:
        model = Testimonial
        fields = ["quote", "video_url", "relationship", "display_name", "show_identity"]
        widgets = {"quote": forms.Textarea(attrs={"rows": 4}), "video_url": forms.HiddenInput()}
        labels = {
            "quote": "Your testimony, in your words",
            "relationship": "How you know them (e.g. donor since June, partner org)",
            "display_name": "Name to show, if you choose to be named",
            "show_identity": "Show my name publicly (your choice; leave off to stay unnamed)",
        }
        help_texts = {
            "relationship": "",
            "display_name": "Shown only if you tick the box below.",
            "show_identity": "Names and faces tied to funding can endanger people. Leave this off unless you are sure.",
        }
