# run_scrape.py
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
    """
    Replace content_text only if:
    - existing is missing OR
    - new is significantly longer (avoid overwriting good content with short noise)
    """
    if is_missing(new):
        return False
    if is_missing(existing):
        return True
    return len(new) > len(existing) + 80


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

                # -----------------------
                # INSERT NEW
                # -----------------------
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

                # -----------------------
                # UPDATE EXISTING (fill missing / improve)
                # -----------------------
                changed = False

                # category: keep existing unless empty
                if is_missing(existing.category) and item.get("category"):
                    existing.category = item["category"]
                    changed = True

                # title: update only if missing
                if is_missing(existing.title) and item.get("title"):
                    existing.title = item["title"]
                    changed = True

                # meta_text: fill if missing
                if is_missing(existing.meta_text) and item.get("meta_text"):
                    existing.meta_text = item["meta_text"]
                    changed = True

                # image_url: fill if missing
                if is_missing(existing.image_url) and item.get("image_url"):
                    existing.image_url = item["image_url"]
                    changed = True

                # content_text: fill if missing or new is much better
                if should_replace_text(existing.content_text, item.get("content_text")):
                    existing.content_text = item["content_text"]
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
