from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class CrawlRequest(BaseModel):
    urls: List[HttpUrl] = Field(
        ..., description="List of seed URLs to crawl or refresh"
    )
    max_depth: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Maximum crawl depth (0 for single page/no discovery)",
    )
    max_pages: int = Field(
        default=20, ge=1, le=100, description="Maximum total pages to crawl"
    )
    strategy: Literal["single", "recursive"] = Field(
        default="recursive",
        description="Single only crawls inputs; recursive discovers and follows local links.",
    )
    concurrency_strategy: Literal["single", "concurrent"] = Field(
        default="concurrent",
        description="Sequential single context vs parallel concurrent multi-page workers.",
    )
    concurrency_limit: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Max concurrent page workers (applicable only in concurrent mode)",
    )
    allowed_domains: Optional[List[str]] = Field(
        default=None,
        description="Optional list of allowed domains. If not provided, defaults to seed URL domains.",
    )
    allowed_urls: Optional[List[str]] = Field(
        default=None,
        description="Optional list of allowed URL prefixes. If provided, only links starting with these prefixes will be crawled.",
    )


class CrawlTaskResponse(BaseModel):
    id: int
    user_id: int
    start_url: str
    status: str
    pages_crawled: int
    pages_failed: int
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CrawledPageResponse(BaseModel):
    id: int
    task_id: int
    url: str
    title: Optional[str]
    depth: int
    status_code: int
    status: str
    error_log: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CrawledPageDetailResponse(CrawledPageResponse):
    html_content: Optional[str]
    text_content: Optional[str]
