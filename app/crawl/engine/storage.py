import hashlib
import logging
from typing import Optional
from urllib.parse import urlparse
from sqlalchemy.ext.asyncio import AsyncSession
from app.crawl.models import CrawledPage
from app.crawl.repository import CrawlRepository
from app.storage.interface import BaseObjectStorage

logger = logging.getLogger(__name__)


class CrawlStorageManager:
    """
    Coordinates relational database records and object storage assets (HTML, screenshots) for crawled pages.
    """

    def __init__(
        self,
        db: AsyncSession,
        object_storage: BaseObjectStorage,
        storage_type: str = "db",  # "db", "object", or "both"
        bucket_name: str = "crawls",
    ) -> None:
        self.db = db
        self.object_storage = object_storage
        self.storage_type = storage_type
        self.bucket_name = bucket_name

    def _generate_page_id(self, url: str) -> str:
        """
        Creates a deterministic, filesystem-safe page identifier based on URL hostname and path.
        Appends an MD5 hash prefix to guarantee uniqueness and prevent collision/invalid characters.
        """
        parsed = urlparse(url)
        safe_host = parsed.netloc.replace(".", "_")
        safe_path = parsed.path.strip("/").replace("/", "_") or "home"
        # Truncate length to prevent OS filesystem limits
        if len(safe_path) > 50:
            safe_path = safe_path[:50]
        url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
        return f"{safe_host}_{safe_path}_{url_hash}"

    async def store_page(
        self,
        task_id: int,
        url: str,
        title: Optional[str],
        html_content: Optional[str],
        text_content: Optional[str],
        depth: int,
        status_code: int,
        status: str,
        error_log: Optional[str] = None,
        screenshot_bytes: Optional[bytes] = None,
    ) -> CrawledPage:
        """
        Persists page metadata in the database and uploads HTML & screenshot files
        to the global Object Storage service according to the selected storage type.
        """
        page_id = self._generate_page_id(url)
        html_storage_path = None
        screenshot_storage_path = None

        # Determine S3/Object storage keys
        html_key = f"tasks/{task_id}/html/{page_id}.html"
        screenshot_key = f"tasks/{task_id}/screenshots/{page_id}.jpeg"

        # 1. Store assets in Object Storage if configured
        if self.storage_type in ("object", "both"):
            # Upload raw HTML if content is present
            if html_content:
                try:
                    html_storage_path = await self.object_storage.upload_file(
                        bucket=self.bucket_name,
                        key=html_key,
                        data=html_content.encode("utf-8"),
                        content_type="text/html",
                    )
                    logger.info(
                        "Uploaded raw HTML to global storage: %s", html_storage_path
                    )
                except Exception as e:
                    logger.error("Failed to upload HTML to global storage: %s", e)
                    # Append error to error log
                    error_log = (
                        f"{error_log or ''} [ObjectStorage HTML Error: {e}]".strip()
                    )

            # Upload screenshots if present
            if screenshot_bytes:
                try:
                    screenshot_storage_path = await self.object_storage.upload_file(
                        bucket=self.bucket_name,
                        key=screenshot_key,
                        data=screenshot_bytes,
                        content_type="image/jpeg",
                    )
                    logger.info(
                        "Uploaded screenshot to global storage: %s",
                        screenshot_storage_path,
                    )
                except Exception as e:
                    logger.error("Failed to upload screenshot to global storage: %s", e)
                    error_log = f"{error_log or ''} [ObjectStorage Screenshot Error: {e}]".strip()

        # 2. Build CrawledPage model representation
        # If storage_type is "object", we omit storing HTML in the database to prevent table bloat,
        # but we store the relative path reference or text_content for indexing search.
        db_html = html_content
        if self.storage_type == "object":
            # Store the path to raw file in place of HTML content
            db_html = (
                f"object://{self.bucket_name}/{html_key}" if html_storage_path else None
            )

        page = CrawledPage(
            task_id=task_id,
            url=url,
            title=title,
            html_content=db_html,
            text_content=text_content,
            depth=depth,
            status_code=status_code,
            status=status,
            error_log=error_log,
        )

        # 3. Persist record to DB via repository
        if self.db:
            return await CrawlRepository.create_crawled_page(self.db, page)
        else:
            from app.database import SessionLocal
            async with SessionLocal() as db_session:
                return await CrawlRepository.create_crawled_page(db_session, page)
