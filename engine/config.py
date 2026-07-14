"""Configuration loading: products.yaml + settings.yaml + environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
DASHBOARD_DIR = ROOT / "dashboard"


@dataclass
class Product:
    id: str
    name: str
    url: str
    tagline: str
    description: str
    value_props: list[str]
    pricing: str
    ideal_customer: str
    pain_phrases: list[str]
    keywords: list[str]
    subreddits: list[str]
    negative_keywords: list[str] = field(default_factory=list)
    priority: int = 99
    # What outreach may and may not promise. offers is the ONLY source of
    # incentives a draft can mention; empty means no incentives, ever.
    sales_policy: str = ""
    offers: list[str] = field(default_factory=list)


@dataclass
class Config:
    products: list[Product]
    settings: dict

    # environment
    anthropic_api_key: str | None = None
    serper_api_key: str | None = None
    resend_api_key: str | None = None
    from_email: str | None = None
    owner_email: str | None = None
    send_enabled: bool = False
    daily_email_cap: int = 20
    email_provider: str = "resend"        # resend | instantly
    instantly_api_key: str | None = None
    instantly_campaign_id: str | None = None

    def product(self, product_id: str) -> Product | None:
        return next((p for p in self.products if p.id == product_id), None)

    @property
    def email_configured(self) -> bool:
        """Resend transactional sending (digest + resend-provider outreach)."""
        return bool(self.resend_api_key and self.from_email)

    @property
    def instantly_configured(self) -> bool:
        return bool(self.instantly_api_key and self.instantly_campaign_id)

    def instantly_campaign_for(self, product_id: str) -> str | None:
        """Per-product campaign from settings.yaml, else the default campaign."""
        mapping = self.settings.get("email", {}).get("instantly_campaigns", {}) or {}
        return mapping.get(product_id) or self.instantly_campaign_id


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config() -> Config:
    products_raw = _load_yaml(CONFIG_DIR / "products.yaml").get("products", [])
    settings = _load_yaml(CONFIG_DIR / "settings.yaml")

    products = []
    for raw in products_raw:
        known = {k: raw[k] for k in Product.__dataclass_fields__ if k in raw}
        products.append(Product(**known))
    products.sort(key=lambda p: p.priority)

    return Config(
        products=products,
        settings=settings,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
        serper_api_key=os.environ.get("SERPER_API_KEY"),
        resend_api_key=os.environ.get("RESEND_API_KEY"),
        from_email=os.environ.get("FROM_EMAIL"),
        owner_email=os.environ.get("OWNER_EMAIL", "coachdan75@gmail.com"),
        send_enabled=os.environ.get("SEND_ENABLED", "false").lower() == "true",
        daily_email_cap=int(os.environ.get("DAILY_EMAIL_CAP", "20")),
        email_provider=os.environ.get("EMAIL_PROVIDER", "resend").lower(),
        instantly_api_key=os.environ.get("INSTANTLY_API_KEY"),
        instantly_campaign_id=os.environ.get("INSTANTLY_CAMPAIGN_ID"),
    )
