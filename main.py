import uvicorn
from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.database import engine, Base
from app.auth.routes import router as auth_router
from app.crawl.routes import router as crawl_router
from app.ingest import ingest_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Automatically create database tables using async connection context
    async with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            from sqlalchemy import text

            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        await conn.run_sync(Base.metadata.create_all)
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


@app.get("/")
def read_root():
    return {
        "status": "online",
        "message": "Welcome to the ChatBot API. Visit /docs for Swagger documentation.",
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
