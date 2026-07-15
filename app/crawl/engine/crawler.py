import asyncio
import logging
import random
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Set
from app.crawl.engine.scraper import PlaywrightScraper
from app.crawl.engine.storage import CrawlStorageManager
from app.crawl.engine.utils import LinkExtractor, RateLimiter
from app.crawl.repository import CrawlRepository

logger = logging.getLogger(__name__)


class CrawlRequestItem:
    """
    Represents a URL request in the BFS queue, holding metadata for retry backoffs.
    """

    def __init__(
        self,
        url: str,
        depth: int,
        retry_count: int = 0,
        next_retry_time: Optional[datetime] = None,
    ) -> None:
        self.url = url
        self.depth = depth
        self.retry_count = retry_count
        self.next_retry_time = next_retry_time


class CrawlerEngine:
    """
    BFS Crawling Engine supporting sequential or concurrent page fetching.
    Manages queues, visited domains, rate limiting, and exponential retry mechanisms.
    """

    def __init__(
        self,
        task_id: int,
        start_urls: List[str],
        max_depth: int,
        max_pages: int,
        concurrency_strategy: str,
        concurrency_limit: int,
        scraper: PlaywrightScraper,
        rate_limiter: RateLimiter,
        storage_manager: CrawlStorageManager,
        allowed_domains: Optional[List[str]] = None,
        allowed_urls: Optional[List[str]] = None,
        max_retries: int = 3,
        retry_backoff_base: float = 2.0,
        retry_backoff_cap: float = 120.0,
        retry_jitter: float = 0.5,
    ) -> None:
        self.task_id = task_id
        self.start_urls = [LinkExtractor.normalize_url(url) for url in start_urls]
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.concurrency_strategy = concurrency_strategy
        self.concurrency_limit = (
            concurrency_limit if concurrency_strategy == "concurrent" else 1
        )
        self.scraper = scraper
        self.rate_limiter = rate_limiter
        self.storage_manager = storage_manager
        self.allowed_domains = allowed_domains
        self.allowed_urls = (
            [LinkExtractor.normalize_url(url) for url in allowed_urls]
            if allowed_urls
            else None
        )
        self.max_retries = max_retries
        self.retry_backoff_base = retry_backoff_base
        self.retry_backoff_cap = retry_backoff_cap
        self.retry_jitter = retry_jitter

        self.queue: asyncio.Queue[CrawlRequestItem] = asyncio.Queue()
        self.visited_urls: Set[str] = set()
        self.retry_list: List[CrawlRequestItem] = []
        self.scraped_count = 0
        self.active_workers = 0
        self.stop_requested = False
        self.lock = asyncio.Lock()

    async def start(self) -> None:
        """
        Executes the crawling pipeline by spawning workers and a retry scheduler.
        """
        logger.info(
            "Starting crawl task ID %d (Strategy: %s, Limit: %d)",
            self.task_id,
            self.concurrency_strategy,
            self.concurrency_limit,
        )

        # Enqueue seed URLs
        for url in self.start_urls:
            self.visited_urls.add(url)
            await self.queue.put(CrawlRequestItem(url=url, depth=0))

        # Start background scheduler to transfer ready retries to the active queue
        scheduler_task = asyncio.create_task(self._retry_scheduler())

        try:
            # Spawn workers
            workers = [
                asyncio.create_task(self._worker(worker_id))
                for worker_id in range(self.concurrency_limit)
            ]
            await asyncio.gather(*workers)
        except Exception as e:
            logger.error(
                "Crawl engine encountered an unexpected execution error: %s", e
            )
            raise
        finally:
            self.stop_requested = True
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass
            logger.info(
                "Crawl task ID %d engine complete. Scraped count: %d",
                self.task_id,
                self.scraped_count,
            )

    async def _retry_scheduler(self) -> None:
        """
        Checks the retry list periodically and pushes expired attempts back to the queue.
        """
        while not self.stop_requested:
            try:
                await asyncio.sleep(1.0)
                now = datetime.now(timezone.utc)
                async with self.lock:
                    ready_items = [
                        item
                        for item in self.retry_list
                        if item.next_retry_time and item.next_retry_time <= now
                    ]
                    for item in ready_items:
                        self.retry_list.remove(item)
                        # Push back into active queue
                        await self.queue.put(item)
                        logger.info(
                            "Retry scheduled for url: %s (attempt %d)",
                            item.url,
                            item.retry_count + 1,
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in retry scheduler task: %s", e)

    async def _worker(self, worker_id: int) -> None:
        """
        Individual worker process pulling tasks from the queue and scraping pages.
        """
        logger.debug("Worker %d starting...", worker_id)
        while not self.stop_requested:
            # 1. Stop early if limit is hit
            if self.scraped_count >= self.max_pages:
                break

            # 2. Check for termination conditions
            # If queue is empty, no workers are active, and no retries are pending, we are done.
            if self.queue.empty() and not self.retry_list and self.active_workers == 0:
                break

            try:
                # Wait briefly for queue elements to avoid busy loop blockages
                item = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            # Double check scrape limit inside lock before processing
            if self.scraped_count >= self.max_pages:
                self.queue.task_done()
                break

            async with self.lock:
                self.active_workers += 1

            try:
                await self.process_url(item)
            except Exception as e:
                logger.error(
                    "Worker %d failed to process URL %s: %s", worker_id, item.url, e
                )
            finally:
                async with self.lock:
                    self.active_workers -= 1
                self.queue.task_done()

            # Yield thread control to keep other APIs responsive
            await asyncio.sleep(0.01)

    async def process_url(self, item: CrawlRequestItem) -> None:
        """
        Executes rate limiting, invokes PlaywrightScraper/PDF extraction, parses HTML links,
        schedules retries, and forwards raw output data to storage.
        """
        # Wait for rate limit permission
        await self.rate_limiter.wait_before_request()

        is_pdf = item.url.lower().endswith(".pdf")
        results = []

        if is_pdf:
            results = await self.scraper.scrape_pdf(item.url)
        else:
            res = await self.scraper.scrape_page(item.url)
            results = [res]

        for result in results:
            status = result.get("status", "failed")
            url = result.get("url", item.url)
            title = result.get("title")
            html_content = result.get("html_content")
            text_content = result.get("text_content")
            status_code = result.get("status_code", 500)
            error_msg = result.get("error")
            screenshot = result.get("screenshot")

            if status == "success":
                # Persist to database & object storage
                await self.storage_manager.store_page(
                    task_id=self.task_id,
                    url=url,
                    title=title,
                    html_content=html_content,
                    text_content=text_content,
                    depth=item.depth,
                    status_code=status_code,
                    status="success",
                    error_log=None,
                    screenshot_bytes=screenshot,
                )
                async with self.lock:
                    self.scraped_count += 1

                # Update count in DB task record
                if self.storage_manager.db:
                    await CrawlRepository.increment_pages_counter(
                        self.storage_manager.db, self.task_id, status="success"
                    )
                else:
                    from app.database import SessionLocal

                    async with SessionLocal() as db_session:
                        await CrawlRepository.increment_pages_counter(
                            db_session, self.task_id, status="success"
                        )

                # Link Discovery: extract outbound links if within max depth limits
                if not is_pdf and item.depth < self.max_depth:
                    discovered = LinkExtractor.extract_links(
                        html_content=html_content,
                        base_url=url,
                        allowed_domains=self.allowed_domains,
                        allowed_urls=self.allowed_urls,
                    )
                    async with self.lock:
                        for link in discovered:
                            if link not in self.visited_urls:
                                self.visited_urls.add(link)
                                await self.queue.put(
                                    CrawlRequestItem(url=link, depth=item.depth + 1)
                                )
                                logger.debug("Discovered and enqueued: %s", link)

            else:
                # Handle Failure & Retries
                logger.warning(
                    "Scrape execution failed for: %s (Error: %s)", url, error_msg
                )

                # Persist failure record into DB for logs
                await self.storage_manager.store_page(
                    task_id=self.task_id,
                    url=url,
                    title=None,
                    html_content=None,
                    text_content=None,
                    depth=item.depth,
                    status_code=status_code,
                    status="failed",
                    error_log=error_msg,
                    screenshot_bytes=None,
                )

                # Schedule retry if within limits
                if item.retry_count < self.max_retries:
                    item.retry_count += 1
                    # Compute exponential backoff time with jitter
                    backoff = min(
                        self.retry_backoff_base * (2 ** (item.retry_count - 1)),
                        self.retry_backoff_cap,
                    )
                    jitter = random.uniform(0, self.retry_jitter)
                    wait_time = backoff + jitter

                    item.next_retry_time = datetime.now(timezone.utc) + timedelta(
                        seconds=wait_time
                    )

                    async with self.lock:
                        self.retry_list.append(item)
                    logger.warning(
                        "Scheduled retry #%d for URL %s in %.2fs",
                        item.retry_count,
                        url,
                        wait_time,
                    )
                else:
                    logger.error(
                        "Max retries reached for URL: %s. Dropping request.", url
                    )
                    if self.storage_manager.db:
                        await CrawlRepository.increment_pages_counter(
                            self.storage_manager.db, self.task_id, status="failed"
                        )
                    else:
                        from app.database import SessionLocal

                        async with SessionLocal() as db_session:
                            await CrawlRepository.increment_pages_counter(
                                db_session, self.task_id, status="failed"
                            )
