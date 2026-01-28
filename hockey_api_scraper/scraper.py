# scraper.py
import os
import time
import re
from urllib.parse import urljoin
from urllib import robotparser

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


BASE = "https://www.hockeyslovakia.sk"

LISTING_URLS = {
    "extraliga": "https://www.hockeyslovakia.sk/sk/articles/extraliga",
    "reprezentacia": "https://www.hockeyslovakia.sk/sk/articles/reprezentacia",
}


# --------------------
# ENV HELPERS
# --------------------
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


SCRAPE_DELAY = _env_float("SCRAPE_DELAY_SECONDS", 2.5)
TIMEOUT = _env_int("SCRAPE_TIMEOUT_SECONDS", 20)
MAX_PER_RUN = _env_int("SCRAPE_MAX_ARTICLES_PER_RUN", 10)

UA = os.getenv(
    "SCRAPE_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "sk-SK,sk;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }
)


class ScrapeError(Exception):
    pass


def polite_sleep():
    time.sleep(SCRAPE_DELAY)


# --------------------
# HTTP FETCH
# --------------------
@retry(
    retry=retry_if_exception_type((requests.RequestException, ScrapeError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
)
def fetch_html(url: str) -> str:
    resp = SESSION.get(url, timeout=TIMEOUT)
    if resp.status_code >= 400:
        raise ScrapeError(f"HTTP {resp.status_code} for {url}")
    return resp.text


# --------------------
# ROBOTS.TXT (via requests)
# --------------------
def build_robots_parser() -> robotparser.RobotFileParser:
    rp = robotparser.RobotFileParser()
    robots_url = urljoin(BASE, "/robots.txt")

    resp = SESSION.get(robots_url, timeout=TIMEOUT)
    if resp.status_code >= 400:
        raise ScrapeError(f"HTTP {resp.status_code} for {robots_url}")

    rp.set_url(robots_url)
    rp.parse(resp.text.splitlines())

    polite_sleep()
    return rp


def robots_allowed(rp: robotparser.RobotFileParser, url: str) -> bool:
    return rp.can_fetch(UA, url)


# --------------------
# TEXT CLEANING
# --------------------
def clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --------------------
# LISTING PARSER (KEY FIX)
# --------------------
def extract_article_links(listing_html: str) -> list[str]:
    """
    Extract ONLY real article links from article listing.
    This avoids menu/footer/internal pages.
    """
    soup = BeautifulSoup(listing_html, "lxml")

    links: list[str] = []

    # Articles are inside article cards
    # This selector is intentionally strict
    for item in soup.select("article, .article-item, .news-item"):
        a = item.find("a", href=True)
        if not a:
            continue

        href = a["href"]
        if not href.startswith("/sk/article/"):
            continue

        full_url = urljoin(BASE, href)
        links.append(full_url)

    # Fallback: known article list structure
    if not links:
        for a in soup.select(".articles-list a[href^='/sk/article/']"):
            links.append(urljoin(BASE, a["href"]))

    # De-duplicate & limit
    seen = set()
    ordered: list[str] = []
    for u in links:
        if u in seen:
            continue
        seen.add(u)
        ordered.append(u)

    return ordered[:MAX_PER_RUN]


# --------------------
# ARTICLE DETAIL
# --------------------
def parse_article_detail(article_html: str, article_url: str) -> dict:
    soup = BeautifulSoup(article_html, "lxml")

    h1 = soup.select_one("h1")
    if not h1:
        raise ScrapeError(f"Missing title on {article_url}")
    title = clean_text(h1.get_text(" ", strip=True))

    meta = soup.select_one(".article-meta")
    meta_text = clean_text(meta.get_text(" ", strip=True)) if meta else None

    image_url = None
    gallery = soup.select_one(".document-gallery")
    if gallery:
        img = gallery.find("img")
        if img and img.get("src"):
            image_url = urljoin(BASE, img["src"])

    content = soup.select_one(".col-content")
    if not content:
        raise ScrapeError(f"Missing content container on {article_url}")

    parts = []
    for node in content.select("p, h2, h3, li"):
        t = node.get_text(" ", strip=True)
        if t:
            parts.append(t)

    content_text = clean_text("\n".join(parts))

    if len(content_text) < 150:
        raise ScrapeError(f"Content too short on {article_url}")

    return {
        "title": title,
        "meta_text": meta_text,
        "image_url": image_url,
        "content_text": content_text,
    }


# --------------------
# SCRAPE LISTING
# --------------------
def scrape_listing(
    rp: robotparser.RobotFileParser, category: str, listing_url: str
) -> list[dict]:

    if not robots_allowed(rp, listing_url):
        raise ScrapeError(f"Robots disallow listing: {listing_url}")

    listing_html = fetch_html(listing_url)
    polite_sleep()

    links = extract_article_links(listing_html)

    items: list[dict] = []
    for url in links:
        if not robots_allowed(rp, url):
            continue

        try:
            html = fetch_html(url)
            polite_sleep()

            data = parse_article_detail(html, url)
            data["origin_url"] = url
            data["category"] = category
            items.append(data)

        except ScrapeError as e:
            print(f"[SCRAPER] skip url={url} reason={e}")
            continue

    return items
