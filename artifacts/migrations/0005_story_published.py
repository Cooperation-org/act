from django.db import migrations, models


def snapshot_published(apps, schema_editor):
    """Stories already published keep showing what they show now."""
    Story = apps.get_model("artifacts", "Story")
    for s in Story.objects.filter(status="published"):
        s.published = {"title": s.title, "statement": s.statement, "template": s.template,
                       "blocks": [{"artifact": b.artifact_id, "text": b.text}
                                  for b in s.blocks.order_by("order", "pk")]}
        s.save(update_fields=["published"])


class Migration(migrations.Migration):

    dependencies = [
        ("artifacts", "0004_demo_copy"),
    ]

    operations = [
        migrations.AddField(
            model_name="story",
            name="published",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.RunPython(snapshot_published, migrations.RunPython.noop),
    ]
