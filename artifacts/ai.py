"""AI helper: from a set of artifacts to a first draft of a story.

Providers are tried in order: Anthropic (reads photos), then MiniMax M3 through its
Anthropic-compatible API (text only). Whichever answers first is used.

It only drafts. Every block it writes is marked by_ai until a person edits it,
and it may only use what the artifacts say: the senders' own words, dates, who
sent them. Off unless ANTHROPIC_API_KEY or MINIMAX_API_KEY is set.
"""
import json
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)



def providers():
    """(name, messages URL, key, model, reads images), in the order to try them."""
    out = []
    if settings.ANTHROPIC_API_KEY:
        out.append(("anthropic", "https://api.anthropic.com/v1/messages", settings.ANTHROPIC_API_KEY,
                    settings.ACT_AI_MODEL, True))
    if settings.MINIMAX_API_KEY:
        out.append(("minimax", "https://api.minimax.io/anthropic/v1/messages", settings.MINIMAX_API_KEY,
                    settings.ACT_MINIMAX_MODEL, False))
    return out


def ask(build_content, max_tokens=2000):
    """Send one user message to the first provider that answers; returns its text.
    build_content(reads_images) gives the message content for that provider."""
    for name, url, key, model, vision in providers():
        try:
            r = requests.post(url, timeout=180, headers={
                "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": model, "max_tokens": max_tokens,
                      "messages": [{"role": "user", "content": build_content(vision)}]})
        except requests.RequestException as e:
            logger.error("ai %s: %s", name, e)
            continue
        if r.status_code != 200:
            logger.error("ai %s: %s %s", name, r.status_code, r.text[:300])
            continue
        return "".join(c.get("text", "") for c in r.json().get("content", []) if c.get("type") == "text")
    raise RuntimeError("The AI helper did not answer. Try again in a minute.")

PROMPT = """You help a small organization tell a story from material its people sent in.
Below are the pieces, each with an id, its kind, when it happened, who sent it, and their own words.

Write a short story draft that uses ONLY facts stated in these pieces. Do not invent names,
numbers, places, quotes or outcomes. Quote people's words exactly when you quote. Plain,
warm, concrete language; no marketing words.

Return JSON only, no prose around it:
{{"statement": "<one sentence: what these pieces together show>",
  "blocks": [{{"artifact": <id or null>, "text": "<1-3 sentences for this piece, or a short bridge>"}}]}}
Order the blocks the way the story should read. Use each artifact at most once; leave out
pieces that do not fit.

Pieces:
{pieces}
"""


def enabled():
    return bool(providers())


def describe(artifacts):
    lines = []
    for a in artifacts:
        lines.append(json.dumps({
            "id": a.pk, "kind": a.kind, "when": a.when.date().isoformat() if a.when else None,
            "sent_by": a.sent_by, "about": a.about, "title": a.title,
            "tags": [t.name for t in a.tags.all()], "words": a.words[:1500],
        }, ensure_ascii=False))
    return "\n".join(lines)


def draft_story(artifacts):
    """Returns (statement, [(artifact_id|None, text)]). Raises RuntimeError on failure."""
    if not enabled():
        raise RuntimeError("The AI helper is not switched on.")
    text = ask(lambda vision: PROMPT.format(pieces=describe(artifacts)))
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        raise RuntimeError("The AI helper's answer could not be read. Try again.")
    ids = {a.pk for a in artifacts}
    blocks = []
    for b in data.get("blocks", []):
        aid = b.get("artifact")
        blocks.append((aid if aid in ids else None, (b.get("text") or "").strip()))
    return (data.get("statement") or "").strip(), blocks


# ---- sorting: labels and a title for each artifact -----------------------------

