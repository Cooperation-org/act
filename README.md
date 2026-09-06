# act

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

## Deploy (VM 513)

- `.env` from `.env.example` (mode 600). Postgres: own DB on VM 100 (confirm name with golda
  before creating — data-boundary rule).
- `deploy/act.service`, `deploy/nginx-act.conf` are the patterns; port 8050.
- `manage.py collectstatic`, `migrate`, `seed_jreas`.
- DNS + Caddy route for act.raisethevoices.org are host-side actions.
- Register in cobox `app-registry.md` when running.

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
is an `EmailLog` row. Admin → Signatures → filter "confirmation": confirmed / drawn /
sent, waiting / email failed / no email sent; action "Resend confirmation email".

PDF: `/letters/<slug>.pdf` is the letter with every counted signature that has `on_pdf`
ticked, ordered by `pdf_sort` then date. In admin, filter (city, country, …), tick rows,
action "PDF of the letter with the selected signatures". WeasyPrint renders the same
templates; static and media files are read from disk.

## Money boundary

act never touches funds. The give rail embeds the Givebutter widget for the org's
Givebutter account (charity of record: Civic Works — receipts and discretion-and-control
disclosure are locked text in `campaigns/donation_text.py`, deliberately not DB-editable;
see projects repo `Active/raise-the-voices/legal/fundraising-compliance.md`) or a
`<simple-tip>` element. Set `Org.givebutter_account_id` + `Campaign.givebutter_campaign_id`
in admin when the Givebutter campaign exists.

## Volunteers

Volunteer logins get `is_staff` + a `VolunteerProfile(org=...)`; admin queries are scoped to
their org, delete is superuser-only, publishing (campaigns, testimonials, updates) is an
approver action. LinkedTrust SSO activates when `LINKEDTRUST_CLIENT_ID/SECRET` are set
(client registered at live.linkedtrust.us — see django-linkedtrust-auth README); Django
session login always works as the fallback.
