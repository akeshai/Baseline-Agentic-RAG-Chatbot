import os
import shutil
from contextlib import nullcontext
from unittest.mock import AsyncMock, patch
import pytest
from app.configs.crawl import settings as crawl_settings
from app.storage.local import LocalObjectStorage

# Helper to register and create API key
def get_auth_headers(client) -> dict:
    register_payload = {
        "name": "Crawl Tester",
        "user_id": "crawl_tester",
        "email": "crawl_tester@example.com",
        "password": "password123",
        "role": "user",
    }
    client.post("/auth/register", json=register_payload)

    key_payload = {
        "key_in": {"name": "Test Key"},
        "login_req": {"email": "crawl_tester@example.com", "password": "password123"},
    }
    response = client.post("/auth/api-keys", json=key_payload)
    plain_key = response.json()["plain_key"]
    return {"X-API-Key": plain_key}


def get_scraper_patch():
    """
    Decides whether to mock the PlaywrightScraper based on the environment 'MODE'.
    - If MODE is 'DEBUG' or 'STAGING', it returns a nullcontext (launches actual Playwright).
    - If MODE is 'PRODUCTION' or unset, it patches PlaywrightScraper with a Mock.
    """
    mode = os.getenv("MODE", "PRODUCTION").strip("'\" ")
    if mode in ("DEBUG", "STAGING"):
        return nullcontext(), None
    else:
        mock_scraper = AsyncMock()
        mock_scraper.__aenter__.return_value = mock_scraper
        return patch("app.crawl.service.PlaywrightScraper", return_value=mock_scraper), mock_scraper


def test_selectors_yaml_file_exists():
    """Verify that selectors.yaml is present and contains expected keys."""
    from pathlib import Path
    import yaml

    path = Path(__file__).parent.parent.parent / "app" / "crawl" / "selectors.yaml"
    assert path.exists(), "selectors.yaml does not exist"
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    assert "default" in config
    assert "domains" in config


def test_create_crawl_task_db_storage(client):
    """
    Verifies that a crawl task runs successfully and stores page contents directly in the database
    when crawl_settings.raw_storage_type is 'db'.
    """
    patcher, mock_scraper = get_scraper_patch()
    with patcher:
        if mock_scraper:
            # Mock return values for fast testing
            mock_scraper.scrape_page.return_value = {
                "url": "https://example.com",
                "title": "Example Domain",
                "html_content": "<html><body><h1>Hello World</h1><a href='/about'>About</a></body></html>",
                "text_content": "Hello World About",
                "status_code": 200,
                "screenshot": b"fake_screenshot_bytes",
                "status": "success",
                "error": None,
            }

        headers = get_auth_headers(client)
        payload = {
            "urls": ["https://example.com"],
            "max_depth": 1,
            "max_pages": 2,
            "strategy": "recursive",
            "concurrency_strategy": "single",
        }

        # Trigger crawl
        response = client.post("/crawl", json=payload, headers=headers)
        assert response.status_code == 201
        data = response.json()
        assert data["status"] in ("pending", "running", "completed")
        assert data["start_url"].startswith("https://example.com")
        task_id = data["id"]

        # Retrieve task details to verify background completion
        task_resp = client.get(f"/crawl/tasks/{task_id}", headers=headers)
        assert task_resp.status_code == 200
        task_data = task_resp.json()
        assert task_data["status"] == "completed"
        assert task_data["pages_crawled"] >= 1

        # List task pages
        pages_resp = client.get(f"/crawl/tasks/{task_id}/pages", headers=headers)
        assert pages_resp.status_code == 200
        pages_list = pages_resp.json()
        assert len(pages_list) >= 1
        page_id = pages_list[0]["id"]

        # Fetch page detail and ensure HTML is stored in DB
        page_detail_resp = client.get(f"/crawl/pages/{page_id}", headers=headers)
        assert page_detail_resp.status_code == 200
        page_detail = page_detail_resp.json()

        if mock_scraper:
            assert page_detail["title"] == "Example Domain"
            assert "Hello World" in page_detail["html_content"]
            assert "Hello World About" in page_detail["text_content"]
        else:
            # Assertion for actual page content fetched from example.com
            assert "Example Domain" in page_detail["title"]
            assert "documentation examples" in page_detail["text_content"]