SORT_PROMPT = """You help {org} sort the raw material its people collected: photos, videos,
voice notes, files, links. For each piece below, pick labels so the team can find and
group things later.

Two kinds of label:
- "parts": which part of {org} this is from (a chapter, city, site, program, cohort or
  partner organization). Only when the piece itself says or shows it. Prefer these
  existing ones: {parts}
- "labels": what it is about (an event, a topic, a kind of moment). 1 to 4, short, lowercase.
  Reuse these existing ones when they fit: {labels}

Also a short plain "title" (under 8 words) saying what the piece is, from what it shows or
says. Never invent names of people, places or numbers that are not in the piece.

Return JSON only: {{"items": [{{"id": <id>, "title": "...", "parts": [...], "labels": [...]}}]}}

Pieces (images follow in the same order where a piece is a photo):
{pieces}
"""

SORT_BATCH = 8


def _image_block(a):
    """A small JPEG of a photo for the model to look at, or None."""
    import base64
    import io
    try:
        from PIL import Image
        with a.file.open("rb") as fh, Image.open(fh) as im:
            im = im.convert("RGB")
            im.thumbnail((768, 768))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80)
    except Exception:
        return None
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                       "data": base64.b64encode(buf.getvalue()).decode()}}


def suggest_labels(org, artifacts, parts, labels):
    """One call for up to SORT_BATCH artifacts. Returns {id: {"title", "parts", "labels"}}."""
    if not enabled():
        raise RuntimeError("The AI helper is not switched on.")
    def content(vision):
        out = [{"type": "text", "text": SORT_PROMPT.format(
            org=org.name, parts=", ".join(parts) or "(none yet)", labels=", ".join(labels) or "(none yet)",
            pieces=describe(artifacts))}]
        for a in artifacts:
            img = _image_block(a) if vision and a.is_image else None
            if img:
                out += [{"type": "text", "text": f"Photo id {a.pk}:"}, img]
        return out
    text = ask(content)
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        raise RuntimeError("The AI helper's answer could not be read. Try again.")
    ids = {a.pk for a in artifacts}
    return {i["id"]: i for i in data.get("items", []) if isinstance(i, dict) and i.get("id") in ids}


def sort_artifacts(org, pks):
    """Label each artifact, a batch at a time, saving each batch as soon as it is back.
    Labels the AI added are recorded in ai_labels so the page can show they came from it.
    An empty title is filled; a title someone wrote is kept."""
    from .models import Artifact, Tag, TagGroup
    pks = list(pks)
    done = 0
    for i in range(0, len(pks), SORT_BATCH):
        batch = list(Artifact.objects.filter(org=org, pk__in=pks[i:i + SORT_BATCH]).prefetch_related("tags"))
        tags = Tag.objects.filter(org=org)
        parts = [t.name for t in tags if t.group == TagGroup.PART]
        labels = [t.name for t in tags if t.group != TagGroup.PART]
        got = suggest_labels(org, batch, parts, labels)
        for a in batch:
            s = got.get(a.pk)
            if not s:
                continue
            added = []
            for group, names in ((TagGroup.PART, s.get("parts") or []), (TagGroup.LABEL, s.get("labels") or [])):
                for n in names:
                    n = str(n).strip().lower()[:60]
                    if not n:
                        continue
                    t, _ = Tag.objects.get_or_create(org=org, name=n, defaults={"group": group})
                    a.tags.add(t)
                    added.append({"label": n, "by": "ai", "confirmed": False})
            have = {x.get("label") for x in a.ai_labels}
            a.ai_labels = a.ai_labels + [x for x in added if x["label"] not in have]
            if not a.title and s.get("title"):
                a.title = str(s["title"]).strip()[:300]
            a.save(update_fields=["ai_labels", "title"])
            done += 1
    return done


def sort_in_background(org, pks):
    import threading

    def run():
        from django.db import connection
        try:
            sort_artifacts(org, pks)
        except Exception:
            logger.exception("ai sort for org %s failed", org.pk)
        finally:
            connection.close()
    threading.Thread(target=run, daemon=True).start()
