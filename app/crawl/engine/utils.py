import asyncio
import random
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse
import lxml.html


class LinkExtractor:
    """
    Decoupled helper to extract URLs and clean text/title from HTML content.
    Utilizes lxml for highly optimized DOM tree parsing.
    """

    @staticmethod
    def extract_links(
        html_content: str,
        base_url: str,
        allowed_domains: Optional[List[str]] = None,
        allowed_urls: Optional[List[str]] = None,
    ) -> List[str]:
        """
        Parses HTML, extracts outbound anchor tags, normalizes links (e.g. stripping fragments),
        and applies domain or prefix validation.
        """
        if not html_content:
            return []

        try:
            dom = lxml.html.fromstring(html_content.encode("utf-8"))
        except Exception:
            return []

        links = []
        for a_tag in dom.xpath("//a[@href]"):
            href = a_tag.get("href").strip()
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue

            full_url = urljoin(base_url, href)
            # Remove any fragment identifiers
            full_url = full_url.split("#")[0]
            parsed = urlparse(full_url)

            # Only follow http and https links.
            if parsed.scheme not in ("http", "https"):
                continue

            # Apply the URL prefix allow-list if configured.
            if allowed_urls:
                is_allowed = False
                for prefix in allowed_urls:
                    if full_url.startswith(str(prefix)):
                        is_allowed = True
                        break
                if not is_allowed:
                    continue

            # Apply the domain allow-list if configured.
            elif allowed_domains:
                domain = parsed.netloc.lower()

                def normalise(d: str) -> str:
                    return d[4:] if d.startswith("www.") else d

                norm_domain = normalise(domain)
                is_allowed = False
                for allowed_d in allowed_domains:
                    if norm_domain == normalise(allowed_d.lower()):
                        is_allowed = True
                        break
                if not is_allowed:
                    continue

            links.append(full_url)

        return list(set(links))

    @staticmethod
    def clean_text_and_title(html_content: str) -> Tuple[str, str]:
        """
        Extracts document title and a cleaned body text, omitting structural elements,
        styles, stylesheets, scripts, templates, and iframe contents.
        """
        if not html_content:
            return "", ""

        try:
            dom = lxml.html.fromstring(html_content.encode("utf-8"))
        except Exception:
            return "", ""

        # Extract title
        title_el = dom.find(".//title")
        title = title_el.text.strip() if title_el is not None and title_el.text else ""

        # Remove irrelevant elements that skew content matching
        for tag in ["script", "style", "head", "noscript", "iframe", "svg"]:
            for element in dom.xpath(f"//{tag}"):
                element.getparent().remove(element)

        # Extract text content and normalize whitespace
        text_content = dom.text_content()
        cleaned_text = " ".join(text_content.split())
        return cleaned_text, title


class RateLimiter:
    """
    Polite sliding-window rate limiter preventing IP bans.
    Ensures safe gap delays and checks minute/hour sliding caps using non-blocking async waits.
    """

    def __init__(
        self,
        base_delay: float = 1.0,
        random_delay: float = 0.5,
        max_requests_per_minute: int = 20,
        max_requests_per_hour: int = 200,
    ) -> None:
        self.base_delay = base_delay
        self.random_delay = random_delay
        self.max_requests_per_minute = max_requests_per_minute
        self.max_requests_per_hour = max_requests_per_hour

        self.last_request_time: Optional[datetime] = None
        self.minute_window: deque[datetime] = deque()
        self.hour_window: deque[datetime] = deque()
        self.lock = asyncio.Lock()

    async def wait_before_request(self) -> None:
        """
        Blocks execution asynchronously until all three rate-limit layers are satisfied.
        """
        async with self.lock:
            now = datetime.now(timezone.utc)

            # 1. Minimum inter-request interval with jitter
            if self.last_request_time is not None:
                elapsed = (now - self.last_request_time).total_seconds()
                jitter = random.uniform(0, self.random_delay)
                required = self.base_delay + jitter
                gap = required - elapsed
                if gap > 0:
                    await asyncio.sleep(gap)
                    now = datetime.now(timezone.utc)

            # 2. Sliding window check
            while True:
                one_minute_ago = now - timedelta(seconds=60)
                one_hour_ago = now - timedelta(seconds=3600)

                # Prune old timestamps
                while self.minute_window and self.minute_window[0] < one_minute_ago:
                    self.minute_window.popleft()
                while self.hour_window and self.hour_window[0] < one_hour_ago:
                    self.hour_window.popleft()

                # If windows have capacity, break
                if (
                    len(self.minute_window) < self.max_requests_per_minute
                    and len(self.hour_window) < self.max_requests_per_hour
                ):
                    break

                # Otherwise, determine sleep target duration
                sleep_duration = 0.1
                if len(self.minute_window) >= self.max_requests_per_minute:
                    resumes_at = self.minute_window[0] + timedelta(seconds=60)
                    sleep_duration = max(sleep_duration, (resumes_at - now).total_seconds())

                if len(self.hour_window) >= self.max_requests_per_hour:
                    resumes_at = self.hour_window[0] + timedelta(seconds=3600)
                    sleep_duration = max(sleep_duration, (resumes_at - now).total_seconds())

                # Add a small buffer to avoid off-by-one timestamp comparisons
                await asyncio.sleep(sleep_duration + 0.05)
                now = datetime.now(timezone.utc)

            # Record request
            self.last_request_time = now
            self.minute_window.append(now)
            self.hour_window.append(now)