def test_create_crawl_task_object_storage(client):
    """
    Verifies that a crawl task runs successfully and offloads raw HTML to global Object Storage
    when crawl_settings.raw_storage_type is 'object'.
    """
    bucket_dir = "test_storage_buckets"
    if os.path.exists(bucket_dir):
        shutil.rmtree(bucket_dir)

    patcher, mock_scraper = get_scraper_patch()
    with patcher:
        if mock_scraper:
            mock_scraper.scrape_page.return_value = {
                "url": "https://example.com/object",
                "title": "Object Domain",
                "html_content": "<html><body><h1>Object Storage test</h1></body></html>",
                "text_content": "Object Storage test",
                "status_code": 200,
                "screenshot": b"fake_screenshot_bytes_jpeg",
                "status": "success",
                "error": None,
            }

        with patch.object(crawl_settings, "raw_storage_type", "object"):
            with patch.object(crawl_settings, "object_storage_root", bucket_dir):
                headers = get_auth_headers(client)
                payload = {
                    "urls": ["https://example.com"],
                    "max_depth": 0,
                    "max_pages": 1,
                    "strategy": "single",
                    "concurrency_strategy": "single",
                }

                # Trigger crawl
                response = client.post("/crawl", json=payload, headers=headers)
                assert response.status_code == 201
                task_id = response.json()["id"]

                # Verify task completed
                task_resp = client.get(f"/crawl/tasks/{task_id}", headers=headers)
                assert task_resp.json()["status"] == "completed"

                # Check pages list
                pages_resp = client.get(f"/crawl/tasks/{task_id}/pages", headers=headers)
                pages = pages_resp.json()
                assert len(pages) == 1
                page_id = pages[0]["id"]

                # Fetch page detail
                page_detail_resp = client.get(f"/crawl/pages/{page_id}", headers=headers)
                assert page_detail_resp.status_code == 200
                page_detail = page_detail_resp.json()

                if mock_scraper:
                    assert "Object Storage test" in page_detail["html_content"]
                    assert "Object Storage test" in page_detail["text_content"]
                else:
                    assert "Example Domain" in page_detail["title"]
                    assert "documentation examples" in page_detail["text_content"]

                # Verify file structure on disk simulating bucket layout
                task_folder = os.path.join(bucket_dir, "crawls", "tasks", str(task_id))
                assert os.path.exists(os.path.join(task_folder, "html")), "HTML directory not created"
                assert os.path.exists(os.path.join(task_folder, "screenshots")), "Screenshot directory not created"

    # Clean up test directories
    if os.path.exists(bucket_dir):
        shutil.rmtree(bucket_dir)


