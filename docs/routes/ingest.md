# Ingestion & Versioning Service Routes (`app/ingest`)

These endpoints manage document ingestion, text/table parsing, token-aware chunking, version deduplication, and RAG search indexing updates.

---

## Headers & Authorization

All routes require API Key authentication:
*   **Header**: `X-API-Key: <your_plain_key>`

---

## Endpoints

### 1. POST `/ingest/text`
*   **Description**: Ingests and chunk-indexes a raw text payload manually pasted from an admin panel.
*   **Request Body (`TextIngestRequest`)**:
    - `source_identifier`: Unique URN referencing the text (e.g. `manual://rates/savings`).
    - `title`: Optional descriptive name for the text block.
    - `text_content`: Clear text content.
    ```json
    {
      "source_identifier": "manual://rates/savings",
      "title": "Savings Interest Rates Note",
      "text_content": "Q: What is the savings rate?\nA: The rate is 3.5% p.a."
    }
    ```
*   **Response Body (`IngestResponse` - `201 Created`)**:
    - `action`: `"created"`, `"updated"`, or `"skipped"` (deduplication status).
    - `version`: Version number (starts at 1).
    ```json
    {
      "document_id": 1,
      "version": 1,
      "action": "created",
      "hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    }
    ```

### 2. POST `/ingest/file`
*   **Description**: Processes multipart document uploads (.txt or .pdf files).
*   **Request Body**:
    - File upload parameter `file` (Multipart form-data).
*   **Response Body (`IngestResponse` - `201 Created`)**

### 3. POST `/ingest/crawl-task/{task_id}`
*   **Description**: Imports, parses, and indexes all successfully crawled pages belonging to a completed crawler task.
*   **Response Body (`List[IngestResponse]` - `200 OK`)**

### 4. GET `/ingest/metadata`
*   **Description**: Retrieves ingested document metadata, including the current version, last updated time, and the history of version records containing custom storage bucket paths (`raw_storage_key`) indicating whether new data was uploaded.
*   **Query Parameters**:
    - `identifier` (Optional URN, e.g. `manual://tests/test_metadata_route` to retrieve details for a single target).
    - `limit` (Optional limit, defaults to `100`).
    - `offset` (Optional offset, defaults to `0`).
*   **Response Body (`IngestionStatusResponse` - `200 OK`)**:
    ```json
    {
      "crawls": [
        {
          "task_id": 11,
          "status": "ingested",
          "created_at": "2026-07-15T14:09:25Z"
        },
        {
          "task_id": 12,
          "status": "pending_ingestion",
          "created_at": "2026-07-15T14:15:00Z"
        }
      ],
      "manual_files": [
        {
          "filename": "document.pdf",
          "status": "pending_ingestion",
          "last_modified_at": "2026-07-15T14:10:00Z"
        }
      ]
    }
    ```

---

## Deduplication & Versioning Rules
1. **Deduplication Check**: When a document is submitted, the system computes the SHA-256 hash of its text. If the hash matches the active version in the database, the operation is skipped (`action: "skipped"`) to conserve resources.
2. **Versioning Update**: If the hash changes, the document's version number increments (e.g. `version 2`), and the old version status is updated to `superseded`.
3. **Index Cleanup**: The vector database chunks belonging to superseded versions are deleted automatically, and the new version chunks are embedded and inserted.
4. **FAQ Syncing**: If Q&A pairs (e.g. `Q: ... A: ...`) are parsed in the text, they are cached in Redis under the `doc:{doc_id}:faqs` key, evicting the previous FAQ set atomically.
5. **Boilerplate Filtering (HTML Selector Isolation)**: If `target_html_selector` is configured under the `ingestion` block in [selectors.yaml](file:///c:/Users/akliv/Desktop/AkeshPersonal/ChatBot/app/configs/selectors.yaml), the system automatically isolates that CSS container (e.g. `main` or `#main-content`), stripping headers, footers, navigation, and sidebar components before chunking. It falls back gracefully to the whole body text if the selector is missing in the document.
6. **Atomic Transaction Safety**: Ingestion operations are executed within a single transaction boundary. Database metadata writes, Redis FAQ caching, and PGVector chunk insertions share the same transaction. If any stage fails (such as an OCI embedding service timeout, authorization error, or chunk/vector length mismatch), the relational database transaction is rolled back, leaving the document status unchanged (e.g., still showing as pending or not ingested). This prevents partial-ingestion states where a document is marked as processed but contains zero vector chunks.

