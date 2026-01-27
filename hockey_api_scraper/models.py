from sqlalchemy import String, Text, DateTime, Integer, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from datetime import datetime, timezone

class Base(DeclarativeBase):
    pass

class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("origin_url", name="uq_articles_origin_url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # "extraliga" / "reprezentacia"
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    origin_url: Mapped[str] = mapped_column(Text, nullable=False)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    meta_text: Mapped[str] = mapped_column(Text, nullable=True)
    image_url: Mapped[str] = mapped_column(Text, nullable=True)

    content_text: Mapped[str] = mapped_column(Text, nullable=False)

    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
