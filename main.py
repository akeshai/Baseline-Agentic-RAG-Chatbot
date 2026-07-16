from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.auth.routes import router as auth_router
from app.crawl.routes import router as crawl_router
from app.database import Base, engine
from app.ingest import ingest_router
from app.search.routes import router as search_router
import logging as logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Automatically create database tables using async connection context
    async with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            from sqlalchemy import text

            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        try:
            await conn.run_sync(Base.metadata.create_all)
        except Exception as e:
            logger.warning(
                "Base.metadata.create_all encountered an exception (possibly index/table already exists): %s",
                e,
            )

        # Dynamic migration for full-text search columns and indices on PostgreSQL
        if conn.dialect.name == "postgresql":
            from sqlalchemy import text

            try:
                await conn.execute(
                    text(
                        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS tsv_content tsvector;"
                    )
                )
                await conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS tsv_content_idx ON document_chunks USING gin(tsv_content);"
                    )
                )
                await conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS document_chunks_embedding_hnsw_idx ON document_chunks USING hnsw (embedding vector_cosine_ops);"
                    )
                )
                await conn.execute(
                    text(
                        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS chunk_metadata jsonb;"
                    )
                )
            except Exception as e:
                logger.warning("FTS and HNSW schema migration exception: %s", e)
    yield
    # Dispose connections on shutdown
    await engine.dispose()


# Create FastAPI application instance with lifespan context
app = FastAPI(
    title="ChatBot API",
    description="A secure chatbot application API with an integrated async authentication system.",
    version="0.1.0",
    lifespan=lifespan,
)

# Register routes
app.include_router(auth_router)
app.include_router(crawl_router)
app.include_router(ingest_router)
app.include_router(search_router)


@app.get("/")
def read_root():
    return {
        "status": "online",
        "message": "Welcome to the ChatBot API. Visit /docs for Swagger documentation.",
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
