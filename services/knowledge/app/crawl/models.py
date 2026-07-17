from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from app.auth.models import PyObjectId


class CrawlTask(BaseModel):
    id: Optional[PyObjectId] = Field(None, alias="_id")
    user_id: str  # String username
    start_url: str
    status: str = "pending"  # "pending", "running", "completed", "failed"
    pages_crawled: int = 0
    pages_failed: int = 0
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True


class CrawledPage(BaseModel):
    id: Optional[PyObjectId] = Field(None, alias="_id")
    task_id: str  # References CrawlTask._id (string)
    url: str
    title: Optional[str] = None
    html_content: Optional[str] = None
    text_content: Optional[str] = None
    depth: int = 0
    status_code: int
    status: str = "success"  # "success", "failed"
    error_log: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
