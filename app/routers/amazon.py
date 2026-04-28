import logging
import re
import time
from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from .. import models, schemas
from ..auth import get_current_user

router = APIRouter(prefix="/api/amazon", tags=["amazon"])
limiter = Limiter(key_func=get_remote_address)
log = logging.getLogger("dkcraftbbq.amazon")

_BLOCK_PHRASES = [
    "enter the characters you see below",
    "type the characters you see in this image",
    "api-services-support@amazon.com",
    "to discuss automated access to amazon data",
]


def _is_blocked(page_title: str, body_text: str) -> bool:
    title_lower = page_title.lower()
    if "robot check" in title_lower or (title_lower.startswith("sorry") and "!" in title_lower):
        return True
    body_lower = body_text.lower()
    return any(phrase in body_lower for phrase in _BLOCK_PHRASES)


def _extract_image(soup, html_text: str) -> str | None:
    m = re.search(r'"hiRes"\s*:\s*"(https://[^"]+)"', html_text)
    if m:
        return m.group(1)
    el = soup.find("img", id="landingImage")
    if el:
        src = el.get("data-old-hires") or el.get("src")
        if src:
            return src
    el = soup.find("img", id="imgBlkFront")
    if el and el.get("src"):
        return el["src"]
    m = re.search(r'"large"\s*:\s*"(https://[^"]+\.(?:jpg|png|webp))"', html_text)
    if m:
        return m.group(1)
    return None


def _extract_description(soup) -> str | None:
    bullets = soup.find("div", id="feature-bullets")
    if bullets:
        items = [li.get_text(strip=True) for li in bullets.find_all("span", class_="a-list-item") if li.get_text(strip=True)]
        if items:
            return " • ".join(items[:4])[:600]
    el = soup.find("div", id="productDescription")
    if el:
        return el.get_text(" ", strip=True)[:600]
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        return meta["content"].strip()[:600]
    return None


def _fetch_product(url: str):
    """Fetch Amazon product page using curl_cffi Chrome impersonation."""
    from curl_cffi import requests as cf
    from bs4 import BeautifulSoup

    with cf.Session(impersonate="chrome120") as session:
        # Pre-warm with homepage to pick up session cookies
        try:
            session.get("https://www.amazon.com/", timeout=8)
            log.info("Session warmed with homepage cookies")
        except Exception as e:
            log.warning("Homepage pre-warm failed (non-fatal): %s", e)
        time.sleep(0.6)

        response = session.get(url, timeout=15)
        log.info("Amazon response: status=%s url=%s", response.status_code, response.url)

        if response.status_code == 503:
            log.warning("503 on first attempt, retrying after delay")
            time.sleep(4)
            response = session.get(url, timeout=15)
            if response.status_code == 503:
                return None, "Amazon returned 503. Wait a moment and try again."

        soup = BeautifulSoup(response.text, "lxml")
        page_title = soup.title.string if soup.title else ""
        log.info("Page title: %s", page_title)

        if _is_blocked(page_title, response.text):
            log.warning("CAPTCHA/block page detected. Title: %s", page_title)
            return None, f"Amazon served a CAPTCHA page ('{page_title}'). Try again in a minute."

        title_el = soup.find("span", id="productTitle")
        title = title_el.get_text(strip=True) if title_el else None
        if not title:
            el = soup.find("h1", id="title")
            title = el.get_text(strip=True) if el else None

        if not title:
            log.warning("No product title found. Page title: %s", page_title)
            log.debug("Body snippet: %s", response.text[500:1500])
            return None, f"Could not find product title (page title was: '{page_title}'). Check that the URL is a product page."

        image_url = _extract_image(soup, response.text)
        description = _extract_description(soup)
        log.info("Extracted — title: %.60s | image: %s | desc: %s", title, bool(image_url), bool(description))
        return {"title": title, "image_url": image_url, "description": description}, ""


@router.post("/fetch", response_model=schemas.AmazonProductData)
@limiter.limit("20/minute")
def fetch_amazon_product(
    request: Request,
    body: schemas.AmazonFetchRequest,
    _: models.AdminUser = Depends(get_current_user),
):
    log.info("Fetching Amazon product: %s", body.url)
    try:
        result, error = _fetch_product(body.url)
        if result is None:
            return schemas.AmazonProductData(success=False, message=error)
        return schemas.AmazonProductData(
            title=result["title"],
            image_url=result["image_url"],
            description=result["description"],
            success=True,
            message="Product data fetched successfully.",
        )
    except Exception as e:
        log.exception("Unexpected error fetching Amazon product")
        return schemas.AmazonProductData(success=False, message=f"Unexpected error: {str(e)[:150]}")
