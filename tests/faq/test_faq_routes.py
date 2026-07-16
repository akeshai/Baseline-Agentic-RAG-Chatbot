from unittest.mock import patch
import pytest
from app.faq.repository import FAQRepository


@pytest.mark.anyio
async def test_faq_routes_complete_flow(client):
    # Setup: Clear Redis cache for targeted categories to ensure absolute test isolation
    repo = FAQRepository()
    await repo.delete_faqs_by_category("gold_loan")
    await repo.delete_faqs_by_category("fixed_deposit_interest_rate")
    """
    Tests the complete lifecycle of FAQ Redis cache querying and management:
    1. Authenticate via X-API-Key.
    2. Ingest two FAQ documents belonging to different categories (Gold Loan & FD).
    3. Search for a specific question (O(1) lookup).
    4. Fetch all categories and verify count summary aggregation.
    5. Fetch category questions (both summary keys and full objects).
    6. Delete all FAQs in one category (Gold Loan) and assert it is removed.
    7. Assert that the other category (FD) remains active and untouched.
    """
    with (
        patch("app.ingest.service.FAQCache._cache_faqs_sync"),
        patch("app.ingest.service.FAQCache._evict_faqs_sync"),
    ):
        # We patch the sync handlers in the test suite so they bypass live Redis
        # only if we mock it, but wait: the tests in tests/test_adapters.py run against
        # real databases or standard mock contexts. Wait, we want to test real Redis/relational
        # databases during integration tests if they are configured, but for pytest-anyio
        # unit test speed, they can mock or run against the test Redis.
        # Let's run against the local Redis client directly since we have it running in docker
        # or mock it if needed. Wait! All other unit tests patch redis/FAQCache sync handlers
        # to avoid network traffic or they don't?
        # Let's look at test_ingest.py: it patches FAQCache sync handlers so it doesn't fail if Redis is down.
        # But wait! If we patch it, we cannot test the Redis lookups because FAQCache won't write to Redis!
        # Ah! To test the real FAQRepository endpoints, we want the real Redis client!
        # Wait, if we use a mock redis client or test against a real local Redis instance:
        # The pytest suite executes with a running local redis container in development!
        # Let's check: does the test environment have a running Redis?
        # Yes, chatbot-redis-1 is running and healthy!
        # So we do NOT need to patch FAQCache! We can test against the real local Redis directly!
        pass

    # 1. Register user and generate API key
    register_payload = {
        "name": "FAQ Route Tester",
        "user_id": "faq_tester",
        "email": "faq_tester@example.com",
        "password": "password123",
        "role": "user",
    }
    client.post("/auth/register", json=register_payload)

    key_payload = {
        "key_in": {"name": "FAQ Test Key"},
        "login_req": {"email": "faq_tester@example.com", "password": "password123"},
    }
    key_resp = client.post("/auth/api-keys", json=key_payload)
    assert key_resp.status_code == 201
    headers = {"X-API-Key": key_resp.json()["plain_key"]}

    # 2. Ingest FAQ Document A (Category: gold_loan)
    url_a = "https://www.dcb.bank.in/loans/gold-loan"
    ingest_resp_a = client.post(
        "/ingest/text",
        json={
            "source_identifier": url_a,
            "title": "Gold Loan FAQ Page",
            "text_content": "Q: What is the Gold Loan rate?\nA: The Gold Loan interest rate starts from 9.5% p.a.",
            "is_html": False
        },
        headers=headers
    )
    assert ingest_resp_a.status_code == 201

    # Ingest FAQ Document B (Category: fixed_deposit_interest_rate)
    url_b = "https://www.dcb.bank.in/fd-rates"
    ingest_resp_b = client.post(
        "/ingest/text",
        json={
            "source_identifier": url_b,
            "title": "FD Interest FAQ Page",
            "text_content": "Q: What is the FD rate?\nA: The FD interest rate is 7.25% p.a. for standard tenures.",
            "is_html": False
        },
        headers=headers
    )
    assert ingest_resp_b.status_code == 201

    # 3. Test GET /faq/search (O(1) direct lookup)
    search_resp = client.get(
        "/faq/search",
        params={"question": "What is the Gold Loan rate?"},
        headers=headers
    )
    assert search_resp.status_code == 200
    search_data = search_resp.json()
    assert search_data["success"] is True
    assert search_data["faq"]["question"] == "What is the Gold Loan rate?"
    assert search_data["faq"]["answer"] == "The Gold Loan interest rate starts from 9.5% p.a."
    assert search_data["faq"]["category"] == "gold_loan"

    # Search with empty query -> 400 Bad Request
    search_empty_resp = client.get(
        "/faq/search",
        params={"question": "  "},
        headers=headers
    )
    assert search_empty_resp.status_code == 400

    # Search non-existing question -> 404 Not Found
    search_missing_resp = client.get(
        "/faq/search",
        params={"question": "How do I buy a car?"},
        headers=headers
    )
    assert search_missing_resp.status_code == 404

    # 4. Test GET /faq/categories (Summary list)
    cat_summary_resp = client.get("/faq/categories", headers=headers)
    assert cat_summary_resp.status_code == 200
    summary_data = cat_summary_resp.json()
    assert summary_data["success"] is True
    assert summary_data["total_categories"] > 0
    
    # Locate gold_loan and fixed_deposit_interest_rate summaries
    gold_loan_meta = next(c for c in summary_data["categories"] if c["id"] == "gold_loan")
    fd_meta = next(c for c in summary_data["categories"] if c["id"] == "fixed_deposit_interest_rate")
    
    assert gold_loan_meta["count"] == 1
    assert fd_meta["count"] == 1

    # 5. Test GET /faq/categories/{category}/questions (Questions list)
    # Case A: Questions only (include_full_data=False)
    list_q_resp = client.get(
        "/faq/categories/gold_loan/questions",
        params={"include_full_data": False},
        headers=headers
    )
    assert list_q_resp.status_code == 200
    list_q_data = list_q_resp.json()
    assert list_q_data["success"] is True
    assert list_q_data["count"] == 1
    assert list_q_data["questions"] == ["What is the Gold Loan rate?"]

    # Case B: Full FAQ data objects (include_full_data=True)
    list_full_resp = client.get(
        "/faq/categories/gold_loan/questions",
        params={"include_full_data": True},
        headers=headers
    )
    assert list_full_resp.status_code == 200
    list_full_data = list_full_resp.json()
    assert list_full_data["success"] is True
    assert list_full_data["count"] == 1
    assert list_full_data["questions"][0]["question"] == "What is the Gold Loan rate?"
    assert list_full_data["questions"][0]["answer"] == "The Gold Loan interest rate starts from 9.5% p.a."

    # 6. Test DELETE /faq/categories/{category} (Category eviction)
    del_resp = client.delete("/faq/categories/gold_loan", headers=headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["success"] is True

    # 7. Assert that gold_loan is deleted
    search_deleted_resp = client.get(
        "/faq/search",
        params={"question": "What is the Gold Loan rate?"},
        headers=headers
    )
    assert search_deleted_resp.status_code == 404

    # Assert category counts reflect the deletion
    cat_summary_post_resp = client.get("/faq/categories", headers=headers)
    assert cat_summary_post_resp.status_code == 200
    gold_loan_meta_post = next(c for c in cat_summary_post_resp.json()["categories"] if c["id"] == "gold_loan")
    assert gold_loan_meta_post["count"] == 0

    # 8. Assert that Document B (FD Category) remains active and untouched
    search_fd_resp = client.get(
        "/faq/search",
        params={"question": "What is the FD rate?"},
        headers=headers
    )
    assert search_fd_resp.status_code == 200
    assert search_fd_resp.json()["faq"]["question"] == "What is the FD rate?"
