# Crawling Service Routes (`app/crawl`)

These routes initiate BFS website crawls, track progress, and fetch dynamic scraped text/HTML page outputs.

---

## Endpoints

### POST `/crawl`
*   **Description**: Starts a background crawl task or refreshes specific pages.
*   **Headers Required**: `X-API-Key: <your_plain_key>`
*   **Request Body (`CrawlRequest` schema)**:
    - `strategy`: `"recursive"` (follows local links) or `"single"` (scrapes input URLs only).
    - `concurrency_strategy`: `"concurrent"` (parallel workers) or `"single"` (sequential processing).
    ```json
    {
      "urls": ["https://example.com"],
      "max_depth": 2,
      "max_pages": 10,
      "strategy": "recursive",
      "concurrency_strategy": "concurrent",
      "concurrency_limit": 3
    }
    ```
*   **Response Body (`CrawlTaskResponse` - `201 Created`)**:
    ```json
    {
      "id": 1,
      "user_id": 1,
      "start_url": "https://example.com/",
      "status": "pending",
      "pages_crawled": 0,
      "pages_failed": 0,
      "error_message": null,
      "created_at": "2026-07-14T15:00:00Z",
      "updated_at": "2026-07-14T15:00:00Z"
    }
    ```

### GET `/crawl/tasks`
*   **Description**: Lists all crawl tasks initiated by the authenticated user.
*   **Headers Required**: `X-API-Key: <your_plain_key>`
*   **Response Body (`List[CrawlTaskResponse]` - `200 OK`)**

### GET `/crawl/tasks/{task_id}`
*   **Description**: Retrieves progress counters and status of a specific task.
*   **Headers Required**: `X-API-Key: <your_plain_key>`
*   **Response Body (`CrawlTaskResponse` - `200 OK`)**

### GET `/crawl/tasks/{task_id}/pages`
*   **Description**: Lists metadata of crawled pages under a specific task.
*   **Headers Required**: `X-API-Key: <your_plain_key>`
*   **Response Body (`List[CrawledPageResponse]` - `200 OK`)**:
    *   *Note: Omits raw HTML and body text payloads to save bandwidth.*
    ```json
    [
      {
        "id": 1,
        "task_id": 1,
        "url": "https://example.com/",
        "title": "Example Domain",
        "depth": 0,
        "status_code": 200,
        "status": "success",
        "error_log": null,
        "created_at": "2026-07-14T15:00:05Z"
      }
    ]
    ```

### GET `/crawl/pages/{page_id}`
*   **Description**: Retrieves details for a specific page, including raw HTML and cleaned text.
*   **Headers Required**: `X-API-Key: <your_plain_key>`
*   **Response Body (`CrawledPageDetailResponse` - `200 OK`)**:
    *   *Note: Dynamically pulls HTML content from the object storage if offloaded.*
    ```json
    {
      "id": 1,
      "task_id": 1,
      "url": "https://example.com/",
      "title": "Example Domain",
      "depth": 0,
      "status_code": 200,
      "status": "success",
      "error_log": null,
      "created_at": "2026-07-14T15:00:05Z",
      "html_content": "<!DOCTYPE html><html>...",
      "text_content": "Example Domain This domain is for use..."
    }
    ```
