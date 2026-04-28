from datetime import datetime
from typing import Optional
from pydantic import BaseModel, HttpUrl, field_validator
import re


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str
    remember_me: bool = False


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


# ── Sections ──────────────────────────────────────────────────────────────────

class SectionCreate(BaseModel):
    title: str
    description: Optional[str] = None
    is_visible: bool = True


class SectionUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    is_visible: Optional[bool] = None
    order_index: Optional[int] = None


class SectionResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    order_index: int
    is_visible: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ReorderRequest(BaseModel):
    ordered_ids: list[int]


# ── Links ─────────────────────────────────────────────────────────────────────

class LinkCreate(BaseModel):
    section_id: int
    title: str
    description: Optional[str] = None
    url: str
    image_url: Optional[str] = None
    is_visible: bool = True

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class LinkUpdate(BaseModel):
    section_id: Optional[int] = None
    title: Optional[str] = None
    description: Optional[str] = None
    url: Optional[str] = None
    image_url: Optional[str] = None
    is_visible: Optional[bool] = None
    order_index: Optional[int] = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: Optional[str]) -> Optional[str]:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class LinkResponse(BaseModel):
    id: int
    section_id: int
    title: str
    description: Optional[str]
    url: str
    image_url: Optional[str]
    order_index: int
    is_visible: bool
    click_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class SectionWithLinksResponse(SectionResponse):
    links: list[LinkResponse] = []


# ── Tracking ──────────────────────────────────────────────────────────────────

class PageViewCreate(BaseModel):
    referrer: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_content: Optional[str] = None
    utm_term: Optional[str] = None
    screen_width: Optional[int] = None
    screen_height: Optional[int] = None
    session_id: Optional[str] = None
    user_agent: Optional[str] = None


class LinkClickCreate(BaseModel):
    link_id: int
    referrer: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_content: Optional[str] = None
    session_id: Optional[str] = None
    user_agent: Optional[str] = None


# ── Analytics ─────────────────────────────────────────────────────────────────

class AnalyticsOverview(BaseModel):
    total_visits: int
    unique_visitors: int
    total_clicks: int
    clicks_today: int
    visits_today: int
    visits_this_week: int
    visits_this_month: int


class DailyVisit(BaseModel):
    date: str
    visits: int
    clicks: int


class TopLink(BaseModel):
    id: int
    title: str
    url: str
    clicks: int


class TrafficSource(BaseModel):
    source: str
    count: int


class GeoStat(BaseModel):
    country: str
    count: int


class UtmCampaignStat(BaseModel):
    campaign: str
    source: str
    count: int


# ── Settings ──────────────────────────────────────────────────────────────────

class SiteSettings(BaseModel):
    site_title: str = "DKCraftBBQ"
    site_tagline: str = "Gear I Actually Use"
    primary_color: str = "#E25822"
    secondary_color: str = "#8B4513"
    background_color: str = "#1a1a1a"
    background_gradient_end: str = "#2d1810"
    text_color: str = "#f5f0eb"
    card_background: str = "#2a1f1a"
    accent_color: str = "#FF6B35"
    logo_path: Optional[str] = None
    custom_css: str = ""
    background_style: str = "gradient"


# ── Amazon ────────────────────────────────────────────────────────────────────

class AmazonFetchRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def validate_amazon_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class AmazonProductData(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    success: bool = False
    message: str = ""
