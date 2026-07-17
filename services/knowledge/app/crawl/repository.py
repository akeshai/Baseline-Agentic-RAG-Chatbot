from datetime import datetime
from typing import Optional, List
from bson import ObjectId

from pymongo.asynchronous.database import AsyncDatabase
from app.crawl.models import CrawledPage, CrawlTask


def map_task(doc: dict) -> CrawlTask:
    doc_copy = dict(doc)
    doc_copy["id"] = str(doc_copy["_id"])
    return CrawlTask(**doc_copy)


def map_page(doc: dict) -> CrawledPage:
    doc_copy = dict(doc)
    doc_copy["id"] = str(doc_copy["_id"])
    return CrawledPage(**doc_copy)


class CrawlRepository:
    @staticmethod
    async def create_task(db: AsyncDatabase, user_id: str, start_url: str) -> CrawlTask:
        """
        Creates a new CrawlTask in the pending state.
        """
        task = CrawlTask(user_id=user_id, start_url=start_url, status="pending")
        task_dict = task.model_dump(exclude={"id"})
        res = await db.crawl_tasks.insert_one(task_dict)
        task.id = str(res.inserted_id)
        return task

    @staticmethod
    async def update_task_status(
        db: AsyncDatabase,
        task_id: str,
        status: str,
        error_message: Optional[str] = None,
    ) -> Optional[CrawlTask]:
        """
        Updates the execution status (and optional error message) of a CrawlTask.
        """
        update_fields = {"status": status, "updated_at": datetime.utcnow()}
        if error_message is not None:
            update_fields["error_message"] = error_message

        try:
            doc = await db.crawl_tasks.find_one_and_update(
                {"_id": ObjectId(task_id)},
                {"$set": update_fields},
                return_document=True,
            )
            if doc:
                return map_task(doc)
        except Exception:
            pass
        return None

    @staticmethod
    async def get_task_by_id(
        db: AsyncDatabase, task_id: str, user_id: str
    ) -> Optional[CrawlTask]:
        """
        Retrieves a crawl task by ID, verifying ownership by user_id.
        """
        try:
            doc = await db.crawl_tasks.find_one(
                {"_id": ObjectId(task_id), "user_id": user_id}
            )
            if doc:
                return map_task(doc)
        except Exception:
            pass
        return None

    @staticmethod
    async def list_tasks_by_user(db: AsyncDatabase, user_id: str) -> List[CrawlTask]:
        """
        Lists all crawl tasks belonging to a specific user, sorted newest first.
        """
        cursor = db.crawl_tasks.find({"user_id": user_id}).sort("created_at", -1)
        tasks = []
        async for doc in cursor:
            tasks.append(map_task(doc))
        return tasks

    @staticmethod
    async def increment_pages_counter(
        db: AsyncDatabase, task_id: str, status: str = "success"
    ) -> Optional[CrawlTask]:
        """
        Increments either the pages_crawled (success) or pages_failed counter of a task.
        """
        field = "pages_crawled" if status == "success" else "pages_failed"
        try:
            doc = await db.crawl_tasks.find_one_and_update(
                {"_id": ObjectId(task_id)},
                {"$inc": {field: 1}, "$set": {"updated_at": datetime.utcnow()}},
                return_document=True,
            )
            if doc:
                return map_task(doc)
        except Exception:
            pass
        return None

    @staticmethod
    async def create_crawled_page(db: AsyncDatabase, page: CrawledPage) -> CrawledPage:
        """
        Persists a CrawledPage record.
        """
        page_dict = page.model_dump(exclude={"id"})
        res = await db.crawled_pages.insert_one(page_dict)
        page.id = str(res.inserted_id)
        return page

    @staticmethod
    async def list_pages_by_task(db: AsyncDatabase, task_id: str) -> List[CrawledPage]:
        """
        Retrieves all pages crawled for a specific task.
        """
        cursor = db.crawled_pages.find({"task_id": task_id}).sort("created_at", 1)
        pages = []
        async for doc in cursor:
            pages.append(map_page(doc))
        return pages

    @staticmethod
    async def get_page_by_id(db: AsyncDatabase, page_id: str) -> Optional[CrawledPage]:
        """
        Retrieves a single crawled page record by ID.
        """
        try:
            doc = await db.crawled_pages.find_one({"_id": ObjectId(page_id)})
            if doc:
                return map_page(doc)
        except Exception:
            pass
        return None
