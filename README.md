# AiMaticX Sales Engine

An automated, daily lead-generation and sales system for your products —
**Doc2Translate, YT2Translate, AiMaticX**, and anything you add later. It runs
on autopilot from GitHub Actions (like your Google Ads project): every morning
it finds people publicly asking for what you sell, scores how ready they are to
buy, writes personalized outreach for the hottest ones, and reports everything
to a dashboard and a digest email.

**You are not required to do any selling.** Your only job is a 10-minute daily
review: approve the drafts it wrote (or turn on full email autopilot and skip
even that).

## How it works — the daily loop

```
 1. DISCOVER   Scans Reddit, Hacker News, and Google (via Serper) for people
               describing the exact problems your products solve.
 2. SCORE      Claude scores every signal 0-100 on a buying-intent rubric
               (explicit need, urgency, fit, specificity, reachability).
               Subscores are summed in code — consistent, auditable.
 3. DRAFT      For every qualified lead (score ≥ 60) it writes grounded,
               personalized outreach: a helpful forum reply or a 50-125 word
               first-touch email. Every draft quotes the lead's actual words —
               it is forbidden from inventing "personalization".
 4. SEND       Email follow-ups (3-touch sequence, days 0/3/10-style) go out
               automatically IF you enable autopilot; forum replies always
               wait for your one-click approval (that's what keeps accounts
               alive — see docs/PLAYBOOK.md).
 5. REPORT     Updates the dashboard and emails you a digest of the day's
               numbers and the hottest leads with direct links.
```

## Quick start (10 minutes, then it's hands-off)

1. **Edit `config/products.yaml`** — replace the placeholder URLs/pricing
   (marked `TODO(Dan)`) with the real ones. Add any new products.
2. **Add repository secrets** (GitHub → Settings → Secrets and variables →
   Actions → *Secrets*):
   - `ANTHROPIC_API_KEY` — required (platform.claude.com)
   - `SERPER_API_KEY` — optional, adds Google discovery (serper.dev, 2,500 free)
   - `RESEND_API_KEY` — optional, enables email sending + your daily digest
     (resend.com, 3,000 emails/month free)
3. **Add repository variables** (same page, *Variables* tab):
   - `OWNER_EMAIL` = `coachdan75@gmail.com`
   - `FROM_EMAIL` = your verified Resend sender (only if using Resend)
   - `SEND_ENABLED` = `false` to start (drafts only) — flip to `true` for
     full email autopilot once you've reviewed a week of drafts
   - `DAILY_EMAIL_CAP` = `20`
4. **Run it once**: Actions tab → *Daily Sales Engine* → Run workflow.
   After that it runs itself every morning (13:05 UTC).

## The dashboard

The engine commits `dashboard/data.json` after every run. View it:

```bash
python run_engine.py serve        # → http://localhost:8422
```

Or turn on GitHub Pages (Settings → Pages → deploy from this branch,
`/dashboard` folder) for an always-on URL.

Before the first real run you can preview it with sample data:

```bash
pip install -r requirements.txt
python run_engine.py demo
python run_engine.py serve
```

## Daily 10-minute review (draft-only mode)

```bash
python run_engine.py queue              # what's waiting
python run_engine.py show 17            # read draft #17
# forum reply → copy it, post it yourself, then:
python run_engine.py posted 17
# email draft → attach the lead's address (it sends on the next run,
# or copy it from `show` and send manually):
python run_engine.py approve 21 --email lead@example.com
python run_engine.py dismiss 9          # not a fit
python run_engine.py replied 17         # they answered! stops follow-ups
python run_engine.py suppress someone@example.com --reason unsubscribed
```

## Project layout

```
config/products.yaml      what you sell (the engine's brain food)
config/settings.yaml      thresholds, caps, sequence timing
engine/                   the pipeline (discover → score → draft → send → report)
run_engine.py             CLI for everything
dashboard/index.html      the command center (reads dashboard/data.json)
data/engine.db            SQLite memory (committed so state survives runs)
data/digest.md            today's digest (also emailed if Resend is set up)
docs/PLAYBOOK.md          the strategy: benchmarks, compliance, scaling path
.github/workflows/        the daily autopilot
```

## Safety rails (on by default)

- **Draft-first**: nothing is sent or posted until you approve it, unless you
  explicitly set `SEND_ENABLED=true` — and even then only emails, never forum
  posts, with a hard daily cap and a suppression list honored on every send.
- **Grounded outreach**: drafts must quote the lead's actual post; the prompt
  forbids invented details (hallucinated personalization is how AI sales tools
  get their senders banned — see the playbook).
- **Compliance**: every email carries an opt-out footer; `suppress` handles
  unsubscribes/bounces instantly; per-lead provenance is stored for every
  contact.

EHS is intentionally excluded from this engine per owner instruction.
