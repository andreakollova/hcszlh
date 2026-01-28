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


def clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_real_article_url(url: str) -> bool:
    """
    Filter out non-article pages that can appear on listing pages.
    Real articles are usually /sk/article/<slug>
    We'll also require a slug with at least one dash or longer than a few chars.
    """
    if not url.startswith("https://www.hockeyslovakia.sk/sk/article/"):
        return False

    slug = url.split("/sk/article/")[-1].strip("/")
    if not slug:
        return False

    # Reject known non-article slugs (example you hit)
    blocked = {"zakladne-udaje"}
    if slug in blocked:
        return False

    # Heuristic: most news slugs are longer and often include '-'
    if len(slug) < 8:
        return False

    return True


def extract_article_links(listing_html: str) -> list[str]:
    """
    Safer extraction:
    - Prefer anchors inside article listing containers if present.
    - Then fallback to generic /sk/article/ links but filtered.
    """
    soup = BeautifulSoup(listing_html, "lxml")

    candidates: list[str] = []

    # 1) try: links inside typical list areas (less menu/nav noise)
    for a in soup.select('section a[href^="/sk/article/"], main a[href^="/sk/article/"], .col-content a[href^="/sk/article/"]'):
        href = a.get("href")
        if href:
            candidates.append(urljoin(BASE, href))

    # 2) fallback: any /sk/article/ link
    if not candidates:
        for a in soup.select('a[href^="/sk/article/"]'):
            href = a.get("href")
            if href:
                candidates.append(urljoin(BASE, href))

    # de-dupe keep order + filter non-articles
    seen = set()
    ordered: list[str] = []
    for u in candidates:
        if u in seen:
            continue
        seen.add(u)
        if is_real_article_url(u):
            ordered.append(u)

    return ordered[:MAX_PER_RUN]


def parse_article_detail(article_html: str, article_url: str) -> dict:
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

    # Your expected container
    content = soup.select_one(".col-md-8.col-lg-9.col-content")

    # Fallbacks (some templates differ)
    if not content:
        content = soup.select_one(".col-content")
    if not content:
        content = soup.select_one("article")
    if not content:
        # As last resort: main content area
        content = soup.select_one("main")

    if not content:
        raise ScrapeError(f"Missing content container on {article_url}")

    parts: list[str] = []
    for node in content.select("p, h2, h3, li"):
        t = node.get_text(" ", strip=True)
        if t:
            parts.append(t)

    content_text = clean_text("\n".join(parts)) if parts else clean_text(content.get_text("\n", strip=True))

    # Extra safety: if the extracted text is too short, it's likely not a news article
    if len(content_text) < 200:
        raise ScrapeError(f"Content too short (not a real article) on {article_url}")

    return {
        "title": title,
        "meta_text": meta_text,
        "image_url": image_url,
        "content_text": content_text,
    }


def scrape_listing(rp: robotparser.RobotFileParser, category: str, listing_url: str) -> list[dict]:
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
            # skip bad/non-article pages without killing the whole run
            print(f"[SCRAPER] skip url={url} reason={e}")
            continue

    return items
