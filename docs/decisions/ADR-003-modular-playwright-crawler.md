# ADR-003: Modular Playwright-Based Web Crawler and Global Object Storage

## Status
Accepted

## Context
The application needs to dynamically scrape and crawl websites (like bank rate sheets) and download PDFs to ingest content. The legacy scraper suffered from:
1. **Tight Coupling**: The scraper logic was hardcoded inside the crawl loop, preventing scraper swapping or mocking.
2. **Hardcoded Selectors**: Elements like loaders and content containers were hardcoded constants.
3. **Blocking Execution**: Long crawls held database connections open and blocked the Python event loop, risking chatbot downtime.
4. **Poor Retries**: Retries were managed in-memory with custom sleeps, risking loss of state and rate-limit violations.

Therefore, we required a production-grade, extensible, and non-blocking crawling architecture.

## Options Considered
- **Option 1: Lightweight HTTP Scraper (HTTPX + BeautifulSoup/lxml)**: Extremely fast and low-resource, but fails on dynamic websites relying on client-side JavaScript rendering.
- **Option 2: Decoupled Playwright-Based Crawler with Pluggable Storage**: Spawns headless browser pages to capture dynamic JS sites, utilizes YAML configuration for custom domain selectors, and writes to a pluggable global object storage layer.

## Decision
We selected **Option 2: Decoupled Playwright-Based Crawler with Pluggable Storage**.

1. **Playwright Scraper**: Handles dynamic rendering. Tracks tracker domains (analytics, fonts) and aborts them, while loading images/stylesheets for rendering screenshots.
2. **YAML Selector Configuration**: Selectors (e.g. `content_selector`, `loader_selector`, `min_content_length`, and `timeout_ms`) are loaded dynamically from `app/crawl/selectors.yaml` by matching domain hostnames, supporting generalized scraping.
3. **Global Object Storage (`app/storage/`)**: Created a reusable global storage interface (`BaseObjectStorage`) and filesystem simulation (`LocalObjectStorage`) that stores raw HTML and screenshots under folder hierarchies:
   ```
   crawls/tasks/{task_id}/html/{page_id}.html
   crawls/tasks/{task_id}/screenshots/{page_id}.jpeg
   ```
4. **Non-Blocking Orchestrator**:
   - Spawns crawler workers in background threads using FastAPI `BackgroundTasks`.
   - Restricts active browsers globally using an async `Semaphore(max_concurrent_crawls)`.
   - Yields thread control (`await asyncio.sleep(0.01)`) in queue loops.
   - Instantiates, commits, and closes DB sessions dynamically per request instead of holding a connection open for the crawl duration.
5. **Conditional Test Execution**:
   - By default, unit tests patch Playwright with an `AsyncMock` to keep CI/CD test runs fast and isolated.
   - If the environment variable `MODE` is set to `"DEBUG"` or `"STAGING"`, the test suite runs the actual browser crawl and assertions on a live target.

## Consequences
- **Pros**:
  - Successfully parses JS-rendered websites and extracts full-fidelity screenshots.
  - decopled architecture makes it easy to add alternative scrapers (e.g. static HTTPX scraper) or cloud storage backends (e.g. S3/MinIO).
  - Keeps the chatbot server highly responsive under load.
  - Conditional test executions provide robust debugging options.
- **Cons**:
  - Playwright browser execution consumes significant system memory and CPU relative to lightweight requests. This is mitigated by global semaphore limits.
