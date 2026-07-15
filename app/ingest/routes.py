import asyncio
import io
import logging
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.auth.routes import get_current_user
from app.configs.crawl import settings as crawl_settings
from app.database import get_db
from app.ingest.schemas import (
    IngestionStatusResponse,
    IngestResponse,
    TextIngestRequest,
)
from app.ingest.service import IngestionService
from app.storage import get_object_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["Ingestion"])
ingestion_service = IngestionService()


def _parse_pdf_bytes(pdf_bytes: bytes) -> str:
    """
    Synchronous PDF text extractor.
    """
    import pdfplumber

    extracted_text = ""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                extracted_text += page_text + "\n"
    return extracted_text


@router.post(
    "/text", response_model=IngestResponse, status_code=status.HTTP_201_CREATED
)
async def ingest_raw_text(
    req: TextIngestRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Accepts raw text payload from admin dashboard, splits it into token chunks,
    calculates SHA-256 hash for versioning, and embeds/indexes it.
    """
    try:
        return await ingestion_service.ingest_content(
            source_type="manual_text",
            identifier=req.source_identifier,
            title=req.title,
            text_content=req.text_content,
        )
    except Exception as e:
        logger.error("Ingest text failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Text ingestion failed: {e}",
        )


@router.post(
    "/file", response_model=IngestResponse, status_code=status.HTTP_201_CREATED
)
async def ingest_manual_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """
    Accepts multipart document uploads (.txt or .pdf), stores the raw file in object storage,
    parses the content, chunks, embeds, and updates versions.
    """
    filename = file.filename
    if not filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file must have a valid filename",
        )

    file_ext = filename.split(".")[-1].lower()
    if file_ext not in ("txt", "pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file format. Only .txt and .pdf files are supported",
        )

    try:
        # Read uploaded bytes
        file_bytes = await file.read()

        # 1. Upload raw copy to global Object Storage (MinIO or local simulation)
        object_storage = get_object_storage()
        storage_key = f"manual/uploads/{filename}"
        await object_storage.upload_file(
            bucket=crawl_settings.raw_html_bucket,
            key=storage_key,
            data=file_bytes,
            content_type=file.content_type,
        )

        # 2. Parse text content based on format (offloaded to thread pool)
        if file_ext == "txt":
            text_content = file_bytes.decode("utf-8", errors="ignore")
        else:
            # Parse PDF in a separate thread to keep the event loop non-blocking
            text_content = await asyncio.to_thread(_parse_pdf_bytes, file_bytes)

        # 3. Trigger ingestion
        return await ingestion_service.ingest_content(
            source_type="manual_file",
            identifier=f"manual://{storage_key}",
            title=filename,
            text_content=text_content,
            raw_storage_key=storage_key,
        )

    except Exception as e:
        logger.error("File ingestion failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"File ingestion failed: {e}",
        )


@router.post(
    "/crawl-task/{task_id}",
    response_model=List[IngestResponse],
    status_code=status.HTTP_200_OK,
)
async def ingest_crawl_task_pages(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Imports and indexes all successfully crawled pages under a completed crawl task.
    """
    # 1. Retrieve all successful page runs using service layer
    pages = await ingestion_service.get_successful_pages_by_task(db, task_id)

    if not pages:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No successful crawled pages found for task {task_id}",
        )

    responses = []
    # 2. Sequentially process pages through the versioning/deduplication coordinator
    for page in pages:
        # Since this can run in a background worker context, yield control to the loop
        await asyncio.sleep(0.001)
        try:
            res = await ingestion_service.ingest_content(
                source_type="url",
                identifier=page.url,
                title=page.title,
                text_content=page.text_content or "",
            )
            responses.append(res)
        except Exception as e:
            logger.error("Failed to ingest crawled page %s: %s", page.url, e)
            # Skip page failures to prevent blocking the entire task import
            continue

    return responses


@router.get(
    "/metadata", response_model=IngestionStatusResponse, status_code=status.HTTP_200_OK
)
async def get_ingested_metadata(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retrieves ingestion status, including all ingested documents and
    a list of pending crawled pages or manual files that are new or updated.
    """
    try:
        return await ingestion_service.get_ingestion_status(db)
    except Exception as e:
        logger.error("Failed to query ingestion metadata: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Querying metadata failed: {e}",
        )
