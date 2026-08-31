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
        fields = ["quote", "video", "relationship", "display_name", "show_identity"]
        widgets = {"quote": forms.Textarea(attrs={"rows": 4})}
        labels = {
            "show_identity": "Show my name publicly (your choice — leave off to stay unnamed)",
        }
