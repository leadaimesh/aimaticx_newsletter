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
 4. SEND       Three email modes (EMAIL_PROVIDER): "gmail" sends from your
               own Gmail per project AND watches those inboxes; "resend"
               sends via API; "instantly" hands leads to an Instantly
               campaign at scale. All fire only when you enable autopilot.
               Forum replies always wait for your one-click approval
               (that's what keeps accounts alive — see docs/PLAYBOOK.md).
 4b. INBOX     In gmail mode the engine reads each account's NEW mail
               (read-only IMAP, never touches read/unread state): a reply
               cancels remaining follow-ups and counts on the dashboard, a
               bounce or "not interested" suppresses the address forever.
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
   - `GMAIL_ADDRESS` + `GMAIL_APP_PASSWORD` — recommended: sends outreach and
     your digest from your own Gmail AND auto-detects replies/bounces. On the
     Google account: enable 2-Step Verification, then create an app password
     at myaccount.google.com/apppasswords. Add
     `GMAIL_ADDRESS_DOC2TRANSLATE` etc. for a separate identity per project.
   - `RESEND_API_KEY` — alternative sender (resend.com), no inbox monitoring
3. **Add repository variables** (same page, *Variables* tab):
   - `OWNER_EMAIL` = `coachdan75@gmail.com`
   - `FROM_EMAIL` = your verified Resend sender (only if using Resend)
   - `SEND_ENABLED` = `false` to start (drafts only) — flip to `true` for
     full email autopilot once you've reviewed a week of drafts
   - `DAILY_EMAIL_CAP` = `20`
4. **Check the setup**: `python run_engine.py doctor` (or just look at the
   first run's logs).
5. **Run it once**: Actions tab → *Daily Sales Engine* → Run workflow.
   After that it runs itself **3× a day** (morning / midday / evening US) —
   speed matters: a reply on a 2-hour-old thread far outperforms a day-old
   one. The digest still arrives once each morning.

## The dashboard

The engine commits `dashboard/data.json` after every run. View it:

```bash
python run_engine.py serve        # → http://localhost:8422
```

Or get an always-on URL that updates after every run: repo Settings → Pages →
Source: *GitHub Actions*, then add repository variable `DEPLOY_DASHBOARD=true`.
(Pages sites on free plans are public — the dashboard only shows already-public
forum posts and your drafts, but skip this if you'd rather keep it private.)

Before the first real run you can preview it with sample data:

```bash
pip install -r requirements.txt
python run_engine.py demo
python run_engine.py serve
```

## Run everything from your iPhone

One-time setup (5 minutes):

1. **Install the GitHub app** from the App Store and sign in — this gives you
   the Engine Action buttons, run history, and failure alerts as push
   notifications.
2. **Turn on the hosted dashboard**: repo Settings → Pages → Source *GitHub
   Actions*, and add repository variable `DEPLOY_DASHBOARD=true`.
3. **Add it to your home screen**: open the Pages URL in Safari → Share →
   *Add to Home Screen*. It installs like an app (own icon, full screen,
   light/dark aware).

The daily loop, entirely on the phone:

- Your **digest email** arrives each morning with the numbers and hottest leads.
- Open the **Sales Engine app** → *Waiting on you* → read a draft → **copy
  draft** → tap *view thread* → paste your reply (or send the email).
- Tap the **⚡ Engine Action** button (top of the queue) → Run workflow →
  `posted` / `dismiss` / `replied` / `approve-email` / `suppress` + the draft
  id shown on the card. The dashboard refreshes itself on the next engine run.

## Daily review from a terminal (alternative)

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
- **Self-monitoring**: if a scheduled run fails, a GitHub issue opens
  automatically (labeled `engine-failure`) so it never dies silently. The
  dashboard's "Which searches find buyers" chart shows which `pain_phrases`
  actually produce qualified leads — prune the duds, add variants of the
  winners.

EHS is intentionally excluded from this engine per owner instruction.
