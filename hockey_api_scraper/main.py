import os
from dotenv import load_dotenv

from fastapi import FastAPI, Depends, Query, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session

from db import SessionLocal, db_ping, ENGINE
from models import Base, Article
from schemas import ArticleOut, ArticleDetailOut, HealthOut

load_dotenv()

APP_NAME = os.getenv("APP_NAME", "hockeyslovakia-api-scraper")

# ---------------------------
# APP
# ---------------------------
app = FastAPI(title=APP_NAME, version="1.0.0")

# ---------------------------
# CORS (Fix for "Failed to fetch")
# ---------------------------
# Example:
# CORS_ORIGINS="http://localhost:5173,https://tvoja-app.vercel.app"
# Or during development/testing:
# CORS_ORIGINS="*"
cors_env = os.getenv("CORS_ORIGINS", "*").strip()

if cors_env == "*":
    allow_origins = ["*"]
else:
    allow_origins = [o.strip() for o in cors_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=False,  # set True only if you REALLY need cookies
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------
# STARTUP
# ---------------------------
@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=ENGINE)

# ---------------------------
# DB DEP
# ---------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ---------------------------
# ROUTES
# ---------------------------
@app.get("/", response_class=HTMLResponse)
def home():
    return f"""
    <html>
      <head><title>{APP_NAME}</title></head>
      <body style="font-family: Arial; padding: 24px;">
        <h1>{APP_NAME}</h1>
        <ul>
          <li><a href="/health">/health</a></li>
          <li><a href="/docs">/docs</a></li>
          <li><a href="/api/articles">/api/articles</a></li>
        </ul>
      </body>
    </html>
    """

@app.get("/health", response_model=HealthOut)
def health(db: Session = Depends(get_db)):
    db_ok = db_ping()

    total = db.execute(select(func.count(Article.id))).scalar() or 0
    last_scraped = db.execute(select(func.max(Article.scraped_at))).scalar()
    last_url = db.execute(
        select(Article.origin_url)
        .order_by(desc(Article.scraped_at), desc(Article.id))
        .limit(1)
    ).scalar()

    return HealthOut(
        ok=True,
        app=APP_NAME,
        db=db_ok,
        articles_count=int(total),
        last_scraped_at=last_scraped,
        last_origin_url=last_url,
    )

@app.get("/api/articles", response_model=list[ArticleOut])
def list_articles(
    category: str | None = Query(default=None, description="extraliga alebo reprezentacia"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    q = select(Article).order_by(desc(Article.scraped_at), desc(Article.id))
    if category:
        q = q.where(Article.category == category)
    q = q.limit(limit).offset(offset)
    return db.execute(q).scalars().all()

@app.get("/api/articles/{article_id}", response_model=ArticleDetailOut)
def get_article(article_id: int, db: Session = Depends(get_db)):
    item = db.execute(
        select(Article).where(Article.id == article_id).limit(1)
    ).scalars().first()

    if not item:
        raise HTTPException(status_code=404, detail="Article not found")

    return item
