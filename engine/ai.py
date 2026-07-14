"""Claude API layer: rubric-based lead scoring and grounded outreach drafting.

Design notes (from 2026 outbound research):
- Scoring uses an anchored rubric with per-criterion subscores that are SUMMED
  IN CODE — more consistent than asking the model for one opaque 0-100.
- Outreach must be grounded: every personalized line references the actual
  quoted post. Fabricated "personalization" is the top failure mode of
  autonomous AI SDRs, so the prompt forbids inventing facts.
"""

from __future__ import annotations

import json
import re
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from .config import Config, Product

MODEL = "claude-opus-4-8"

# Owner rule: outreach must read like a human typed it. Dashes used as
# punctuation are the #1 "an AI wrote this" tell and are banned outright.
HUMAN_STYLE = """STYLE RULES, NON-NEGOTIABLE:
- Write like a real person typing an email or a forum comment on their phone.
- NEVER use a dash as punctuation. No em dashes, no en dashes, no " - " between
  clauses. Use a comma, a period, or start a new sentence instead.
- Use contractions (I'm, you're, don't, it's).
- Vary sentence length. Short sentences are good. The odd fragment is fine.
- No marketing words: seamless, effortless, game-changer, unlock, supercharge,
  revolutionize, leverage.
- No bullet points, no numbered lists, no bold text. Just sentences.
- No "I hope this finds you well", "Just circling back", "Quick question".
- If a sentence would sound odd read aloud to a friend, rewrite it."""

_DASH_PUNCT = re.compile(r"\s*[—–]\s*")   # em dash, en dash
_SPACED_HYPHEN = re.compile(r"(?<=\w)\s+-\s+(?=\w)")  # "word - word"


