from datetime import datetime
from sqlalchemy import ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class CrawlTask(Base):
    __tablename__ = "crawl_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    start_url: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, default="pending")  # "pending", "running", "completed", "failed"
    pages_crawled: Mapped[int] = mapped_column(nullable=False, default=0)
    pages_failed: Mapped[int] = mapped_column(nullable=False, default=0)
    error_message: Mapped[str] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, default=func.now(), onupdate=func.now()
    )

    # Relationship to crawled pages
    crawled_pages: Mapped[list["CrawledPage"]] = relationship(
        "CrawledPage", back_populates="task", cascade="all, delete-orphan"
    )


class CrawledPage(Base):
    __tablename__ = "crawled_pages"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("crawl_tasks.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(index=True, nullable=False)
    title: Mapped[str] = mapped_column(nullable=True)
    html_content: Mapped[str] = mapped_column(nullable=True)  # Nullable if stored in Object Storage
    text_content: Mapped[str] = mapped_column(nullable=True)
    depth: Mapped[int] = mapped_column(nullable=False, default=0)
    status_code: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, default="success")  # "success", "failed"
    error_log: Mapped[str] = mapped_column(nullable=True)  # Logs errors during fetching
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, default=func.now()
    )

    # Back relationship to the parent task
    task: Mapped["CrawlTask"] = relationship("CrawlTask", back_populates="crawled_pages")
