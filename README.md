# act

> ## STOP — read before touching any database
>
> **NEVER `loaddata`, restore a dump, or copy a SQLite file into a deployed act database.**
>
> The `act` database on VM 100 holds the LIVE open letters and their signatures
> (cooperation.org/letters). Every deployment's rows carry their own primary keys, so a dump
> from another deployment **overwrites live rows by id**. A dump of the Jreas demo was created
> and destroyed on 2026-09-15 for exactly this reason (golda: "the letters are VERY IMPORTANT").
>
> Content moves by re-importing from source or by typing it in /admin. **Every deployment gets
> its OWN database** (golda, 2026-09-15). The voluntask/Raise the Voices deployment does NOT
> share `act`. Never create a database without asking golda for the name.
>
> Backup of the live letters rows: golda's `~/work/9-15-2026-act-letters-backup.sql` on VM 200.

Story + calls-to-action app for volunteer orgs. Campaign pages where money is ONE way to help:
give (wrapped Givebutter widget, or SimpleTip), mentor, hire, host an event, AMA, subscribe.
Testimonials from named-or-anonymous vouchers (LinkedClaim URI per testimony, publish-gated),
per-person share links with counted attribution, Instagram story/post card downloads.

First deployment target: VM 513 (voluntask), domain act.raisethevoices.org, for the
Raise the Voices JREAS Hub campaign. Design + phased plan: projects repo
`Active/raise-the-voices/act-design.md`; VM handoff: `dashboard-vm513-handoff.md`.

