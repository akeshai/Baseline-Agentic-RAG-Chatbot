import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import httpx
import pdfplumber
import yaml
from playwright.async_api import Browser, Page, Playwright, async_playwright

logger = logging.getLogger(__name__)

# List of tracker/font domains to block for performance
BLOCKED_DOMAINS = [
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "googletagmanager.com",
    "google-analytics.com",
    "clarity.ms",
    "doubleclick.net",
    "facebook.net",
]

PDF_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class PlaywrightScraper:
    """
    Production-grade dynamic scraper using Playwright.
    Configurable via app/crawl/selectors.yaml per domain.
    Extracts HTML, cleaned text, and screenshots.
    """

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.selectors_config: Dict[str, Any] = {}
        self._load_selectors_config()

    def _load_selectors_config(self) -> None:
        """
        Loads the YAML configurations for selectors.
        """
        config_path = Path(__file__).parent.parent / "selectors.yaml"
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    self.selectors_config = yaml.safe_load(f) or {}
                logger.info("Loaded selectors configuration from %s", config_path)
            except Exception as e:
                logger.error("Failed to parse selectors.yaml: %s", e)
        else:
            logger.warning(
                "selectors.yaml not found at %s. Using internal defaults.", config_path
            )

    def _get_site_config(self, url: str) -> Dict[str, Any]:
        """
        Retrieves selector and timeout overrides for a domain.
        """
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        # Check domain configuration overrides
        domains_list = self.selectors_config.get("domains", [])
        for entry in domains_list:
            target_domain = entry.get("domain", "").lower()
            # Direct match or subdomain match (e.g. support.dcb.bank.in matches dcb.bank.in)
            if domain == target_domain or domain.endswith("." + target_domain):
                return {**self.selectors_config.get("default", {}), **entry}

        return self.selectors_config.get(
            "default",
            {
                "content_selector": "body",
                "loader_selector": None,
                "min_content_length": 100,
                "timeout_ms": 30000,
                "wait_for_visible": None,
            },
        )

    async def __aenter__(self) -> "PlaywrightScraper":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def start(self) -> None:
        """
        Launches the browser.
        """
        if not self.browser:
            logger.info("Initializing Playwright...")
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=self.headless,
                args=["--disable-dev-shm-usage", "--no-sandbox"],
            )

    async def close(self) -> None:
        """
        Disposes browser resources.
        """
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None
        logger.info("Playwright browser disposed.")

    async def _setup_page(self) -> Page:
        """
        Creates a clean browser context and page, adding interceptors to abort tracking domains.
        """
        if not self.browser:
            raise RuntimeError(
                "Browser not initialized. Call start() or use async context manager."
            )

        context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=PDF_USER_AGENT,
        )
        page = await context.new_page()

        # Intercept and abort fonts/trackers for performance, while leaving stylesheets and images intact
        async def block_trackers(route):
            url = route.request.url.lower()
            if any(blocked in url for blocked in BLOCKED_DOMAINS):
                logger.debug("Aborted tracking request: %s", url)
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", block_trackers)
        return page

    async def scrape_page(self, url: str) -> Dict[str, Any]:
        """
        Scrapes a dynamic page, waiting for loaders, selectors, and content stabilization.
        Returns a dictionary containing raw HTML, title, inner text, and screenshot bytes.
        """
        page = await self._setup_page()
        config = self._get_site_config(url)

        timeout_ms = config.get("timeout_ms", 30000)
        content_selector = config.get("content_selector", "body")
        loader_selector = config.get("loader_selector")
        wait_for_visible = config.get("wait_for_visible")
        min_length = config.get("min_content_length", 100)

        page.set_default_timeout(timeout_ms)

        try:
            logger.info("Navigating to %s (timeout: %dms)", url, timeout_ms)
            # Load page DOM
            response = await page.goto(
                url, wait_until="domcontentloaded", timeout=timeout_ms
            )
            status_code = response.status if response else 200

            # 1. Wait for loader to disappear if configured
            if loader_selector:
                try:
                    await page.wait_for_selector(
                        loader_selector, state="hidden", timeout=15000
                    )
                except Exception:
                    logger.debug(
                        "Loader selector %s wait timed out or element not found.",
                        loader_selector,
                    )

            # 2. Wait for target content visibility if configured
            if wait_for_visible:
                await page.wait_for_selector(
                    wait_for_visible, state="visible", timeout=15000
                )

            # 3. Poll for text content stabilization
            text_content = await self._poll_until_stable(
                page, content_selector, min_length, timeout_ms
            )

            # 4. Capture screenshot
            screenshot_bytes = await page.screenshot(
                full_page=True,
                type="jpeg",
                quality=60,
                timeout=10000,
            )

            # Get title and HTML
            title = await page.title()
            html_content = await page.content()

            return {
                "url": url,
                "title": title.strip(),
                "html_content": html_content,
                "text_content": text_content,
                "status_code": status_code,
                "screenshot": screenshot_bytes,
                "status": "success",
                "error": None,
            }

        except Exception as e:
            logger.error("Error scraping page %s: %s", url, e)
            return {
                "url": url,
                "title": None,
                "html_content": None,
                "text_content": None,
                "status_code": 500,
                "screenshot": None,
                "status": "failed",
                "error": str(e),
            }
        finally:
            context = page.context
            await page.close()
            await context.close()

    async def _poll_until_stable(
        self, page: Page, selector: str, min_length: int, max_time_ms: int
    ) -> str:
        """
        Polls the inner text of a selector until its character count stabilizes.
        """
        elapsed_ms = 0
        poll_interval_ms = 500
        last_length = 0
        stable_cycles = 0
        text = ""

        # Clamp max poll duration to 10 seconds to avoid infinite waits
        max_poll_ms = min(max_time_ms, 10000)

        while elapsed_ms < max_poll_ms:
            try:
                # If target element is not available, default to body
                text = await page.inner_text(selector)
            except Exception:
                try:
                    text = await page.inner_text("body")
                except Exception:
                    text = ""

            current_length = len(text.strip())

            if current_length >= min_length:
                if current_length == last_length:
                    stable_cycles += 1
                else:
                    stable_cycles = 0

                if stable_cycles >= 3:  # Stable for 1.5 seconds
                    break

            last_length = current_length
            await asyncio.sleep(poll_interval_ms / 1000.0)
            elapsed_ms += poll_interval_ms

        return text.strip()

    async def scrape_pdf(self, url: str) -> List[Dict[str, Any]]:
        """
        Downloads a PDF and extracts text page-by-page.
        Limits file sizes to 50MB and pages to 100 to prevent CPU/memory hogging.
        """
        logger.info("Scraping PDF document: %s", url)
        try:
            # Check file size with HEAD request
            async with httpx.AsyncClient(
                timeout=15.0, headers={"User-Agent": PDF_USER_AGENT}
            ) as client:
                head = await client.head(url)
                size_header = head.headers.get("content-length")
                if size_header and size_header.isdigit():
                    size_bytes = int(size_header)
                    if size_bytes > 50 * 1024 * 1024:  # 50MB
                        raise ValueError(
                            f"PDF exceeds maximum file size limit (50MB): {size_bytes} bytes"
                        )

            # Download PDF bytes
            async with httpx.AsyncClient(
                timeout=60.0, headers={"User-Agent": PDF_USER_AGENT}
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                pdf_data = resp.content

            # Stream to temp file and parse page-by-page using pdfplumber
            parsed_pages = []
            parsed_url = urlparse(url)
            filename = os.path.basename(parsed_url.path) or "document.pdf"

            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp:
                temp.write(pdf_data)
                temp_path = temp.name

            try:
                with pdfplumber.open(temp_path) as pdf:
                    total_pages = len(pdf.pages)
                    if total_pages > 100:
                        logger.warning(
                            "PDF %s contains %d pages (limit: 100). Skipping.",
                            url,
                            total_pages,
                        )
                        return []

                    for page_idx, page in enumerate(pdf.pages, start=1):
                        text = page.extract_text() or ""
                        parsed_pages.append(
                            {
                                "url": f"{url}#page={page_idx}",
                                "title": f"{filename} - Page {page_idx}",
                                "html_content": None,
                                "text_content": text.strip(),
                                "status_code": 200,
                                "screenshot": None,
                                "status": "success",
                                "error": None,
                                "pdf_metadata": {
                                    "page_number": page_idx,
                                    "total_pages": total_pages,
                                },
                            }
                        )
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

            return parsed_pages

        except Exception as e:
            logger.error("Failed to parse PDF from %s: %s", url, e)
            return [
                {
                    "url": url,
                    "title": None,
                    "html_content": None,
                    "text_content": None,
                    "status_code": 500,
                    "screenshot": None,
                    "status": "failed",
                    "error": str(e),
                }
            ]
