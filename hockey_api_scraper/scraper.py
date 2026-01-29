from __future__ import annotations

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


def polite_sleep() -> None:
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
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_BG_RE = re.compile(r"background-image\s*:\s*url\((['\"]?)(.*?)\1\)", re.IGNORECASE)


def _extract_bg_image_from_style(style: str) -> str | None:
    if not style:
        return None
    m = _BG_RE.search(style)
    if not m:
        return None
    raw = (m.group(2) or "").strip().strip("'\"").strip()
    return raw or None


def extract_listing_items(listing_html: str) -> list[dict]:
    """
    STRICT: ber iba news tiles (to, čo má class article-item + background-image)
    Typická štruktúra podľa tvojho DevTools:
      <article ...>
        <div class="overlay-container">
          <a class="article-item ... lazy" href="/sk/article/..." style="background-image:url('...')">
    """
    soup = BeautifulSoup(listing_html, "lxml")

    candidates: list[dict] = []

    # 1) Najpresnejšie: iba tile linky v článkových <article> blokoch
    for a in soup.select('article .overlay-container a.article-item[href^="/sk/article/"]'):
        href = (a.get("href") or "").strip()
        if not href.startswith("/sk/article/"):
            continue

        origin_url = urljoin(BASE, href)

        thumb = None
        bg = _extract_bg_image_from_style(a.get("style", ""))
        if bg:
            thumb = urljoin(BASE, bg)

        candidates.append({"origin_url": origin_url, "image_url": thumb})

    # 2) Fallback: stále len .article-item, ale kdekoľvek (nie všetky stránky majú overlay-container)
    if not candidates:
        for a in soup.select('a.article-item[href^="/sk/article/"]'):
            href = (a.get("href") or "").strip()
            if not href.startswith("/sk/article/"):
                continue
            origin_url = urljoin(BASE, href)

            thumb = None
            bg = _extract_bg_image_from_style(a.get("style", ""))
            if bg:
                thumb = urljoin(BASE, bg)

            candidates.append({"origin_url": origin_url, "image_url": thumb})

    # de-dupe keep order + limit
    seen: set[str] = set()
    ordered: list[dict] = []
    for it in candidates:
        u = it["origin_url"]
        if u in seen:
            continue
        seen.add(u)
        ordered.append(it)

    return ordered[:MAX_PER_RUN]


def extract_detail_image_url(soup: BeautifulSoup) -> str | None:
    def pick_img(sel: str) -> str | None:
        img = soup.select_one(sel)
        if not img:
            return None
        for attr in ("src", "data-src", "data-original", "data-lazy-src", "data-lazy"):
            val = img.get(attr)
            if val:
                return urljoin(BASE, val.strip())
        return None

    # presne to, čo máš na detaile
    url = pick_img(".document-gallery .doc-image-main img")
    if url:
        return url

    url = pick_img(".document-gallery img")
    if url:
        return url

    # niekedy môže byť hero v static-page
    url = pick_img(".static-page img")
    if url:
        return url

    # posledná záchrana: prvý obrázok v texte
    url = pick_img(".col-content img")
    if url:
        return url

    return None


def parse_article_detail(article_html: str, article_url: str) -> dict:
    soup = BeautifulSoup(article_html, "lxml")

    h1 = soup.select_one("h1")
    if not h1:
        raise ScrapeError(f"Missing title on {article_url}")
    title = clean_text(h1.get_text(" ", strip=True))

    meta = soup.select_one(".article-meta")
    meta_text = clean_text(meta.get_text(" ", strip=True)) if meta else None

    # content container
    content = soup.select_one(".col-md-8.col-lg-9.col-content")
    if not content:
        content = soup.select_one(".col-content")
    if not content:
        content = soup.select_one(".static-page")
    if not content:
        raise ScrapeError(f"Missing content container on {article_url}")

    parts: list[str] = []
    for node in content.select("p, h2, h3, li"):
        t = node.get_text(" ", strip=True)
        if t:
            parts.append(t)

    content_text = clean_text("\n".join(parts)) if parts else clean_text(content.get_text("\n", strip=True))
    if len(content_text) < 150:
        raise ScrapeError(f"Content too short on {article_url}")

    image_url = extract_detail_image_url(soup)

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

    listing_items = extract_listing_items(listing_html)

    items: list[dict] = []
    for li in listing_items:
        url = li["origin_url"]
        listing_thumb = li.get("image_url")

        if not robots_allowed(rp, url):
            continue

        try:
            html = fetch_html(url)
            polite_sleep()

            data = parse_article_detail(html, url)

            # fallback: ak detail nič nemá, použi thumbnail z karty (správny tile)
            if not data.get("image_url") and listing_thumb:
                data["image_url"] = listing_thumb

            data["origin_url"] = url
            data["category"] = category
            items.append(data)

        except ScrapeError as e:
            print(f"[SCRAPER] skip url={url} reason={e}")
            continue

    return items
