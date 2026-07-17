import asyncio
import logging
import sys
import os
from datetime import datetime
from bs4 import BeautifulSoup
from shared.database.mongo import MongoDBManager
from app.configs.crawl import settings as crawl_settings
from app.storage import get_object_storage

# Ensure services/knowledge is in Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Manually load the root .env file from the correct workspace root (3 levels up)
root_env = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env")
)
if os.path.exists(root_env):
    with open(root_env, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip("'\" ")
                os.environ[k.strip()] = v
else:
    print(f"WARNING: Root .env not found at {root_env}")


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("import_crawled_task")


async def main():
    logger.info("Initializing storage and database connections...")
    logger.info(f"Storage Provider: {crawl_settings.object_storage_provider}")
    logger.info(f"MinIO Endpoint: {crawl_settings.minio_endpoint}")

    storage = get_object_storage()
    db = MongoDBManager.get_db()

    # User to assign task to (from MongoDB query: akesh-aih)
    user_id = "akesh-aih"
    start_url = "https://www.dcb.bank.in"

    # 1. Create a CrawlTask in MongoDB (with a valid ObjectId generated automatically)
    task_doc = {
        "user_id": user_id,
        "start_url": start_url,
        "status": "running",
        "pages_crawled": 0,
        "pages_failed": 0,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    task_res = await db.crawl_tasks.insert_one(task_doc)
    new_task_id = str(task_res.inserted_id)
    logger.info(f"Created new CrawlTask in MongoDB with ID: {new_task_id}")

    # 2. List all HTML files under tasks/2/html/
    bucket = crawl_settings.raw_html_bucket
    prefix = "tasks/2/html/"
    logger.info(
        f"Listing HTML files in MinIO bucket '{bucket}' with prefix '{prefix}'..."
    )
    files = await storage.list_files(bucket, prefix)

    if not files:
        logger.error(
            f"No crawled HTML files found in bucket '{bucket}' with prefix '{prefix}'. Exiting."
        )
        await db.crawl_tasks.delete_one({"_id": task_res.inserted_id})
        return

    logger.info(f"Found {len(files)} files to register in MongoDB.")

    # 3. Process each file and register it in the crawled_pages collection
    pages_crawled = 0
    pages_failed = 0

    for idx, f in enumerate(files):
        key = f["key"]
        filename = f["filename"]

        try:
            # Download raw HTML
            html_bytes = await storage.download_file(bucket, key)
            html_content = html_bytes.decode("utf-8", errors="ignore")

            # Parse HTML
            soup = BeautifulSoup(html_content, "html.parser")

            # Find URL (from canonical link or og:url, else fall back to filename representation)
            url = None
            canonical_tag = soup.find("link", rel="canonical")
            if canonical_tag and canonical_tag.get("href"):
                url = canonical_tag["href"]
            else:
                og_url_tag = soup.find("meta", property="og:url")
                if og_url_tag and og_url_tag.get("content"):
                    url = og_url_tag["content"]

            # Fallback URL parsing from filename
            if not url:
                url_slug = filename.replace(".html", "")
                if "_" in url_slug and len(url_slug.split("_")[-1]) == 8:
                    url_slug = "_".join(url_slug.split("_")[:-1])
                if url_slug.startswith("www_dcb_bank_in_"):
                    path_part = url_slug[len("www_dcb_bank_in_") :]
                    url = "https://www.dcb.bank.in/" + path_part.replace("_", "/")
                elif url_slug.startswith("www_dcb_bank_in"):
                    url = "https://www.dcb.bank.in/"
                else:
                    url = f"https://www.dcb.bank.in/{url_slug}"

            # Extract Title
            title = filename
            if soup.title and soup.title.string:
                title = soup.title.string.strip()

            # Extract plain text content for quick parsing
            for tag in soup(["script", "style", "head", "title", "meta"]):
                tag.decompose()
            text_content = soup.get_text(separator="\n")
            text_content = "\n".join(
                [line.strip() for line in text_content.splitlines() if line.strip()]
            )

            # Prepare crawled page document
            page_doc = {
                "task_id": new_task_id,
                "url": url,
                "title": title,
                "html_content": f"object://{bucket}/{key}",
                "text_content": text_content,
                "depth": 1,
                "status_code": 200,
                "status": "success",
                "created_at": datetime.utcnow(),
            }

            await db.crawled_pages.insert_one(page_doc)
            pages_crawled += 1
            if pages_crawled % 20 == 0 or pages_crawled == len(files):
                logger.info(
                    f"Registered {pages_crawled}/{len(files)} pages in MongoDB..."
                )

        except Exception as e:
            logger.error(f"Failed to process file {key}: {e}")
            pages_failed += 1

    # Update CrawlTask status to completed
    await db.crawl_tasks.update_one(
        {"_id": task_res.inserted_id},
        {
            "$set": {
                "status": "completed",
                "pages_crawled": pages_crawled,
                "pages_failed": pages_failed,
                "updated_at": datetime.utcnow(),
            }
        },
    )

    logger.info("CrawlTask registration complete!")
    logger.info("=================================================")
    logger.info(f"New MongoDB Task ID: {new_task_id}")
    logger.info(f"Registered pages count: {pages_crawled} (Failed: {pages_failed})")
    logger.info("=================================================")
    logger.info(
        "You can now trigger ingestion for this task by sending a POST request to:"
    )
    logger.info(f"http://localhost:8000/ingest/crawl-task/{new_task_id}")


if __name__ == "__main__":
    asyncio.run(main())
