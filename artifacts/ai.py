"""AI helper: from a set of artifacts to a first draft of a story.

It only drafts. Every block it writes is marked by_ai until a person edits it,
and it may only use what the artifacts say: the senders' own words, dates, who
sent them. Off unless ANTHROPIC_API_KEY is set.
"""
import json
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

API = "https://api.anthropic.com/v1/messages"

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
    return bool(settings.ANTHROPIC_API_KEY)


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
    r = requests.post(API, timeout=120, headers={
        "x-api-key": settings.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
        "content-type": "application/json"}, json={
        "model": settings.ACT_AI_MODEL, "max_tokens": 2000,
        "messages": [{"role": "user", "content": PROMPT.format(pieces=describe(artifacts))}]})
    if r.status_code != 200:
        logger.error("ai draft: %s %s", r.status_code, r.text[:300])
        raise RuntimeError("The AI helper did not answer. Try again in a minute.")
    text = "".join(c.get("text", "") for c in r.json().get("content", []) if c.get("type") == "text")
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
