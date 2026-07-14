# Crawling Service Routes (`app/crawl`)

These routes initiate background web crawls, track execution progress, and fetch scraped page details.

---

## 1. Parameters Reference

| Field | Type | Default | Constraints | Description |
| :--- | :--- | :--- | :--- | :--- |
| **`urls`** | `List[HttpUrl]` | *Required* | Min 1 item | A list of seed URLs to crawl or refresh. |
| **`max_depth`** | `int` | `2` | `0 <= val <= 5` | BFS link discovery depth (forced to `0` if strategy is `"single"`). |
| **`max_pages`** | `int` | `20` | `1 <= val <= 100` | Soft stop limit on total crawled pages to protect memory. |
| **`strategy`** | `string` | `"recursive"` | `"single"` or `"recursive"` | Execution scope. See detailed flows below. |
| **`concurrency_strategy`** | `string` | `"concurrent"` | `"single"` or `"concurrent"` | Worker loop structure (sequential vs concurrent workers). |
| **`concurrency_limit`** | `int` | `3` | `1 <= val <= 10` | Max simultaneous workers active in concurrent mode. |
| **`allowed_domains`** | `List[str]` | `null` | Optional | Allowed domain hostnames (defaults to seed hostnames if recursive). |
| **`allowed_urls`** | `List[str]` | `null` | Optional | Restricts crawling strictly to links matching these URL prefixes. |

---

## 2. Crawling Flow Examples

### Flow A: Single Page Scrape or Refresh (`strategy: "single"`)
Use this flow to scrape specific, standalone pages or documents (e.g. rate tables or PDF forms) without discovering or following other links.

-   **Behavior**: Forces `max_depth = 0`. The crawler starts, fetches the input URLs directly, offloads assets, and completes the task immediately.
-   **POST `/crawl` Request Body**:
    ```json
    {
      "urls": ["https://www.dcb.bank.in/customer-corner"],
      "strategy": "single",
      "concurrency_strategy": "single"
    }
    ```
-   **Execution Sequence**:
    1.  Registers `CrawlTask` in the database (`status: "pending"`).
    2.  FastAPI launches a background worker context.
    3.  Browser initializes and scrapes `https://www.dcb.bank.in/customer-corner`.
    4.  Extracts title, body text, and saves a JPEG screenshot.
    5.  Saves files to object storage and logs task completion (`status: "completed"`).

---

### Flow B: Recursive BFS Site Scan (`strategy: "recursive"`)
Use this flow to scan an entire site or section of a site up to a specific depth and page limit.

-   **Behavior**: Starts at depth 0, parses pages, extracts all local anchor link URLs matching the allowed domains/prefixes, and adds them to a BFS queue to crawl next.
-   **POST `/crawl` Request Body**:
    ```json
    {
      "urls": ["https://www.dcb.bank.in/"],
      "max_depth": 3,
      "max_pages": 15,
      "strategy": "recursive",
      "concurrency_strategy": "concurrent",
      "concurrency_limit": 4,
      "allowed_domains": ["dcb.bank.in"],
      "allowed_urls": ["https://www.dcb.bank.in/assets"]
    }
    ```
-   **Execution Sequence**:
    1.  Registers `CrawlTask` in the database.
    2.  Spawns `4` concurrent Playwright page workers.
    3.  Pulls `https://www.dcb.bank.in/` from queue (depth 0).
    4.  Saves page data and extracts local links.
    5.  Filters discovered links:
        -   Must belong to `dcb.bank.in` domain.
        -   Must start with prefix `https://www.dcb.bank.in/assets`.
    6.  Enqueues matching links as depth 1.
    7.  Workers pull from queue concurrently until `max_pages` (15) is hit or the queue is exhausted.

---

## 3. Endpoints Documentation

### POST `/crawl`
*   **Description**: Creates and schedules a background crawl run.
*   **Headers Required**: `X-API-Key: <your_plain_key>`
*   **Response Body (`CrawlTaskResponse` - `201 Created`)**:
    ```json
    {
      "id": 12,
      "user_id": 1,
      "start_url": "https://www.dcb.bank.in/",
      "status": "pending",
      "pages_crawled": 0,
      "pages_failed": 0,
      "error_message": null,
      "created_at": "2026-07-14T15:00:00Z",
      "updated_at": "2026-07-14T15:00:00Z"
    }
    ```

### GET `/crawl/tasks`
*   **Description**: Lists all crawl tasks started by the authenticated user.
*   **Headers Required**: `X-API-Key: <your_plain_key>`
*   **Response Body (`List[CrawlTaskResponse]` - `200 OK`)**

### GET `/crawl/tasks/{task_id}`
*   **Description**: Retrieves task status and progression counters.
*   **Headers Required**: `X-API-Key: <your_plain_key>`
*   **Response Body (`CrawlTaskResponse` - `200 OK`)**

### GET `/crawl/tasks/{task_id}/pages`
*   **Description**: Lists all successfully or unsuccessfully crawled pages for a task.
*   **Headers Required**: `X-API-Key: <your_plain_key>`
*   **Response Body (`List[CrawledPageResponse]` - `200 OK`)**:
    ```json
    [
      {
        "id": 43,
        "task_id": 12,
        "url": "https://www.dcb.bank.in/assets/rates.pdf",
        "title": "Rates Sheet",
        "depth": 1,
        "status_code": 200,
        "status": "success",
        "error_log": null,
        "created_at": "2026-07-14T15:01:10Z"
      }
    ]
    ```

### GET `/crawl/pages/{page_id}`
*   **Description**: Retrieves raw page HTML body and text contents.
*   **Headers Required**: `X-API-Key: <your_plain_key>`
*   **Response Body (`CrawledPageDetailResponse` - `200 OK`)**:
    ```json
    {
      "id": 43,
      "task_id": 12,
      "url": "https://www.dcb.bank.in/assets/rates.pdf",
      "title": "Rates Sheet",
      "depth": 1,
      "status_code": 200,
      "status": "success",
      "error_log": null,
      "created_at": "2026-07-14T15:01:10Z",
      "html_content": null,
      "text_content": "Savings Interest Rates effective from..."
    }
    ```
