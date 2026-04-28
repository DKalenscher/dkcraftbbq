from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text,
    ForeignKey, func,
)
from sqlalchemy.orm import relationship
from .database import Base


class AdminUser(Base):
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(200), nullable=False)
    created_at = Column(DateTime, default=func.now())
    last_login = Column(DateTime, nullable=True)


class Section(Base):
    __tablename__ = "sections"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    order_index = Column(Integer, default=0, nullable=False)
    is_visible = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    links = relationship(
        "Link",
        back_populates="section",
        cascade="all, delete-orphan",
        order_by="Link.order_index",
    )


class Link(Base):
    __tablename__ = "links"

    id = Column(Integer, primary_key=True, index=True)
    section_id = Column(Integer, ForeignKey("sections.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    url = Column(Text, nullable=False)
    image_url = Column(Text, nullable=True)
    order_index = Column(Integer, default=0, nullable=False)
    is_visible = Column(Boolean, default=True, nullable=False)
    click_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    section = relationship("Section", back_populates="links")
    clicks = relationship("LinkClick", back_populates="link", cascade="all, delete-orphan")


class PageView(Base):
    __tablename__ = "page_views"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=func.now(), index=True)
    ip_hash = Column(String(32), nullable=True, index=True)
    user_agent = Column(Text, nullable=True)
    referrer = Column(Text, nullable=True)
    utm_source = Column(String(200), nullable=True, index=True)
    utm_medium = Column(String(200), nullable=True)
    utm_campaign = Column(String(200), nullable=True, index=True)
    utm_content = Column(String(200), nullable=True)
    utm_term = Column(String(200), nullable=True)
    country = Column(String(100), nullable=True, index=True)
    region = Column(String(100), nullable=True)
    city = Column(String(100), nullable=True)
    screen_width = Column(Integer, nullable=True)
    screen_height = Column(Integer, nullable=True)
    session_id = Column(String(36), nullable=True)


class LinkClick(Base):
    __tablename__ = "link_clicks"

    id = Column(Integer, primary_key=True, index=True)
    link_id = Column(Integer, ForeignKey("links.id", ondelete="CASCADE"), nullable=False, index=True)
    timestamp = Column(DateTime, default=func.now(), index=True)
    ip_hash = Column(String(32), nullable=True)
    user_agent = Column(Text, nullable=True)
    referrer = Column(Text, nullable=True)
    utm_source = Column(String(200), nullable=True)
    utm_medium = Column(String(200), nullable=True)
    utm_campaign = Column(String(200), nullable=True)
    utm_content = Column(String(200), nullable=True)
    session_id = Column(String(36), nullable=True)
    country = Column(String(100), nullable=True)
    region = Column(String(100), nullable=True)
    city = Column(String(100), nullable=True)

    link = relationship("Link", back_populates="clicks")


class SiteSetting(Base):
    __tablename__ = "site_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