## Run (dev)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_jreas        # RTV org + JREAS campaign (draft)
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver
```

Publish the campaign in /admin (Campaigns → jreas → status: published).

## Deploy

Live: **cooperation.org/letters/** on the civic-actions VM (10.0.0.154) since 2026-09-07; only
`/letters/`, `/admin/`, `/static/`, `/media/` are routed there. Later: act.raisethevoices.org
(VM 513) for the campaign pages.

- `.env` from `.env.example` (mode 600). Postgres: database `act` on VM 100 (roles `act_owner` for
  migrations, `act_user` for the running app). `ACT_BASE_PATH`/`SCRIPT_NAME` empty at a domain root.
- Python 3.12 venv, `pip install -r requirements.txt`. WeasyPrint needs `libpango-1.0-0
  libpangoft2-1.0-0 libharfbuzz-subset0`.
- `manage.py migrate --check` (run `migrate` with the `act_owner` credentials if pending),
  `collectstatic`. `media/` must be writable by the service user (`act`).
- `deploy/act-retry-mail.service` + `.timer`: confirmation-mail retries (`systemctl enable --now act-retry-mail.timer`).
- `deploy/act.service` (gunicorn 127.0.0.1:8050) and `deploy/nginx-cooperation.org.conf` (locations
  added inside the existing cooperation.org vhost) are what is live. `deploy/nginx-act.conf` is the
  own-domain variant for act.raisethevoices.org.
- Deploy a change: `git pull`, `.venv/bin/pip install -r requirements.txt`, `collectstatic --clear`
  (hashed filenames; nginx caches `/static/` for 7 days), tests with `ACT_DEBUG=1 manage.py test`,
  `migrate --check`, `sudo systemctl restart act`.
- DNS + Caddy route are host-side actions. Register in cobox `app-registry.md`.

## Deployments

One repo and one database schema, several deployments. `ACT_APPS` in `.env` picks the apps a
deployment routes and shows in admin: `campaigns` (story + calls to action, owns `/`),
`letters` (open letters and petitions at `/letters/`). Org and volunteer accounts are shared
and always visible.

| Site | ACT_APPS | ACT_SITE_ORG | Public paths |
|------|----------|--------------|--------------|
| cooperation.org | `letters` |, | `/letters/`, `/admin/` (root forwards to `/letters/`) |
| raisethevoices.org | `campaigns,blog,people` |, (platform: every org's published campaigns) | `/`, `/c/<slug>/`, `/updates/`, `/blog/`, `/people/`, `/admin/` |
| Jreas Coop (domain TBD) | `campaigns,blog,people` | `jreas-coop` | `/` (org home), `/c/jreas-coop/`, `/updates/`, `/blog/`, `/people/`, `/governance/`, `/admin/` |

New capabilities (content management, content planning, email campaigns) arrive as further
apps in this list.

## Single-org site (`ACT_SITE_ORG`)

Set `ACT_SITE_ORG=<org slug>` and the deployment *is* that org: the wordmark and footer are
the org's name, home is `campaigns/org_home.html` (tagline, `about` in Markdown, its campaigns,
the give rail of its first published campaign with the locked Civic Works disclosure, Latest,
People, Governance), and campaigns, posts and people of other orgs 404. Every text field is the
org's own words, edited in /admin → Orgs (superuser). A slug with no Org row raises
`ImproperlyConfigured` on `/` rather than silently falling back.

- `/updates/` and `/updates/feed/` (RSS): published campaign updates (receipts, counts) and blog
  posts as one stream, newest first. Home shows the latest six.
- `/blog/`, `/blog/<slug>/`, `/blog/feed/`, `blog` app. Post: Markdown body, optional author
  (a Person), summary for lists and feed, publish gate (`published_at` set on publish).
- `/people/`, `/people/<slug>/`, `people` app. Person: name, role, photo, Markdown bio, links,
  optional login. **Publishes only with `consent_on_record` ticked and an approver's publish** , 
  a name tied to funding can endanger someone; the admin action and the save hook both refuse
  otherwise. A post's author link appears only while the author's profile is published.
- `/governance/`, shown (and linked in nav) only when the Org has `governance_text` (Markdown,
  the members' own words) or `governance_url`. Governance itself is not modelled here: earned
  governance (reviewed work → voting weight, pie, votes, sortition) is GovKit
  (Cooperation-org/govkit, dash.workers.vc); `governance_url` points at the org's page there.

`seed_jreas` creates org `jreas-coop` ("Jreas Coop") and the JREAS Hub campaign under it, as draft.

## Letters (open letters and petitions)

`letters/` app, pages under `/letters/<slug>/`. A Letter belongs to an Org, is written in
Markdown (bold, italics, paragraphs, bullets), and is `draft` until published in /admin.
`kind` is open letter (counter only) or petition (addressee + goal). Theme: parchment,
EB Garamond (bundled, OFL).

Signing: first name, last name, city, country, plus the letter's own fields
(Letters → Signature fields: label, kind short / paragraph / checkbox, required, public).
A signature counts when the signer has **either** confirmed their email **or** drawn a
signature (signature_pad, stored as PNG). Email is required to get updates
(`keep_updated`). Same email twice: an unconfirmed one is replaced, a confirmed one is
refused.

Confirmation mail goes out over SMTP (`EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`,
`DEFAULT_FROM_EMAIL`). With `EMAIL_HOST` unset nothing is sent; every attempt, sent or failed,
is an `EmailLog` row. A failed send is retried automatically 1 h, 6 h and 24 h later
(`letters.models.RETRY_DELAYS`; `manage.py retry_confirmations` on `deploy/act-retry-mail.timer`,
every 15 min), then it gives up. The schedule and last error live on the Signature. Admin →
Signatures → filter "confirmation": confirmed / drawn / approved / sent, waiting / failed, retry
scheduled / failed, gave up / no email sent; action "Resend confirmation email" starts over.
**Approved** (checkbox in the list) makes a signature count and show even if the email never
confirmed, for the friend who signed but never got the mail.

PDF: `/letters/<slug>.pdf` is the letter with every counted signature that has `on_pdf`
ticked, ordered by `pdf_sort` then date. In admin, filter (city, country, …), tick rows,
action "PDF of the letter with the selected signatures". WeasyPrint renders the same
templates; static and media files are read from disk.

## Testimonials are LinkedClaims

Same mechanism as workers.vc (`doorway/claims.py`) and `LinkedClaims/docs/embedding.md`:

1. `/c/<slug>/testimony/` embeds `<linked-video-recorder>` from `LT_EMBED`; the browser uploads the
   video to LinkedTrust storage and the returned `videoUrl` lands in the form's hidden field.
2. The testimonial sits **pending**. In admin an approver runs "Sign as LinkedClaim and publish":
   `campaigns/linkedtrust.py` POSTs `{subject: campaign page, claim: ENDORSES, statement, videoUrl,
   name only if opted in}` to `LT_API/api/claims` with `x-lt-client-id/secret`, stores `claim_id`,
   `linkedclaim_uri`, `signed_at`, or `sign_error`.
3. The campaign page renders each signed testimonial as `<linked-badge claim-id=…>` (badge.js from
   `LT_EMBED`); unsigned ones render as plain cards marked "not yet signed".

`LT_CLIENT_ID`/`LT_CLIENT_SECRET` are issued per site by whoever runs live.linkedtrust.us; without
them signing is refused (an issuer-less claim is permanent damage), publishing still works.

## Money boundary

act never touches funds. The give rail embeds the Givebutter widget for the org's
Givebutter account (charity of record: Civic Works, receipts and discretion-and-control
disclosure are locked text in `campaigns/donation_text.py`, deliberately not DB-editable;
see projects repo `Active/raise-the-voices/legal/fundraising-compliance.md`) or a
`<simple-tip>` element. Set `Org.givebutter_account_id` + `Campaign.givebutter_campaign_id`
in admin when the Givebutter campaign exists.

## Volunteers

Volunteer logins get `is_staff` + one `VolunteerProfile(org=...)` per org they work with (the
same people volunteer on letters and on fundraisers); admin queries are scoped to those orgs,
Org pickers offer only them, delete is superuser-only, and publishing (campaigns, testimonials,
updates, posts, people) is an approver action **per org**, an approver for org A cannot publish
org B's rows even when they can see them. LinkedTrust SSO activates when `LINKEDTRUST_CLIENT_ID/SECRET` are set
(client registered at live.linkedtrust.us, see django-linkedtrust-auth README); Django
session login always works as the fallback.
