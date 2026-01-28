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

# Categories you want
LISTING_URLS = {
    "extraliga": "https://www.hockeyslovakia.sk/sk/articles/extraliga",
    "reprezentacia": "https://www.hockeyslovakia.sk/sk/articles/reprezentacia",
}


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


# Politeness + limits
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


def build_robots_parser() -> robotparser.RobotFileParser:
    """
    IMPORTANT:
    - Do NOT use rp.read() (urllib) to avoid SSL issues on some environments.
    - Fetch robots.txt via requests, then parse text into robotparser.
    """
    rp = robotparser.RobotFileParser()
    robots_url = urljoin(BASE, "/robots.txt")

    resp = SESSION.get(robots_url, timeout=TIMEOUT)
    if resp.status_code >= 400:
        raise ScrapeError(f"HTTP {resp.status_code} for {robots_url}")

    rp.set_url(robots_url)
    rp.parse(resp.text.splitlines())

    # politeness (robots is also a request)
    polite_sleep()
    return rp


def robots_allowed(rp: robotparser.RobotFileParser, url: str) -> bool:
    return rp.can_fetch(UA, url)


def clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_article_links(listing_html: str) -> list[str]:
    """
    Listing pages contain links like: <a href="/sk/article/...">
    We take only the latest MAX_PER_RUN (page usually newest-first).
    """
    soup = BeautifulSoup(listing_html, "lxml")
    links: list[str] = []

    for a in soup.select('a[href^="/sk/article/"]'):
        href = a.get("href")
        if href:
            links.append(urljoin(BASE, href))

    # de-dupe while keeping order
    seen = set()
    ordered: list[str] = []
    for u in links:
        if u not in seen:
            seen.add(u)
            ordered.append(u)

    return ordered[:MAX_PER_RUN]


def parse_article_detail(article_html: str, article_url: str) -> dict:
    """
    From article detail page we extract:
    - title (h1)
    - meta_text (date/author/source) from ".article-meta.clearfix" if present
    - image_url from ".document-gallery.margin-bottom-30 img" if present
    - content_text from ".col-md-8.col-lg-9.col-content"
    """
    soup = BeautifulSoup(article_html, "lxml")

    h1 = soup.select_one("h1")
    if not h1:
        raise ScrapeError(f"Missing h1 on {article_url}")
    title = clean_text(h1.get_text(" ", strip=True))

    meta = soup.select_one(".article-meta.clearfix")
    meta_text = clean_text(meta.get_text(" ", strip=True)) if meta else None

    image_url = None
    gallery = soup.select_one(".document-gallery.margin-bottom-30")
    if gallery:
        img = gallery.select_one("img")
        if img and img.get("src"):
            image_url = urljoin(BASE, img["src"])

    content = soup.select_one(".col-md-8.col-lg-9.col-content")
    if not content:
        # fallback if class order differs
        content = soup.select_one('[class*="col-content"]')
    if not content:
        raise ScrapeError(f"Missing content container on {article_url}")

    parts: list[str] = []
    for node in content.select("p, h2, h3, li"):
        t = node.get_text(" ", strip=True)
        if t:
            parts.append(t)

    if parts:
        content_text = clean_text("\n".join(parts))
    else:
        content_text = clean_text(content.get_text("\n", strip=True))

    return {
        "title": title,
        "meta_text": meta_text,
        "image_url": image_url,
        "content_text": content_text,
    }


def scrape_listing(
    rp: robotparser.RobotFileParser, category: str, listing_url: str
) -> list[dict]:
    """
    Scrape one listing (category) page and up to MAX_PER_RUN latest articles.
    Returns list of dicts ready to insert.
    """
    if not robots_allowed(rp, listing_url):
        raise ScrapeError(f"Robots disallow listing: {listing_url}")

    listing_html = fetch_html(listing_url)
    polite_sleep()

    links = extract_article_links(listing_html)

    items: list[dict] = []
    for url in links:
        if not robots_allowed(rp, url):
            continue

        html = fetch_html(url)
        polite_sleep()

        data = parse_article_detail(html, url)
        data["origin_url"] = url
        data["category"] = category
        items.append(data)

    return items
