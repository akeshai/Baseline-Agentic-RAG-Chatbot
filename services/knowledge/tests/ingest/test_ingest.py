import pytest
from unittest.mock import patch
from bson import ObjectId

from shared.database.mongo import MongoDBManager
from app.ingest.chunker import TokenAwareChunker
from app.ingest.parser import HTMLTableParser
from app.ingest.service import IngestionService


def test_html_table_parser():
    """
    Verifies that HTMLTableParser correctly extracts and expands rowspan/colspan.
    """
    html = """
    <table>
        <thead>
            <tr>
                <th colspan="2">Details</th>
            </tr>
            <tr>
                <th>Category</th>
                <th>Rate</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td rowspan="2">Gold</td>
                <td>12%</td>
            </tr>
            <tr>
                <td>14%</td>
            </tr>
        </tbody>
    </table>
    """
    parser = HTMLTableParser()
    dfs = parser.parse_html(html)
    assert len(dfs) == 1
    df = dfs[0]

    # Verify column index flattening
    assert "Details > Category" in df.columns or "Category" in df.columns
    # Verify forward fill of rowspan
    assert df.iloc[0, 0] == "Gold"
    assert df.iloc[1, 0] == "Gold"


def test_token_aware_chunker():
    """
    Verifies that TokenAwareChunker processes plain text and tables.
    """
    chunker = TokenAwareChunker(chunk_size=10, chunk_overlap=2)
    text = "This is a long piece of text designed to test the chunker token capability."

    chunks = chunker._chunk_plain_text(text, source_title="TestDoc")
    assert len(chunks) > 0
    assert all(c["metadata"]["type"] == "text" for c in chunks)
    assert "TestDoc" in chunks[0]["content"]


@pytest.mark.anyio
async def test_ingestion_service_lifecycle(client):
    """
    Verifies the complete ingestion version lifecycle:
    Case 1: Created (first insert)
    Case 2: Skipped (duplicate identical payload)
    Case 3: Updated (content changes, version bumps, chunk sync)
    """
    # Mock Redis to avoid network traffic
    with (
        patch("app.ingest.service.FAQCache._cache_faqs_sync"),
        patch("app.ingest.service.FAQCache._evict_faqs_sync"),
    ):  # noqa
        service = IngestionService()
        identifier = "manual://tests/test_doc_lifecycle"
        title = "Lifecycle Document"

        # --- Run 1: First Upload (Created) ---
        text_1 = "Q: What is the rate?\nA: The rate is 10% p.a. for standard accounts."
        res_1 = await service.ingest_content(
            source_type="manual_text",
            identifier=identifier,
            title=title,
            text_content=text_1,
        )
        assert res_1["action"] == "created"
        assert res_1["version"] == 1
        doc_id = res_1["document_id"]

        # Verify DB entries
        db = MongoDBManager.get_db()
        doc_rec = await db.ingested_documents.find_one({"_id": ObjectId(doc_id)})
        assert doc_rec is not None
        assert doc_rec["current_version"] == 1
        assert doc_rec["current_hash"] == res_1["hash"]

        # Check versions
        versions = await db.document_versions.find({"document_id": doc_id}).to_list(
            length=100
        )
        assert len(versions) == 1
        assert versions[0]["status"] == "active"

        # Check chunks
        from app.vector_store import get_vector_store

        vs = get_vector_store()
        chunks = await vs.query_chunks(
            filter_expr=f'version_id == "{versions[0]["_id"]}"'
        )
        assert len(chunks) > 0

        # --- Run 2: Re-upload Identical Payload (Skipped) ---
        res_2 = await service.ingest_content(
            source_type="manual_text",
            identifier=identifier,
            title=title,
            text_content=text_1,
        )
        assert res_2["action"] == "skipped"
        assert res_2["version"] == 1

        # --- Run 3: Modify Payload (Updated / Bump Version) ---
        text_2 = (
            "Q: What is the rate?\nA: The rate is now 11% p.a. for standard accounts."
        )
        res_3 = await service.ingest_content(
            source_type="manual_text",
            identifier=identifier,
            title=title,
            text_content=text_2,
        )
        assert res_3["action"] == "updated"
        assert res_3["version"] == 2

        # Verify DB update and old version superseded
        db = MongoDBManager.get_db()
        doc_rec = await db.ingested_documents.find_one({"_id": ObjectId(doc_id)})
        assert doc_rec["current_version"] == 2
        assert doc_rec["current_hash"] == res_3["hash"]

        # Verifications list
        versions = await db.document_versions.find({"document_id": doc_id}).to_list(
            length=100
        )
        assert len(versions) == 2

        active_ver = [v for v in versions if v["status"] == "active"]
        superseded_ver = [v for v in versions if v["status"] == "superseded"]
        assert len(active_ver) == 1
        assert len(superseded_ver) == 1
        assert active_ver[0]["version"] == 2
        assert superseded_ver[0]["version"] == 1

        # Assert old chunks were cleared and new chunks inserted
        from app.vector_store import get_vector_store

        vs = get_vector_store()

        chunks_1 = await vs.query_chunks(
            filter_expr=f'version_id == "{superseded_ver[0]["_id"]}"'
        )
        assert len(chunks_1) == 0

        chunks_2 = await vs.query_chunks(
            filter_expr=f'version_id == "{active_ver[0]["_id"]}"'
        )
        assert len(chunks_2) > 0


