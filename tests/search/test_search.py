import pytest
from unittest.mock import patch


def test_search_endpoint_unauthorized(client):
    """
    Verifies that querying the /search endpoint without authentication yields 401.
    """
    resp = client.post("/search", json={"query": "test query", "limit": 2})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_search_lifecycle_and_active_version_joins(client):
    """
    Verifies full Search API workflow:
    1. Upload document (Version 1).
    2. Search query matches and returns content, score, title, and source URL.
    3. Update document (Version 2) marking Version 1 as superseded.
    4. Search query retrieves ONLY Version 2 chunks, ignoring superseded Version 1 chunks.
    """
    # Mock Redis Q&A cache to bypass network
    with (
        patch("app.ingest.service.FAQCache._cache_faqs_sync"),
        patch("app.ingest.service.FAQCache._evict_faqs_sync"),
    ):
        # 1. Register user and generate API key
        register_payload = {
            "name": "Search Tester",
            "user_id": "search_tester",
            "email": "search_tester@example.com",
            "password": "password123",
            "role": "user",
        }
        client.post("/auth/register", json=register_payload)

        key_payload = {
            "key_in": {"name": "Search Test Key"},
            "login_req": {
                "email": "search_tester@example.com",
                "password": "password123",
            },
        }
        key_resp = client.post("/auth/api-keys", json=key_payload)
        assert key_resp.status_code == 201
        headers = {"X-API-Key": key_resp.json()["plain_key"]}

        # 2. Ingest Version 1 content
        source_url = "https://www.dcb.bank.in/rates-saver"
        title = "DCB Savings Rates Overview"
        v1_text = (
            "The savings account interest rate is 5.5% per annum for standard balances."
        )

        ingest_v1_resp = client.post(
            "/ingest/text",
            json={
                "source_identifier": source_url,
                "title": title,
                "text_content": v1_text,
            },
            headers=headers,
        )
        assert ingest_v1_resp.status_code == 201

        # 3. Perform search on Version 1
        search_resp = client.post(
            "/search",
            json={"query": "What is the savings account rate?", "limit": 3},
            headers=headers,
        )
        assert search_resp.status_code == 200
        search_data = search_resp.json()
        assert "results" in search_data
        results = search_data["results"]
        assert len(results) > 0

        # Verify result item schema
        match = results[0]
        assert "content" in match
        assert "score" in match
        assert "title" in match
        assert "source" in match

        # Verify content and metadata values
        assert "5.5% per annum" in match["content"]
        assert match["title"] == title
        assert match["source"] == source_url
        assert isinstance(match["score"], float)
        assert -1.0 <= match["score"] <= 1.0

        # 4. Ingest Version 2 content (updates document, marks Version 1 superseded)
        v2_text = (
            "The savings account interest rate has been updated to 6.25% per annum."
        )
        ingest_v2_resp = client.post(
            "/ingest/text",
            json={
                "source_identifier": source_url,
                "title": title,
                "text_content": v2_text,
            },
            headers=headers,
        )
        assert ingest_v2_resp.status_code == 201

        # 5. Perform search again
        search_v2_resp = client.post(
            "/search",
            json={"query": "What is the savings account rate?", "limit": 5},
            headers=headers,
        )
        assert search_v2_resp.status_code == 200
        search_v2_data = search_v2_resp.json()
        results_v2 = search_v2_data["results"]

        # Verify that ONLY Version 2 chunks are found, and Version 1 superseded chunk is absent
        assert len(results_v2) > 0

        # Check if the new text is retrieved
        found_new = any("6.25% per annum" in r["content"] for r in results_v2)
        # Check if the old text is completely hidden
        found_old = any("5.5% per annum" in r["content"] for r in results_v2)

        assert found_new is True, "Should retrieve updated Version 2 chunk"
        assert found_old is False, "Should NOT retrieve superseded Version 1 chunk"

        # 6. Test direct lookup of chunk IDs and parent retrieval
        chunk_id = results_v2[0].get("id")
        assert chunk_id is not None, "Returned chunks should have unique integer ID"

        search_ids_resp = client.post(
            "/search", json={"chunk_ids": [chunk_id]}, headers=headers
        )
        assert search_ids_resp.status_code == 200
        search_ids_data = search_ids_resp.json()
        assert "results" in search_ids_data
        results_ids = search_ids_data["results"]
        assert len(results_ids) == 1
        assert results_ids[0]["id"] == chunk_id
        assert results_ids[0]["content"] == results_v2[0]["content"]
        assert results_ids[0]["title"] == title
        assert results_ids[0]["source"] == source_url
        assert results_ids[0]["metadata"] is not None
        assert "document_id" in results_ids[0]["metadata"]
        assert "chunk_index" in results_ids[0]["metadata"]


