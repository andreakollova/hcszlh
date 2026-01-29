from __future__ import annotations

from dotenv import load_dotenv

# MUST be called before importing db.py (ENGINE is created at import time)
load_dotenv()

from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from db import SessionLocal, ENGINE
from models import Base, Article
from scraper import build_robots_parser, LISTING_URLS, scrape_listing


def init_db() -> None:
    Base.metadata.create_all(bind=ENGINE)


def get_existing(db, origin_url: str) -> Article | None:
    q = select(Article).where(Article.origin_url == origin_url).limit(1)
    return db.execute(q).scalars().first()


def is_missing(val: str | None) -> bool:
    return val is None or str(val).strip() == ""


def should_replace_text(existing: str | None, new: str | None) -> bool:
    if is_missing(new):
        return False
    if is_missing(existing):
        return True
    return len(new) > len(existing) + 80


def is_good_image(url: str | None) -> bool:
    if is_missing(url):
        return False
    u = str(url)
    # hockeyslovakia používa Upload/Gallery ako hlavný zdroj
    return ("/Upload/Gallery/" in u) or ("/Upload/" in u)


def should_replace_image(existing: str | None, new: str | None) -> bool:
    """
    Prepíš image_url ak:
    - existing je prázdne a new existuje
    - alebo new vyzerá "lepšie" (Upload/Gallery) a existing nie
    - alebo existing vyzerá ako niečo mimo (nie Upload) a new je Upload
    """
    if is_missing(new):
        return False
    if is_missing(existing):
        return True

    if is_good_image(new) and not is_good_image(existing):
        return True

    # ak sa líšia a nové je z Upload/Gallery, preferuj nové
    if str(new).strip() != str(existing).strip() and is_good_image(new):
        return True

    return False


def run_scrape() -> dict:
    init_db()
    rp = build_robots_parser()

    scanned = 0
    inserted = 0
    updated = 0
    skipped = 0
    errors = 0

    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        for category, listing_url in LISTING_URLS.items():
            try:
                scraped_items = scrape_listing(rp, category, listing_url)
            except Exception as e:
                print(f"[RUN] listing failed category={category} url={listing_url} err={e}")
                errors += 1
                continue

            scanned += len(scraped_items)

            for item in scraped_items:
                origin_url = item["origin_url"]
                existing = get_existing(db, origin_url)

                if existing is None:
                    art = Article(
                        category=item.get("category", category),
                        origin_url=origin_url,
                        title=item.get("title") or "",
                        meta_text=item.get("meta_text"),
                        image_url=item.get("image_url"),
                        content_text=item.get("content_text") or "",
                        scraped_at=now,
                    )
                    db.add(art)
                    try:
                        db.commit()
                        inserted += 1
                    except IntegrityError:
                        db.rollback()
                    except Exception as e:
                        db.rollback()
                        print(f"[RUN] insert failed url={origin_url} err={e}")
                        errors += 1
                    continue

                changed = False

                if is_missing(existing.category) and item.get("category"):
                    existing.category = item["category"]
                    changed = True

                if is_missing(existing.title) and item.get("title"):
                    existing.title = item["title"]
                    changed = True

                if is_missing(existing.meta_text) and item.get("meta_text"):
                    existing.meta_text = item["meta_text"]
                    changed = True

                if should_replace_image(existing.image_url, item.get("image_url")):
                    existing.image_url = item.get("image_url")
                    changed = True

                if should_replace_text(existing.content_text, item.get("content_text")):
                    existing.content_text = item.get("content_text") or existing.content_text
                    changed = True

                if changed:
                    existing.scraped_at = now
                    try:
                        db.commit()
                        updated += 1
                    except Exception as e:
                        db.rollback()
                        print(f"[RUN] update failed url={origin_url} err={e}")
                        errors += 1
                else:
                    skipped += 1

    return {
        "scanned": scanned,
        "inserted_new": inserted,
        "updated_existing": updated,
        "skipped_existing": skipped,
        "errors": errors,
    }


if __name__ == "__main__":
    print(run_scrape())
