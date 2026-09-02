# Nexus Health Insurance — AI Platform Hub

An informational hub for the ten department chatbots, the central LLM
Gateway, and Keycloak realm — **not** a chatbot itself. No auth, no
database. It exists to answer "what is this thing, how is it built,
is it HIPAA compliant, and what should I make a video about" in one
place, with a navbar linking out to the real chatbots.

## What's here

- **Home** — a directory of all 10 department chatbots with links out
  to their own ports, plus a shared-infrastructure summary.
- **Architecture** — an HTML/CSS system diagram (no external diagram
  dependency — pure Bootstrap boxes/arrows, so it always renders),
  the auth/token-relay flow, the LangGraph pipeline shape, the HITL
  commit pattern (including the Adjudication exception), and the
  database design.
- **HIPAA Compliance** — an honest audit of this platform's own
  architecture against the Security Rule (what's solid, what isn't),
  three concrete approaches (managed API + BAA / self-hosted Ollama /
  hybrid minimization), and a real, sourced GPU pricing table with
  monthly cost scenarios for self-hosting.
- **Training** — a dropdown of 12 candidate YouTube video topics, each
  built from something that actually happened during this platform's
  build (a real bug, a real design trade-off), not a generic tutorial
  outline. Each has a target length, audience, outline, and which
  files are worth showing on screen.

## Single source of truth

Everything data-driven — the chatbot directory, the training topics,
the GPU pricing table — lives in `app/content.py` as plain Python
data structures, not scattered across templates. The navbar's
Chatbots and Training dropdowns render FROM that same data via a
Flask `context_processor`, so there's no way for the navbar and the
page content to drift out of sync. Add a chatbot or a training topic
in one place; it shows up everywhere it needs to.

## Running locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python wsgi.py     # dev server on :5000
```

Port 5000 was deliberately left free by all ten department chatbots
(they run 5001–5010) specifically so this hub could live here without
a collision.

The "Open chatbot" links point to `http://{HUB_HOST}:{port}/` for each
department — they don't proxy or embed the chatbots, they just open
them in a new tab. If a given chatbot isn't currently running, that
link will fail to connect, same as opening its URL directly would.

## Adding a new chatbot to the directory

Add an entry to `CHATBOTS` in `app/content.py`:

```python
{"dept_code": "NEWDEPT", "name": "New Department", "port": 5011,
 "description": "One sentence on what it does.",
 "icon": "bi-some-bootstrap-icon"},
```

It will appear in the navbar dropdown and the home page grid
automatically — no template changes needed.

## Adding a training topic

Add an entry to `TRAINING_TOPICS` in `app/content.py` with a unique
`slug`. It will appear in the Training navbar dropdown, the
`/training` index (grouped by `category`), and get its own detail
page at `/training/<slug>` automatically.

## Tests

This app is intentionally simple enough (static content, no auth, no
DB) that a full pytest suite would mostly be testing Flask and Jinja
themselves. What was actually verified before delivery:

- Every route returns 200: `/`, `/architecture`, `/hipaa-compliance`,
  `/training`, and all 12 `/training/<slug>` detail pages
- The 404 case for an unknown training slug actually returns 404
- The navbar's Chatbots dropdown renders all 10 department links with
  the correct ports (checked against `app/content.py`, not
  hand-counted)
- The GPU pricing table renders all 5 rows from `GPU_PRICING`
- No leaked Jinja/template errors on any page (grepped the full
  rendered HTML of every page for `undefined`/`traceback`/etc.)
- The monthly self-hosting cost figures were computed in Python, not
  estimated by hand, and cross-checked against the source pricing
