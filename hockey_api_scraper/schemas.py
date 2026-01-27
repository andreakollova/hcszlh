from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class ArticleOut(BaseModel):
    id: int
    category: str
    origin_url: str
    title: str
    meta_text: Optional[str] = None
    image_url: Optional[str] = None
    scraped_at: datetime

    class Config:
        from_attributes = True

class ArticleDetailOut(ArticleOut):
    content_text: str

class HealthOut(BaseModel):
    ok: bool
    app: str
    db: bool
    articles_count: int
    last_scraped_at: Optional[datetime] = None
    last_origin_url: Optional[str] = None