def test_faq_extraction():
    """
    Verifies regex FAQ extractor parsing and hybrid URL/keyword categorization.
    """
    service = IngestionService()
    text = """
    Welcome to our portal.
    Question: How do I apply?
    Answer: Simply click on the apply now button.
    
    Q: Is there an age limit?
    A: Yes, min 18 years.
    """
    # Test standard text FAQ extraction with url categorization matching "others"
    faqs = service._extract_faqs_from_text(
        text, url="https://www.dcb.bank.in/customer-support"
    )
    assert len(faqs) == 2
    assert faqs[0]["question"] == "How do I apply?"
    assert faqs[0]["answer"] == "Simply click on the apply now button."
    assert faqs[0]["category"] == "others"
    assert faqs[1]["question"] == "Is there an age limit?"
    assert faqs[1]["answer"] == "Yes, min 18 years."
    assert faqs[1]["category"] == "others"

    # Test HTML parsing fallback for FAQ extraction with category mapping (e.g. gold loan)
    html_text = """
    <div>
        <p><strong>Question:</strong> What are gold loan interest rates?</p>
        <p><strong>Answer:</strong> Our interest rates start from 9.5% p.a. on gold ornaments.</p>
    </div>
    """
    html_faqs = service._extract_faqs_from_text(
        html_text, url="https://www.dcb.bank.in/loans/gold-loan"
    )
    assert len(html_faqs) == 1
    assert html_faqs[0]["question"] == "What are gold loan interest rates?"
    assert (
        html_faqs[0]["answer"]
        == "Our interest rates start from 9.5% p.a. on gold ornaments."
    )
    assert html_faqs[0]["category"] == "gold_loan"

    # Test Accordion HTML parser logic
    accordion_html = """
    <section id="product-faqs">
        <div data-accordion-component="AccordionItem">
            <div data-accordion-component="AccordionItemHeading">
                <div class="accordion__button">What is the tenure for booking a fixed deposit?</div>
            </div>
            <div data-accordion-component="AccordionItemPanel">
                <p>You can choose any tenure from 7 days to 120 months to book a DCB FD.</p>
            </div>
        </div>
    </section>
    """
    accordion_faqs = service._extract_faqs_from_text(
        accordion_html, url="https://www.dcb.bank.in/fd-rates"
    )
    assert len(accordion_faqs) == 1
    assert (
        accordion_faqs[0]["question"]
        == "What is the tenure for booking a fixed deposit?"
    )
    assert "7 days to 120 months" in accordion_faqs[0]["answer"]
    assert accordion_faqs[0]["category"] == "fixed_deposit_interest_rate"