def test_crawler_exponential_backoff_retry(client):
    """
    Tests that a failed scrape request triggers exponential backoff retries,
    saves the failure log, and increments the failed page counter.
    """
    patcher, mock_scraper = get_scraper_patch()
    with patcher:
        if mock_scraper:
            # Make scraping always fail
            mock_scraper.scrape_page.return_value = {
                "url": "https://failing-domain.com",
                "title": None,
                "html_content": None,
                "text_content": None,
                "status_code": 503,
                "screenshot": None,
                "status": "failed",
                "error": "Service Unavailable",
            }
            target_url = "https://failing-domain.com"
        else:
            # Choose a domain that will trigger a network/DNS resolution failure
            target_url = "https://thisdomaindoesnotexistatall12345.com"

        # Shorten backoff durations for fast testing
        from app.crawl.engine.crawler import CrawlerEngine
        original_init = CrawlerEngine.__init__

        def mock_engine_init(self, *args, **kwargs):
            kwargs["max_retries"] = 1
            kwargs["retry_backoff_base"] = 0.1
            kwargs["retry_jitter"] = 0.0
            original_init(self, *args, **kwargs)

        headers = get_auth_headers(client)
        payload = {
            "urls": [target_url],
            "max_depth": 0,
            "max_pages": 1,
            "strategy": "single",
            "concurrency_strategy": "single",
        }

        with patch.object(CrawlerEngine, "__init__", mock_engine_init):
            response = client.post("/crawl", json=payload, headers=headers)
            assert response.status_code == 201
            task_id = response.json()["id"]

            # Fetch task details
            task_resp = client.get(f"/crawl/tasks/{task_id}", headers=headers)
            task_data = task_resp.json()
            assert task_data["status"] == "completed"
            assert task_data["pages_failed"] >= 1

            # Check pages list
            pages_resp = client.get(f"/crawl/tasks/{task_id}/pages", headers=headers)
            pages = pages_resp.json()
            # Verify page is stored with status failed
            assert len(pages) >= 1
            assert pages[0]["status"] == "failed"
            if mock_scraper:
                assert "Service Unavailable" in pages[0]["error_log"]
            else:
                assert pages[0]["error_log"] is not None


def test_crawler_headless_mode_env_behavior():
    """
    Verifies that CrawlService sets headless=False when MODE is 'DEBUG' or 'STAGING',
    and headless=True in other modes (like PRODUCTION).
    """
    from app.crawl.service import CrawlService
    import asyncio

    # Patch PlaywrightScraper class so we do not actually launch browser process in this unit test
    with patch("app.crawl.service.PlaywrightScraper") as mock_scraper_class:
        # Mock DB sessions and repo calls
        from unittest.mock import MagicMock
        with patch("app.crawl.service.SessionLocal"), \
             patch("app.crawl.service.CrawlRepository", new_callable=AsyncMock), \
             patch("app.crawl.service.CrawlerEngine", new_callable=MagicMock) as mock_engine_class:
             
             mock_engine = AsyncMock()
             mock_engine_class.return_value = mock_engine
             mock_scraper_class.return_value = AsyncMock()
             mock_scraper_class.return_value.__aenter__.return_value = AsyncMock()
             
             # Case 1: MODE is 'DEBUG' -> should configure headless=False
             with patch.dict(os.environ, {"MODE": "DEBUG"}):
                 asyncio.run(CrawlService.run_crawl_background(
                     task_id=999,
                     urls=["https://example.com"],
                     max_depth=0,
                     max_pages=1,
                     strategy="single",
                     concurrency_strategy="single",
                     concurrency_limit=1
                 ))
                 mock_scraper_class.assert_called_with(headless=False)
             
             mock_scraper_class.reset_mock()
             
             # Case 2: MODE is 'STAGING' -> should configure headless=False
             with patch.dict(os.environ, {"MODE": "STAGING"}):
                 asyncio.run(CrawlService.run_crawl_background(
                     task_id=999,
                     urls=["https://example.com"],
                     max_depth=0,
                     max_pages=1,
                     strategy="single",
                     concurrency_strategy="single",
                     concurrency_limit=1
                 ))
                 mock_scraper_class.assert_called_with(headless=False)
                 
             mock_scraper_class.reset_mock()
             
             # Case 3: MODE is 'PRODUCTION' -> should configure headless=True
             with patch.dict(os.environ, {"MODE": "PRODUCTION"}):
                 asyncio.run(CrawlService.run_crawl_background(
                     task_id=999,
                     urls=["https://example.com"],
                     max_depth=0,
                     max_pages=1,
                     strategy="single",
                     concurrency_strategy="single",
                     concurrency_limit=1
                 ))
                 mock_scraper_class.assert_called_with(headless=True)
