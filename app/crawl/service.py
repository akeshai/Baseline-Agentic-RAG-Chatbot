import asyncio
import logging
from typing import List
from urllib.parse import urlparse
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.configs.crawl import settings as crawl_settings
from app.database import SessionLocal
from app.storage.local import LocalObjectStorage
from app.crawl.engine.scraper import PlaywrightScraper
from app.crawl.engine.storage import CrawlStorageManager
from app.crawl.engine.utils import RateLimiter
from app.crawl.engine.crawler import CrawlerEngine
from app.crawl.models import CrawlTask
from app.crawl.repository import CrawlRepository
from app.crawl.schemas import CrawlRequest

logger = logging.getLogger(__name__)

# Global semaphore to limit concurrent crawl tasks running on the system to prevent CPU/Memory exhaustion
_concurrent_crawls_semaphore = asyncio.Semaphore(2)


class CrawlService:
    @staticmethod
    async def start_crawl_task(
        db: AsyncSession,
        user_id: int,
        crawl_req: CrawlRequest,
        background_tasks: BackgroundTasks,
    ) -> CrawlTask:
        """
        Creates a pending crawl task in the database and triggers the background crawler.
        """
        # Determine the primary start URL to log
        primary_url = str(crawl_req.urls[0])

        # Create CrawlTask record
        task = await CrawlRepository.create_task(db, user_id, primary_url)

        # Enqueue the crawler pipeline in FastAPI background tasks
        background_tasks.add_task(
            CrawlService.run_crawl_background,
            task_id=task.id,
            urls=[str(u) for u in crawl_req.urls],
            max_depth=0 if crawl_req.strategy == "single" else crawl_req.max_depth,
            max_pages=crawl_req.max_pages,
            strategy=crawl_req.strategy,
            concurrency_strategy=crawl_req.concurrency_strategy,
            concurrency_limit=crawl_req.concurrency_limit,
        )

        return task

    @staticmethod
    async def run_crawl_background(
        task_id: int,
        urls: List[str],
        max_depth: int,
        max_pages: int,
        strategy: str,
        concurrency_strategy: str,
        concurrency_limit: int,
    ) -> None:
        """
        Background executor running outside the main request cycle.
        Politely queues tasks using the global semaphore and handles browser launch/teardown.
        """
        logger.info("Background crawl task ID %d waiting for semaphore...", task_id)

        async with _concurrent_crawls_semaphore:
            logger.info("Background crawl task ID %d started.", task_id)

            # 1. Update task status to running
            async with SessionLocal() as db:
                await CrawlRepository.update_task_status(db, task_id, "running")

            # Resolve allowed domains for BFS scoping
            allowed_domains = []
            if strategy == "recursive":
                for url in urls:
                    try:
                        netloc = urlparse(url).netloc
                        if netloc:
                            allowed_domains.append(netloc)
                    except Exception:
                        pass
                if not allowed_domains:
                    allowed_domains = None
            else:
                allowed_domains = None

            # 2. Run crawl pipeline
            error_message = None
            status = "completed"

            try:
                # Initialize Playwright browser dynamically within context manager
                # Launch with UI visible (headless=False) in DEBUG or STAGING modes, otherwise headless=True
                import os

                mode = os.getenv("MODE", "PRODUCTION").strip("'\" ")
                headless = True
                if mode in ("DEBUG", "STAGING"):
                    headless = False

                async with PlaywrightScraper(headless=headless) as scraper:
                    # Dynamically instantiate independent DB sessions inside the loop via StorageManager
                    async with SessionLocal() as db:
                        object_storage = LocalObjectStorage(
                            root_dir=crawl_settings.object_storage_root
                        )
                        storage_manager = CrawlStorageManager(
                            db=db,
                            object_storage=object_storage,
                            storage_type=crawl_settings.raw_storage_type,
                            bucket_name=crawl_settings.raw_html_bucket,
                        )

                        rate_limiter = RateLimiter(
                            base_delay=1.0,
                            random_delay=0.5,
                            max_requests_per_minute=20,
                            max_requests_per_hour=200,
                        )

                        engine = CrawlerEngine(
                            task_id=task_id,
                            start_urls=urls,
                            max_depth=max_depth,
                            max_pages=max_pages,
                            concurrency_strategy=concurrency_strategy,
                            concurrency_limit=concurrency_limit,
                            scraper=scraper,
                            rate_limiter=rate_limiter,
                            storage_manager=storage_manager,
                            allowed_domains=allowed_domains,
                            allowed_urls=None,
                        )

                        # Start crawl loop. Releases execution context via sleeps to avoid CPU blocking.
                        await engine.start()

            except Exception as e:
                logger.exception(
                    "Crawl task ID %d failed in background execution", task_id
                )
                status = "failed"
                error_message = str(e)

            # 3. Update task final status
            async with SessionLocal() as db:
                await CrawlRepository.update_task_status(
                    db, task_id, status, error_message
                )
