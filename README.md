# ChatBot API & Crawling Service

This repository provides a secure, async-first ChatBot API containing:
1. **Authentication Service**: Stateful API key generation (`sk_live_...`), SHA-256 async hashing, and role-based access control (RBAC).
2. **Crawling Service**: Production-grade Playwright BFS crawler that handles dynamic JavaScript-rendered pages and PDFs, respects rates, utilizes exponential backoff retries, and stores files modularly.
3. **Ingestion & Versioning Service**: Processes crawled pages and manual uploads, splits text using a token-aware chunker, structures HTML tables row-by-row, and syncs chunks to a vector search index with Redis FAQ caching.
4. **Global Object Storage**: Reusable object storage backend that Simulates S3/MinIO bucket hierarchies locally on disk.

---

## Documentation

For detailed guides on routes, APIs, and custom scraping selectors:
- **[Authentication Service Routes](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/docs/routes/auth.md)**
- **[Crawling Service Routes](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/docs/routes/crawl.md)**
- **[Ingestion Service Routes](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/docs/routes/ingest.md)**
- **[Selector Configurations Guide](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/docs/selectors_guide.md)**

---

## Workspace Directory Layout

Here are the key modules and directories of the project:

-   [pyproject.toml](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/pyproject.toml) - Project package declarations and dependency locks.
-   [Dockerfile](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/Dockerfile) - Optimization-caching Docker recipe running Python 3.13 and Playwright Chromium.
-   [docker-compose-dev.yml](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/docker-compose-dev.yml) - Local development stack (Database, Redis, MinIO, CloudBeaver, ChatBot App).
-   [app/](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/app) - Primary application codebase.
    -   [app/configs/](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/app/configs) - Global configurations (Database, Auth settings, and Crawler settings).
    -   [app/storage/](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/app/storage) - Reusable global Object Storage drivers ([LocalObjectStorage](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/app/storage/local.py)).
    -   [app/vector_store/](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/app/vector_store) - Modular vector search adapters ([PGVectorStore](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/app/vector_store/pgvector.py)).
    -   [app/auth/](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/app/auth) - API Key and credentials authorization logic.
    -   [app/crawl/](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/app/crawl) - Core Crawling service.
        -   [app/crawl/selectors.yaml](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/app/crawl/selectors.yaml) - Site-specific parsing config overrides.
        -   [app/crawl/engine/scraper.py](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/app/crawl/engine/scraper.py) - Playwright execution & PDF extractor.
        -   [app/crawl/engine/crawler.py](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/app/crawl/engine/crawler.py) - BFS scheduler & retry queues.
    -   [app/ingest/](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/app/ingest) - Ingestion service managing parser, chunker, and deduplication sync.
-   [docs/](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/docs) - Architectural Decision Records (ADRs) and developer documents.
    -   [docs/decisions/ADR-003-modular-playwright-crawler.md](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/docs/decisions/ADR-003-modular-playwright-crawler.md) - Design rationale for the crawl service.
-   [tests/](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/tests) - Full automated test suite.
    -   [tests/auth/](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/tests/auth) - Credentials security tests.
    -   [tests/crawl/test_crawler.py](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/tests/crawl/test_crawler.py) - Crawl integration test suites.
    -   [tests/ingest/test_ingest.py](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/tests/ingest/test_ingest.py) - Ingest and table parser tests.

---

## Quick Start Guide

### 1. Setup Environment
Create a [.env](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/.env) file at the root:
```ini
DB_TYPE=sqlite
DB_NAME=chatbot.db
MODE=DEBUG
```

### 2. Install and Initialize (Local Run)
Make sure `uv` is installed, then synchronize package dependencies and download Playwright browsers:
```bash
# Sync packages
uv sync

# Install chromium browsers
uv run playwright install chromium
```

Start the FastAPI application:
```bash
uv run uvicorn main:app --reload
```

---

## Running in Docker Containers

To spin up the complete development stack (FastAPI server, PostgreSQL database, MinIO object storage, and CloudBeaver db browser UI):
```bash
docker compose -f docker-compose-dev.yml up -d --build
```
The FastAPI server will be accessible at `http://localhost:8000`. You can inspect the Swagger REST documentation at `http://localhost:8000/docs`.

### Fresh Application Restart (No Volume History)
To completely delete all volume history (clearing PostgreSQL databases, Redis cache indices, and MinIO object buckets) and launch a completely clean setup:
```bash
# 1. Stop active containers and remove associated volumes
docker compose -f docker-compose-dev.yml down -v

# 2. (Optional) Remove local storage file backups from disk
# On Windows (PowerShell):
Remove-Item -Path storage_buckets -Recurse -Force
# On Linux/macOS:
rm -rf storage_buckets

# 3. Rebuild and launch containers fresh
docker compose -f docker-compose-dev.yml up -d --build
```


---

## Test Executions

We support two execution environments for automated testing:

1.  **Fast Mock Mode (CI / Production Default)**:
    Runs all tests locally and instantly by mock patching the browser network fetches:
    ```bash
    uv run pytest
    ```
2.  **Live Browser Integration Mode (Local Debug)**:
    Spins up the **actual Playwright browser** to fetch targets, save screenshots, and verify object files:
    ```powershell
    $env:MODE="DEBUG"
    uv run pytest -s tests/crawl/test_crawler.py
    ```
