# Sales Engine Playbook

The strategy behind the engine, based on 2025–2026 outbound research. Read this
once; it explains why the engine works the way it does and how to scale it.

## Why this design (and not a "fully autonomous AI salesperson")

2025 was the year fully-autonomous AI SDRs failed publicly: the sector saw
50–70% cancellation rates on managed AI-SDR contracts, and the best-funded
player lost ~70–80% of customers within months. The autopsies point at four
failure modes — this engine is designed against each one:

| Failure mode | Our countermeasure |
|---|---|
| Hallucinated personalization (fake details, invented compliments) | Every draft must include a `grounding_quote` — the exact phrase from the lead's post it responds to. Inventing details is forbidden in the prompt. |
| Deliverability collapse from autonomous volume | Hard daily cap (`DAILY_EMAIL_CAP`), send-window hours, 3-touch max, suppression list checked on every send. |
| Platform bans for automated posting (Reddit/LinkedIn) | Forum replies are **never** auto-posted. The engine drafts; you post. 25 min/day of human posting built one SaaS 50 paying customers in 6 months. |
| No feedback loop | Every message, score and outcome is logged in `data/engine.db`; the dashboard shows the funnel honestly. |

## The two channels

**1. Engage-where-they-asked (highest conversion).** A helpful reply on a
2-hour-old Reddit/HN thread outperforms a perfect cold email sent three days
later. Rules that work: lead with genuinely useful help, disclose you built the
product ("I built X for exactly this"), never link-drop in a first interaction.
The engine finds the threads, scores them, and writes the reply — you spend 10
minutes posting the good ones.

**2. Cold email (scalable).** For leads found via Google/Quora where there is
no thread to join. 50–125 words, plain text, problem-first opener referencing
their actual post (specific hooks get roughly double the replies of generic
ones), one CTA, 3 touches over ~2 weeks (~60% of replies come from follow-ups;
more than 4 touches produces spam complaints, not replies).

## Honest benchmarks (what "working" looks like)

Per 1,000 raw signals discovered:

| Stage | Realistic rate |
|---|---|
| Signal → qualified (score ≥ 60) | 5–15% |
| Qualified → contactable | 60–85% |
| Contacted → reply | 3–5% average; 8–15% achievable with grounded personalization |
| Reply → trial/meeting | 15–30% |

Net: ~1,000 signals → ~60–100 contacted → ~3–10 replies → **1–3 trials**.
The dashboard's "good" thresholds: **≥5% reply rate, ≥25% reply→trial.**
Expect modest absolute numbers in week 1 — the compounding comes from running
every single day and from tuning `pain_phrases` toward what actually qualifies.

## Email deliverability checklist (before flipping SEND_ENABLED=true)

1. **Use a separate domain** for outreach (e.g. `getdoc2translate.com`), never
   your product domain — a spam complaint must not hurt your main domain.
2. **DNS**: SPF + DKIM (2048-bit) + DMARC (`p=none` minimum). Resend sets most
   of this up when you verify the domain.
3. **Warm up 4–6 weeks**: 5–10/day → 15–20 → 30–40 → cap ~50/day/inbox. That's
   why `DAILY_EMAIL_CAP` starts at 20.
4. **Keep bounces <2% and complaints <0.3%** — suppress bounced addresses
   immediately (`run_engine.py suppress <email> --reason bounced`).
5. **CAN-SPAM**: honest subject, working opt-out honored instantly, physical
   address. Add your mailing address to `unsubscribe_footer` in
   `config/settings.yaml` before real sending. (Penalties run to ~$53k/email —
   the footer and suppression list are not optional.)

## The scaling path

- **Week 1–2 (draft-only)**: review drafts daily, dismiss bad ones. Tune
  `pain_phrases` in products.yaml toward whatever produced 80+ scores.
- **Week 3+**: set up the outreach domain + Resend, flip `SEND_ENABLED=true`
  with the cap at 10–20/day.
- **When email volume justifies it**: add contact enrichment (a "waterfall"
  of email-finder APIs — Hunter → Prospeo → Dropcontact; one provider finds
  40–60% of addresses, a 3-provider waterfall reaches 85–95%). Slot it in
  between scoring and outreach in `engine/pipeline.py`.
- **When Reddit replies prove out**: consider Reddit's official OAuth API for
  higher-volume discovery (100 queries/min free vs the throttled public JSON
  used now).
- **Optional paid layer**: X/Twitter keyword monitoring (~$25–50/mo pay-per-use)
  once the free sources are saturated.

## Tuning the scorer

The rubric lives in `engine/ai.py` (`SignalRubric`): explicit need (25),
urgency (20), ICP fit (25), specificity (15), reachability (15). The qualify
threshold (60) is in `config/settings.yaml`. If you get too many weak drafts,
raise the threshold to 70; too few leads, lower it to 50 and tighten
`pain_phrases` instead — precision of the search phrases matters more than the
threshold.

## Recording outcomes

The engine can't see replies to forum posts or your inbox (yet), so close the
loop manually — it takes seconds and makes the dashboard honest:

```bash
python run_engine.py replied <draft_id>     # counts the reply, stops follow-ups
```
