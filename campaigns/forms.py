from django import forms

from .models import Response, Testimonial


class ResponseForm(forms.ModelForm):
    class Meta:
        model = Response
        fields = ["name", "email", "message"]
        widgets = {"message": forms.Textarea(attrs={"rows": 3})}


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
