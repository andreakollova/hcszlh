# run_scrape.py
from dotenv import load_dotenv

# MUST be called before importing db.py (ENGINE is created at import time)
load_dotenv()

from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from db import SessionLocal, ENGINE
from models import Base, Article
from scraper import build_robots_parser, LISTING_URLS, scrape_listing


def init_db():
    Base.metadata.create_all(bind=ENGINE)


def already_exists(db, origin_url: str) -> bool:
    q = select(Article.id).where(Article.origin_url == origin_url).limit(1)
    return db.execute(q).first() is not None


def run_scrape() -> dict:
    init_db()
    rp = build_robots_parser()

    scanned = 0
    inserted = 0

    with SessionLocal() as db:
        for category, listing_url in LISTING_URLS.items():
            items = scrape_listing(rp, category, listing_url)
            scanned += len(items)

            for item in items:
                # avoid duplicate work
                if already_exists(db, item["origin_url"]):
                    continue

                art = Article(
                    category=item["category"],
                    origin_url=item["origin_url"],
                    title=item["title"],
                    meta_text=item.get("meta_text"),
                    image_url=item.get("image_url"),
                    content_text=item["content_text"],
                    scraped_at=datetime.now(timezone.utc),
                )

                db.add(art)
                try:
                    db.commit()
                    inserted += 1
                except IntegrityError:
                    # if another process inserted the same URL in between
                    db.rollback()

    return {"scanned": scanned, "inserted_new": inserted}


if __name__ == "__main__":
    print(run_scrape())