@pytest.mark.anyio
async def test_table_reconstruction_and_sibling_resolutions(client):
    """
    Verifies that:
    1. HTML tables are ingested and chunked as individual rows.
    2. Setting resolve_full_tables=True on /search query queries all row siblings
       and reconstructs a single unified Markdown table context block.
    """
    with (
        patch("app.ingest.service.FAQCache._cache_faqs_sync"),
        patch("app.ingest.service.FAQCache._evict_faqs_sync"),
    ):
        # 1. Register user and generate API key
        register_payload = {
            "name": "Table Tester",
            "user_id": "table_tester",
            "email": "table_tester@example.com",
            "password": "password123",
            "role": "user",
        }
        client.post("/auth/register", json=register_payload)

        key_payload = {
            "key_in": {"name": "Table Test Key"},
            "login_req": {
                "email": "table_tester@example.com",
                "password": "password123",
            },
        }
        key_resp = client.post("/auth/api-keys", json=key_payload)
        assert key_resp.status_code == 201
        headers = {"X-API-Key": key_resp.json()["plain_key"]}

        # 2. Ingest HTML containing table
        source_url = "https://www.dcb.bank.in/fd-rates-table"
        title = "DCB Savings Table Test"
        html_content = """
        <html>
        <body>
            <table>
                <caption>DCB FD Rates</caption>
                <thead>
                    <tr>
                        <th>Tenure</th>
                        <th>General Rate</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>10 months</td>
                        <td>6.50%</td>
                    </tr>
                    <tr>
                        <td>12 months</td>
                        <td>6.75%</td>
                    </tr>
                </tbody>
            </table>
        </body>
        </html>
        """

        ingest_resp = client.post(
            "/ingest/text",
            json={
                "source_identifier": source_url,
                "title": title,
                "text_content": html_content,
                "is_html": True,
            },
            headers=headers,
        )
        assert ingest_resp.status_code == 201

        # 3. Search WITHOUT resolving full tables
        search_normal_resp = client.post(
            "/search",
            json={"query": "10 months", "resolve_full_tables": False},
            headers=headers,
        )
        assert search_normal_resp.status_code == 200
        normal_results = search_normal_resp.json()["results"]
        assert len(normal_results) > 0

        # Find the chunk of type table_row in the results
        table_row_chunks = [
            r
            for r in normal_results
            if r.get("metadata", {}).get("type") == "table_row"
        ]
        assert len(table_row_chunks) > 0, (
            f"Expected table_row chunk in results: {normal_results}"
        )
        normal_chunk = table_row_chunks[0]

        # Should contain single row details
        assert ("Row 1:" in normal_chunk["content"]) or (
            "Row 2:" in normal_chunk["content"]
        )
        if "Row 1:" in normal_chunk["content"]:
            assert "10 months" in normal_chunk["content"]
            assert "6.50%" in normal_chunk["content"]
            assert "12 months" not in normal_chunk["content"]  # Other row is absent
        else:
            assert "12 months" in normal_chunk["content"]
            assert "6.75%" in normal_chunk["content"]
            assert "10 months" not in normal_chunk["content"]  # Other row is absent

        # 4. Search WITH resolving full tables enabled
        search_resolve_resp = client.post(
            "/search",
            json={"query": "10 months", "resolve_full_tables": True},
            headers=headers,
        )
        assert search_resolve_resp.status_code == 200
        resolved_results = search_resolve_resp.json()["results"]
        assert len(resolved_results) > 0

        # Find resolved table row chunk in results
        resolved_table_chunks = [
            r
            for r in resolved_results
            if r.get("metadata", {}).get("type") == "table_row"
        ]
        assert len(resolved_table_chunks) > 0
        resolved_chunk = resolved_table_chunks[0]

        # Should contain the unified markdown table including BOTH rows
        assert "Full Table:" in resolved_chunk["content"]
        assert "Tenure | General Rate" in resolved_chunk["content"]
        assert "10 months | 6.50%" in resolved_chunk["content"]
        assert "12 months | 6.75%" in resolved_chunk["content"]

        # 5. Direct ID lookup WITH resolving full tables enabled
        chunk_id = normal_chunk["id"]
        search_ids_resp = client.post(
            "/search",
            json={"chunk_ids": [chunk_id], "resolve_full_tables": True},
            headers=headers,
        )
        assert search_ids_resp.status_code == 200
        resolved_ids = search_ids_resp.json()["results"]
        assert len(resolved_ids) == 1
        assert "Full Table:" in resolved_ids[0]["content"]
        assert "10 months | 6.50%" in resolved_ids[0]["content"]
        assert "12 months | 6.75%" in resolved_ids[0]["content"]
