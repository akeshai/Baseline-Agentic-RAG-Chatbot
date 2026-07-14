from typing import Optional, Sequence
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.crawl.models import CrawlTask, CrawledPage


class CrawlRepository:
    @staticmethod
    async def create_task(db: AsyncSession, user_id: int, start_url: str) -> CrawlTask:
        """
        Creates a new CrawlTask in the pending state.
        """
        task = CrawlTask(user_id=user_id, start_url=start_url, status="pending")
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task

    @staticmethod
    async def update_task_status(
        db: AsyncSession, task_id: int, status: str, error_message: Optional[str] = None
    ) -> Optional[CrawlTask]:
        """
        Updates the execution status (and optional error message) of a CrawlTask.
        """
        stmt = select(CrawlTask).filter(CrawlTask.id == task_id)
        result = await db.execute(stmt)
        task = result.scalars().first()
        if task:
            task.status = status
            if error_message is not None:
                task.error_message = error_message
            await db.commit()
            await db.refresh(task)
        return task

    @staticmethod
    async def get_task_by_id(db: AsyncSession, task_id: int, user_id: int) -> Optional[CrawlTask]:
        """
        Retrieves a crawl task by ID, verifying ownership by user_id.
        """
        stmt = select(CrawlTask).filter(CrawlTask.id == task_id, CrawlTask.user_id == user_id)
        result = await db.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def list_tasks_by_user(db: AsyncSession, user_id: int) -> Sequence[CrawlTask]:
        """
        Lists all crawl tasks belonging to a specific user, sorted newest first.
        """
        stmt = select(CrawlTask).filter(CrawlTask.user_id == user_id).order_by(CrawlTask.created_at.desc())
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def increment_pages_counter(
        db: AsyncSession, task_id: int, status: str = "success"
    ) -> Optional[CrawlTask]:
        """
        Increments either the pages_crawled (success) or pages_failed counter of a task.
        """
        stmt = select(CrawlTask).filter(CrawlTask.id == task_id)
        result = await db.execute(stmt)
        task = result.scalars().first()
        if task:
            if status == "success":
                task.pages_crawled += 1
            else:
                task.pages_failed += 1
            await db.commit()
            await db.refresh(task)
        return task

    @staticmethod
    async def create_crawled_page(db: AsyncSession, page: CrawledPage) -> CrawledPage:
        """
        Persists a CrawledPage record.
        """
        db.add(page)
        await db.commit()
        await db.refresh(page)
        return page

    @staticmethod
    async def list_pages_by_task(db: AsyncSession, task_id: int) -> Sequence[CrawledPage]:
        """
        Retrieves all pages crawled for a specific task.
        """
        stmt = select(CrawledPage).filter(CrawledPage.task_id == task_id).order_by(CrawledPage.created_at.asc())
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_page_by_id(db: AsyncSession, page_id: int) -> Optional[CrawledPage]:
        """
        Retrieves a single crawled page record by ID.
        """
        stmt = select(CrawledPage).filter(CrawledPage.id == page_id)
        result = await db.execute(stmt)
        return result.scalars().first()
