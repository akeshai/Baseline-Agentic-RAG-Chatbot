from typing import List
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.auth.routes import get_current_user
from app.database import get_db
from app.crawl.repository import CrawlRepository
from app.crawl.schemas import (
    CrawlRequest,
    CrawledPageDetailResponse,
    CrawledPageResponse,
    CrawlTaskResponse,
)
from app.crawl.service import CrawlService

router = APIRouter(prefix="/crawl", tags=["Crawling"])


@router.post("", response_model=CrawlTaskResponse, status_code=status.HTTP_201_CREATED)
async def create_crawl_task(
    crawl_req: CrawlRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Triggers a background crawl or page refresh.
    Supports single or recursive BFS fetching strategies, and sequential or concurrent concurrency models.
    """
    return await CrawlService.start_crawl_task(
        db, current_user.id, crawl_req, background_tasks
    )


@router.get("/tasks", response_model=List[CrawlTaskResponse])
async def list_crawl_tasks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lists metadata for all crawling tasks initiated by the authenticated user.
    """
    return await CrawlRepository.list_tasks_by_user(db, current_user.id)


@router.get("/tasks/{task_id}", response_model=CrawlTaskResponse)
async def get_crawl_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retrieves execution progress and status of a specific crawl task.
    """
    task = await CrawlRepository.get_task_by_id(db, task_id, current_user.id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Crawl task not found or unauthorized",
        )
    return task


@router.get("/tasks/{task_id}/pages", response_model=List[CrawledPageResponse])
async def list_task_pages(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lists metadata (excluding heavy page content bodies) of all crawled pages for a task.
    """
    task = await CrawlRepository.get_task_by_id(db, task_id, current_user.id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Crawl task not found or unauthorized",
        )
    return await CrawlRepository.list_pages_by_task(db, task_id)


@router.get("/pages/{page_id}", response_model=CrawledPageDetailResponse)
async def get_page_detail(
    page_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retrieves full details of a crawled page, dynamically resolving raw HTML content
    from S3/Object Storage if stored off-DB.
    """
    page = await CrawlRepository.get_page_by_id(db, page_id)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Crawled page not found"
        )

    # Validate task ownership
    task = await CrawlRepository.get_task_by_id(db, page.task_id, current_user.id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own the parent crawl task",
        )

    # Dynamically resolve HTML content from global LocalObjectStorage if it was offloaded
    if page.html_content and page.html_content.startswith("object://"):
        try:
            uri = page.html_content[9:]
            parts = uri.split("/", 1)
            bucket = parts[0]
            key = parts[1]

            from app.configs.crawl import settings as crawl_settings
            from app.storage.local import LocalObjectStorage

            storage = LocalObjectStorage(root_dir=crawl_settings.object_storage_root)
            html_bytes = await storage.download_file(bucket, key)
            page.html_content = html_bytes.decode("utf-8")
        except Exception as e:
            page.html_content = f"[Failed to load content from Object Storage: {e}]"

    return page