def humanize(text: str) -> str:
    """Backstop scrubber: remove dash-as-punctuation even if the model slips.
    Hyphens inside words (e-commerce, follow-up) are left alone."""
    text = _DASH_PUNCT.sub(", ", text)
    text = _SPACED_HYPHEN.sub(", ", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


# ── Structured output shapes ─────────────────────────────────────────────────

class SignalRubric(BaseModel):
    signal_index: int = Field(description="Index of the signal in the input list")
    explicit_need: int = Field(ge=0, le=25, description="0-25: how explicitly they state the problem this product solves")
    urgency: int = Field(ge=0, le=20, description="0-20: timeline/urgency signals ('need this by', 'currently comparing')")
    fit: int = Field(ge=0, le=25, description="0-25: how well the poster matches the ideal customer profile")
    specificity: int = Field(ge=0, le=15, description="0-15: concrete pain details vs vague complaining")
    reachable: int = Field(ge=0, le=15, description="0-15: is a reply/contact plausible and welcome in this context")
    disqualified: bool = Field(description="True if competitor employee, student assignment, rant with no buying context, or spam")
    rationale: str = Field(description="One sentence explaining the score")
    channel: Literal["reddit_reply", "hn_reply", "email", "skip"] = Field(
        description="Best way to engage: reply in-thread where they asked, email if a contact is visible, skip otherwise"
    )


class ScoreBatch(BaseModel):
    scores: list[SignalRubric]


class OutreachDraft(BaseModel):
    reply_text: str = Field(description="The in-thread reply or email body, plain text")
    subject: str = Field(default="", description="Email subject line; empty for forum replies")
    grounding_quote: str = Field(description="Exact phrase from the original post that this draft responds to")


# ── Client ───────────────────────────────────────────────────────────────────

class AI:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)

    # -- scoring --------------------------------------------------------------

    def score_signals(self, product: Product, signals: list[dict]) -> list[dict]:
        """Score a batch of signals against a product. Returns per-signal dicts
        with total (summed in code), subscores, rationale and channel."""
        catalog = _product_context(product)
        items = "\n\n".join(
            f"[{i}] SOURCE: {s['source']}\nTITLE: {s.get('title') or '(none)'}\n"
            f"POST: {(s.get('body') or '')[:1500]}"
            for i, s in enumerate(signals)
        )
        prompt = f"""You are a lead-qualification analyst for a small software business.

PRODUCT BEING SOLD:
{catalog}

Below are {len(signals)} public posts found by keyword search. For EACH post,
score it on the rubric. Anchors: a 90+ total is someone explicitly asking for a
tool like this right now ("what's the best way to translate a PDF and keep the
formatting? need it this week"); a ~50 is someone describing the pain without
asking for a solution; a ~10 is topically adjacent chatter with no buying
context. Be strict — most posts are NOT leads. Set disqualified=true for
competitor employees, homework/student posts, rants with no buying intent, or
anything that isn't a real person with the problem.

POSTS:
{items}

Return one rubric entry per post, using the post's index."""

        response = self.client.messages.parse(
            model=MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
            output_format=ScoreBatch,
        )
        batch = response.parsed_output
        results = []
        for r in batch.scores:
            total = 0 if r.disqualified else (
                r.explicit_need + r.urgency + r.fit + r.specificity + r.reachable
            )
            results.append({
                "signal_index": r.signal_index,
                "score": total,
                "channel": "skip" if r.disqualified else r.channel,
                "detail": json.dumps({
                    "explicit_need": r.explicit_need,
                    "urgency": r.urgency,
                    "fit": r.fit,
                    "specificity": r.specificity,
                    "reachable": r.reachable,
                    "disqualified": r.disqualified,
                    "rationale": r.rationale,
                }),
            })
        return results

    # -- outreach -------------------------------------------------------------

    def draft_outreach(self, product: Product, signal: dict, kind: str,
                       unsubscribe_footer: str = "") -> OutreachDraft:
        """Draft one grounded reply or first-touch email for a qualified lead."""
        catalog = _product_context(product)
        if kind == "email":
            channel_rules = f"""Write a FIRST-TOUCH COLD EMAIL.
- 50 to 125 words, plain text, one clear call to action.
- Problem-first opener that references their actual post (quote or closely
  paraphrase it). Specific hooks roughly double reply rates.
- No fake familiarity.
- Sign off simply as "Dan".
- End the body with this exact footer on its own lines:
{unsubscribe_footer}

{HUMAN_STYLE}"""
        else:
            channel_rules = f"""Write an IN-THREAD REPLY (Reddit/Hacker News).
- Lead with genuinely useful help: answer their actual question or give a
  concrete tip they can use even without the product.
- Mention the product once, briefly and honestly, in the spirit of "I built
  <name> for exactly this". Communities reward transparency and ban
  astroturfing.
- Match forum tone: casual, like a comment typed between other things.
- 60 to 150 words.

{HUMAN_STYLE}"""

        prompt = f"""You write outreach for a solo founder named Dan.

PRODUCT:
{catalog}

THE LEAD'S PUBLIC POST ({signal['source']}):
TITLE: {signal.get('title') or '(none)'}
POST: {(signal.get('body') or '')[:2000]}

{channel_rules}

HARD RULES:
- Every personalized claim must come from the post above. NEVER invent details
  about the person, their company, or their situation.
- Include in grounding_quote the exact phrase from their post you are
  responding to. If you cannot ground the draft in their actual words, keep it
  generic rather than inventing.
- PRICING AND OFFERS: this is a paid product. NEVER offer free work, free
  translations, free samples, free trials, discounts, or "I'll run it for you
  free". The ONLY incentives you may mention are the ones listed under
  CURRENT OFFERS in the product info above; if none are listed, your call to
  action is simply to look at the product, never a giveaway. Never invent or
  estimate prices; quote pricing only exactly as written in the product info."""

        response = self.client.messages.parse(
            model=MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
            output_format=OutreachDraft,
        )
        draft = response.parsed_output
        draft.reply_text = humanize(draft.reply_text)
        draft.subject = humanize(draft.subject)
        return draft

    def draft_followup(self, product: Product, signal: dict, previous_body: str,
                       touch: int, unsubscribe_footer: str = "") -> OutreachDraft:
        """Draft follow-up email touch 2 or 3 (shorter each time)."""
        prompt = f"""You write outreach for a solo founder named Dan.

PRODUCT:
{_product_context(product)}

This person was emailed before about their post ("{(signal.get('title') or signal.get('body') or '')[:200]}")
and has not replied. Previous email:
---
{previous_body[:800]}
---

Write follow-up #{touch - 1}. Rules:
- Shorter than the previous email ({'2 or 3 sentences' if touch == 2 else '1 or 2 sentences, this is the last touch, close the loop politely'}).
- Add one NEW angle or piece of value, never "just bumping this".
- Plain text, sign off as "Dan".
- End the body with this exact footer on its own lines:
{unsubscribe_footer}
- grounding_quote: reuse the strongest phrase from their original post.
- NEVER offer free work, free translations, discounts, or any incentive not
  listed under CURRENT OFFERS above. No "I'll do one free to win you back".

{HUMAN_STYLE}"""

        response = self.client.messages.parse(
            model=MODEL,
            max_tokens=8000,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
            output_format=OutreachDraft,
        )
        draft = response.parsed_output
        draft.reply_text = humanize(draft.reply_text)
        draft.subject = humanize(draft.subject)
        return draft

    # -- digest ---------------------------------------------------------------

    def write_digest(self, summary_json: str) -> str:
        """Turn today's stats + top leads into a short readable owner digest."""
        prompt = f"""Write today's daily digest for Dan, the owner of a small
software business, based on this JSON from his automated sales engine:

{summary_json}

Format (plain text, no markdown tables):
1. One-line headline: the single most important thing today.
2. "Today's numbers" — 3-4 short lines.
3. "Hot leads to act on" — for each top lead: product, one-line summary of what
   they asked, score, and the direct link. Order hottest first.
4. "Waiting on you" — count of drafts pending approval, if any.
Keep it under 300 words. No fluff, no pep talk. Write in plain human
sentences and never use a dash as punctuation (no em dashes, no " - ");
use a comma or a new sentence instead."""

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        return humanize(text)


def _product_context(product: Product) -> str:
    offers = "; ".join(product.offers) if product.offers else "NONE (no giveaways, no discounts)"
    policy = product.sales_policy.strip() or "Paid product. Never offer free work."
    return (
        f"Name: {product.name}\n"
        f"URL: {product.url}\n"
        f"What it does: {product.description.strip()}\n"
        f"Key benefits: {'; '.join(product.value_props)}\n"
        f"Pricing: {product.pricing}\n"
        f"SALES POLICY: {policy}\n"
        f"CURRENT OFFERS (the only incentives allowed in outreach): {offers}\n"
        f"Ideal customer: {product.ideal_customer.strip()}"
    )
