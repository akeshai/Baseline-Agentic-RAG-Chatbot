import asyncio
import os
import sys

# Ensure project root is in python path
sys.path.append(os.getcwd())

# Force environment overrides to connect to the Docker container's PostgreSQL database
os.environ["DB_TYPE"] = "postgresql"
os.environ["DB_HOST"] = "localhost"
os.environ["DB_PORT"] = "5432"
os.environ["DB_NAME"] = "chatbot"
os.environ["DB_USER"] = "postgres"
os.environ["DB_PASSWORD"] = "postgres"

from sqlalchemy import select, delete
from app.database import SessionLocal
from app.crawl.models import CrawledPage
from app.ingest.models import IngestedDocument


async def main():
    print("--------------------------------------------------")
    print("CLEANING INGESTION METADATA FOR TASK 11")
    print("--------------------------------------------------")
    
    async with SessionLocal() as session:
        # 1. Fetch urls corresponding to task 11
        stmt = select(CrawledPage.url).where(CrawledPage.task_id == 11)
        result = await session.execute(stmt)
        urls = result.scalars().all()
        
        if not urls:
            print("[WARN] No crawled pages found in database for task 11.")
            return
            
        print(f"Found {len(urls)} crawled page URLs for task 11.")
        
        # 2. Delete corresponding IngestedDocuments (cascades delete versions/chunks)
        delete_stmt = delete(IngestedDocument).where(IngestedDocument.source_identifier.in_(urls))
        res = await session.execute(delete_stmt)
        await session.commit()
        
        print(f"[SUCCESS] Cleaned metadata. IngestedDocument records deleted: {res.rowcount}")
        print("You can now trigger the ingestion endpoint /ingest/crawl-task/11 again!")
        print("--------------------------------------------------")


if __name__ == "__main__":
    asyncio.run(main())