def test_parsing_real_html_tables():
    """
    Reads moved test HTML files and ensures they parse cleanly.
    """
    import os

    assets_dir = os.path.join(os.path.dirname(__file__), "assets")

    # Read table_html_4.html.md which is a large interest rate table
    file_path = os.path.join(assets_dir, "table_html_4.html.md")
    assert os.path.exists(file_path)

    with open(file_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    parser = HTMLTableParser()
    dfs = parser.parse_html(html_content)
    assert len(dfs) > 0
    df = dfs[0]

    # Verify that we parsed rows and columns successfully
    assert not df.empty
    assert df.shape[0] > 0
    assert df.shape[1] > 0


def test_ingest_metadata_route(client):
    """
    Verifies that the GET /ingest/metadata route returns the document version
    and pending crawled pages/files metadata under authentication.
    """
    # 1. Register user and create API key
    register_payload = {
        "name": "Ingest Tester",
        "user_id": "ingest_tester",
        "email": "ingest_tester@example.com",
        "password": "password123",
        "role": "user",
    }
    client.post("/auth/register", json=register_payload)

    key_payload = {
        "key_in": {"name": "Test Ingest Key"},
        "login_req": {"email": "ingest_tester@example.com", "password": "password123"},
    }
    key_resp = client.post("/auth/api-keys", json=key_payload)
    assert key_resp.status_code == 201
    headers = {"X-API-Key": key_resp.json()["plain_key"]}

    # 2. Ingest raw text
    ingest_payload = {
        "source_identifier": "manual://tests/test_metadata_route",
        "title": "Metadata Test Doc",
        "text_content": "This is test text for metadata endpoint testing.",
    }
    post_resp = client.post("/ingest/text", json=ingest_payload, headers=headers)
    assert post_resp.status_code == 201

    # 3. Create mock crawl directory and db page for task 999
    import asyncio
    import shutil
    from pathlib import Path

    from app.configs.crawl import settings as crawl_settings
    from app.crawl.models import CrawledPage
    from shared.database.mongo import MongoDBManager

    root_dir = Path(crawl_settings.object_storage_root)
    task_dir = root_dir / "crawls" / "tasks" / "999"
    task_dir.mkdir(parents=True, exist_ok=True)
    with open(task_dir / "dummy.html", "w") as f:
        f.write("dummy")

    async def _add_pending_page():
        db = MongoDBManager.get_db()
        new_page = CrawledPage(
            task_id="999",
            url="https://www.example.com/page_to_ingest",
            title="Page 1",
            status="success",
            status_code=200,
            text_content="Crawl content.",
        )
        await db.crawled_pages.insert_one(new_page.model_dump(by_alias=True))

    asyncio.run(_add_pending_page())

    try:
        # 4. Retrieve metadata status list - Task 999 should be pending_ingestion
        meta_resp = client.get("/ingest/metadata", headers=headers)
        assert meta_resp.status_code == 200
        status_data = meta_resp.json()

        assert "crawls" in status_data
        assert "manual_files" in status_data

        task_meta = next(t for t in status_data["crawls"] if t["task_id"] == "999")
        assert task_meta["status"] == "pending_ingestion"

        # 5. Ingest task 999 page to mark it as ingested
        ingest_payload = {
            "source_identifier": "https://www.example.com/page_to_ingest",
            "title": "Ingested Page 1",
            "text_content": "Crawl content.",
        }
        ingest_resp = client.post("/ingest/text", json=ingest_payload, headers=headers)
        assert ingest_resp.status_code == 201

        # 6. Retrieve metadata list - Task 999 should now be ingested
        meta_resp_2 = client.get("/ingest/metadata", headers=headers)
        assert meta_resp_2.status_code == 200
        status_data_2 = meta_resp_2.json()

        task_meta_2 = next(t for t in status_data_2["crawls"] if t["task_id"] == "999")
        assert task_meta_2["status"] == "ingested"

    finally:
        # Cleanup task directory from disk
        if task_dir.exists():
            shutil.rmtree(task_dir.parent / "999")


def test_target_html_selector_extraction():
    """
    Verifies that TokenAwareChunker isolates text inside the target HTML selector,
    ignoring boilerplate content.
    """
    html = """
    <html>
        <body>
            <header>Header content that should be ignored</header>
            <main id="main-content">
                <article>This is the important targeted content inside main.</article>
            </main>
            <footer>Footer content that should be ignored</footer>
        </body>
    </html>
    """
    # 1. Chunker without selector (should extract everything)
    chunker_all = TokenAwareChunker()
    chunker_all.target_html_selector = None
    chunker_all.config = {}  # Clear global excludes from selectors.yaml for this test
    chunks_all = chunker_all._chunk_document_sync(html, is_html=True)
    assert len(chunks_all) > 0
    text_all = "".join(c["content"] for c in chunks_all)
    assert "Header content" in text_all
    assert "Footer content" in text_all

    # 2. Chunker with selector (should isolate target section)
    chunker_targeted = TokenAwareChunker()
    chunker_targeted.target_html_selector = "#main-content"
    chunks_targeted = chunker_targeted._chunk_document_sync(html, is_html=True)
    assert len(chunks_targeted) > 0
    text_targeted = "".join(c["content"] for c in chunks_targeted)
    assert "Header content" not in text_targeted
    assert "Footer content" not in text_targeted
    assert "important targeted content" in text_targeted


def test_dynamic_html_selector_cleaning():
    """
    Verifies that TokenAwareChunker correctly prunes elements matching excludes,
    finds the root, and handles include strategies.
    """
    html = """
    <html>
        <body>
            <header class="site-header">Header to prune</header>
            <div class="breadcrumbs">Breadcrumbs to prune</div>
            <main class="page-content">
                <article class="main-content">
                    <h1>DCB Savings</h1>
                    <p>Important savings text.</p>
                </article>
                <div class="ads banner-ad">Prune me</div>
                <article class="main-content">
                    <h2>Another section</h2>
                    <p>More text.</p>
                </article>
            </main>
            <aside class="sidebar">Prune me</aside>
            <footer class="footer">Prune me</footer>
        </body>
    </html>
    """
    chunker = TokenAwareChunker()
    chunker.config = {
        "root_selectors": ["main", ".page-content"],
        "include": {
            "strategy": "all_matches",
            "selectors": [".main-content", "article"],
        },
        "exclude": {
            "tags": ["header", "footer", "aside"],
            "classes": ["sidebar", "ads", "breadcrumbs"],
            "ids": [],
        },
        "html_converter": {
            "ignore_images": True,
            "ignore_emphasis": True,
            "ignore_links": True,
            "body_width": 0,
        },
    }
    cleaned_text, _ = chunker._process_html_tables_and_text(
        html, source_title="TestDoc"
    )

    assert "Header to prune" not in cleaned_text
    assert "Breadcrumbs to prune" not in cleaned_text
    assert "Prune me" not in cleaned_text
    assert "DCB Savings" in cleaned_text
    assert "Important savings text." in cleaned_text
    assert "Another section" in cleaned_text
