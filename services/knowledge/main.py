from contextlib import asynccontextmanager
import logging as logger

import uvicorn
from fastapi import FastAPI

from app.auth.routes import router as auth_router
from app.crawl.routes import router as crawl_router
from app.ingest import ingest_router
from app.search.routes import router as search_router
from app.faq.routes import router as faq_router
from shared.database.mongo import MongoDBManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Trigger MongoDB connection pool initialization
    db = MongoDBManager.get_db()

    # Create indexes asynchronously for collection performance and unique fields
    try:
        await db.users.create_index("user_id", unique=True)
        await db.users.create_index("email", unique=True)
        await db.api_keys.create_index("key_hash", unique=True)
        await db.ingested_documents.create_index("source_identifier", unique=True)
        await db.document_versions.create_index("document_id")
        await db.crawl_tasks.create_index("user_id")
        await db.crawled_pages.create_index("task_id")
        await db.crawled_pages.create_index("url")
        logger.info("Successfully verified and created MongoDB indexes")
    except Exception as e:
        logger.warning("Failed to create MongoDB indexes: %s", e)

    yield
    # Safely close connections on shutdown
    await MongoDBManager.close()


# Create FastAPI application instance with lifespan context
app = FastAPI(
    title="Knowledge Service",
    description="Microservice managing web crawls, parsing, Milvus vector database indexing, and FAQ cache aggregation.",
    version="0.1.0",
    lifespan=lifespan,
)

# Register routes
app.include_router(auth_router)
app.include_router(crawl_router)
app.include_router(ingest_router)
app.include_router(search_router)
app.include_router(faq_router)


@app.get("/")
def read_root():
    return {
        "status": "online",
        "service": "knowledge",
        "message": "Welcome to the Knowledge Service. Visit /docs for Swagger documentation.",
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
