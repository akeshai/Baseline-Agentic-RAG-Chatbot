import asyncio
import hashlib
import json
import logging
import re
from typing import Any, Dict, List

import redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.configs.dbs import settings as db_settings
from app.database import SessionLocal
from app.ingest.chunker import TokenAwareChunker
from app.ingest.models import DocumentVersion, IngestedDocument
from app.storage import get_object_storage
from app.vector_store import BaseVectorStore, PGVectorStore

logger = logging.getLogger(__name__)


class FAQCache:
    """
    Handles caching and evicting Q&A lists inside Redis,
    offloading network actions to thread pools to maintain non-blocking execution.
    """

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self._client = None

    @property
    def client(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.from_url(self.redis_url, decode_responses=True)
        return self._client

    async def cache_faqs(self, doc_id: int, faqs: List[Dict[str, str]]) -> None:
        if not faqs:
            return
        try:
            await asyncio.to_thread(self._cache_faqs_sync, doc_id, faqs)
            logger.info(
                "Successfully cached %d FAQs in Redis for doc %d", len(faqs), doc_id
            )
        except Exception as e:
            logger.warning("Redis FAQ caching failed (graceful bypass): %s", e)

    def _cache_faqs_sync(self, doc_id: int, faqs: List[Dict[str, str]]) -> None:
        key = f"doc:{doc_id}:faqs"
        pipe = self.client.pipeline()
        pipe.delete(key)
        for faq in faqs:
            pipe.rpush(key, json.dumps(faq))
        pipe.execute()

    async def evict_faqs(self, doc_id: int) -> None:
        try:
            await asyncio.to_thread(self._evict_faqs_sync, doc_id)
            logger.info("Evicted FAQs from Redis for doc %d", doc_id)
        except Exception as e:
            logger.warning("Redis FAQ eviction failed (graceful bypass): %s", e)

    def _evict_faqs_sync(self, doc_id: int) -> None:
        key = f"doc:{doc_id}:faqs"
        self.client.delete(key)


class IngestionService:
    """
    Coordinating service managing document text/HTML ingestion, content versioning,
    SHA-256 deduplication, vector store index syncing, and Redis Q&A caching.
    """

    def __init__(
        self,
        vector_store: BaseVectorStore = None,
        chunker: TokenAwareChunker = None,
    ):
        self.vector_store = vector_store or PGVectorStore()
        self.chunker = chunker or TokenAwareChunker()
        self.faq_cache = FAQCache(redis_url=db_settings.redis_url)

    async def get_metadata(
        self,
        db_session: AsyncSession,
        identifier: str = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[IngestedDocument]:
        from sqlalchemy.orm import selectinload

        stmt = select(IngestedDocument).options(selectinload(IngestedDocument.versions))
        if identifier:
            stmt = stmt.where(IngestedDocument.source_identifier == identifier)
        stmt = stmt.limit(limit).offset(offset)
        result = await db_session.execute(stmt)
        return list(result.scalars().all())

    async def get_successful_pages_by_task(
        self,
        db_session: AsyncSession,
        task_id: int,
    ) -> List[Any]:
        from app.crawl.models import CrawledPage

        stmt = select(CrawledPage).where(
            CrawledPage.task_id == task_id, CrawledPage.status == "success"
        )
        result = await db_session.execute(stmt)
        return list(result.scalars().all())

    async def get_ingestion_status(
        self,
        db_session: AsyncSession,
    ) -> Dict[str, Any]:
        """
        Retrieves ingestion status by scanning storage assets (Local or MinIO) and comparing against database sync points.
        Uses task-level presence checks and file modification time updates to remain extremely fast.
        """
        from datetime import datetime, timezone

        from app.configs.crawl import settings as crawl_settings
        from app.crawl.models import CrawledPage
        from app.ingest.models import DocumentVersion

        def _to_naive_utc(dt: datetime) -> datetime:
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt

        # 1. Retrieve all ingested task IDs (JOIN query)
        stmt_tasks = (
            select(CrawledPage.task_id)
            .join(
                IngestedDocument, CrawledPage.url == IngestedDocument.source_identifier
            )
            .distinct()
        )
        res_tasks = await db_session.execute(stmt_tasks)
        ingested_task_ids = set(res_tasks.scalars().all())

        # Get configured storage instance
        object_storage = get_object_storage()

        # 2. Scan crawls/tasks/ in storage to list task directories
        crawls_status = []
        try:
            # List all html files under task prefix
            task_files = await object_storage.list_files(
                crawl_settings.raw_html_bucket, "tasks"
            )

            # Map task ID to its latest mtime
            task_mtime_map = {}
            for f in task_files:
                parts = f["key"].split("/")
                if len(parts) >= 2 and parts[0] == "tasks" and parts[1].isdigit():
                    t_id = int(parts[1])
                    mtime = _to_naive_utc(f["mtime"])
                    if t_id not in task_mtime_map or mtime > task_mtime_map[t_id]:
                        task_mtime_map[t_id] = mtime

            # Populate crawls status
            for task_id in sorted(task_mtime_map.keys()):
                status = (
                    "ingested" if task_id in ingested_task_ids else "pending_ingestion"
                )
                crawls_status.append(
                    {
                        "task_id": task_id,
                        "status": status,
                        "created_at": task_mtime_map[task_id],
                    }
                )
        except Exception as e:
            logger.error("Failed to list crawl tasks in storage: %s", e)

        # 3. Retrieve all ingested manual files keys
        stmt_files = select(
            DocumentVersion.raw_storage_key, DocumentVersion.created_at
        ).where(
            DocumentVersion.raw_storage_key.like("manual/uploads/%"),
            DocumentVersion.status == "active",
        )
        res_files = await db_session.execute(stmt_files)
        ingested_files_map = {r[0]: _to_naive_utc(r[1]) for r in res_files.all()}

        # 4. Scan manual/uploads/ in storage to list files
        files_status = []
        try:
            files_list = await object_storage.list_files(
                crawl_settings.raw_html_bucket, "manual/uploads"
            )

            for f in files_list:
                key = f["key"]
                mtime = _to_naive_utc(f["mtime"])

                if key not in ingested_files_map:
                    status = "pending_ingestion"
                else:
                    db_created_at = ingested_files_map[key]
                    # If file has been updated in storage after database ingestion, it is pending
                    if mtime > db_created_at:
                        status = "pending_ingestion"
                    else:
                        status = "ingested"

                files_status.append(
                    {
                        "filename": f["filename"],
                        "status": status,
                        "last_modified_at": mtime,
                    }
                )
        except Exception as e:
            logger.error("Failed to list manual files in storage: %s", e)

        return {
            "crawls": crawls_status,
            "manual_files": files_status,
        }

    async def ingest_content(
        self,
        source_type: str,
        identifier: str,
        title: str = None,
        text_content: str = "",
        raw_storage_key: str = None,
        is_html: bool = False,
    ) -> Dict[str, Any]:
        """
        Coordinates document parsing, deduplication, chunking, embedding updates, and Q&A sync.
        All CPU-intensive tasks (hashing, BeautifulSoup parsing) are offloaded to worker threads.
        Executes within a single transaction boundary, committing only when both DB metadata
        and vector store insertions complete successfully.
        """
        # 1. Compute text payload SHA-256 in a non-blocking worker thread
        content_hash = await asyncio.to_thread(self._compute_hash, text_content)

        # 2. Query document database using an isolated session
        async with SessionLocal() as db_session:
            try:
                stmt = select(IngestedDocument).where(
                    IngestedDocument.source_identifier == identifier
                )
                result = await db_session.execute(stmt)
                doc = result.scalars().first()

                if doc:
                    # Case A: Document already exists. Verify if content changed
                    if doc.current_hash == content_hash:
                        logger.info(
                            "Ingestion skipped for %s (hash matches latest)", identifier
                        )
                        return {
                            "document_id": doc.id,
                            "version": doc.current_version,
                            "action": "skipped",
                            "hash": content_hash,
                        }

                    # Case B: Content hash changed. Bump version and update
                    old_version = doc.current_version
                    new_version = old_version + 1

                    # Update document meta
                    doc.current_version = new_version
                    doc.current_hash = content_hash
                    doc.title = title or doc.title

                    # Mark older versions as superseded
                    await db_session.execute(
                        update(DocumentVersion)
                        .where(
                            DocumentVersion.document_id == doc.id,
                            DocumentVersion.status == "active",
                        )
                        .values(status="superseded")
                    )

                    # Insert new version record
                    new_ver_rec = DocumentVersion(
                        document_id=doc.id,
                        version=new_version,
                        content_hash=content_hash,
                        raw_storage_key=raw_storage_key,
                        text_content=text_content,
                        status="active",
                    )
                    db_session.add(new_ver_rec)
                    await db_session.flush()

                    doc_id = doc.id
                    version_num = new_version
                    version_rec_id = new_ver_rec.id
                    action_taken = "updated"

                else:
                    # Case C: First time document is registered
                    new_doc = IngestedDocument(
                        source_type=source_type,
                        source_identifier=identifier,
                        title=title,
                        current_version=1,
                        current_hash=content_hash,
                    )
                    db_session.add(new_doc)
                    await db_session.flush()

                    new_ver_rec = DocumentVersion(
                        document_id=new_doc.id,
                        version=1,
                        content_hash=content_hash,
                        raw_storage_key=raw_storage_key,
                        text_content=text_content,
                        status="active",
                    )
                    db_session.add(new_ver_rec)
                    await db_session.flush()

                    doc_id = new_doc.id
                    version_num = 1
                    version_rec_id = new_ver_rec.id
                    action_taken = "created"

                # 3. Synchronize vector index chunks
                if action_taken == "updated":
                    # Delete old chunks belonging to previous versions of this document
                    await self.vector_store.delete_chunks_by_document(
                        doc_id, db_session=db_session
                    )
                    # Evict old FAQs from Redis
                    await self.faq_cache.evict_faqs(doc_id)

                # Generate chunk splits
                chunks = await self.chunker.chunk_document(
                    text_or_html=text_content,
                    is_html=is_html,
                    source_title=title or identifier,
                )

                if chunks:
                    # Bulk embed and upload new chunks to the vector store adapter
                    await self.vector_store.insert_chunks(
                        version_rec_id, chunks, db_session=db_session
                    )

                # 4. Parse & extract FAQs from clean text to cache in Redis
                faqs = await asyncio.to_thread(
                    self._extract_faqs_from_text, text_content
                )
                if faqs:
                    await self.faq_cache.cache_faqs(doc_id, faqs)

                # Commit only when everything succeeds
                await db_session.commit()

                return {
                    "document_id": doc_id,
                    "version": version_num,
                    "action": action_taken,
                    "hash": content_hash,
                }

            except Exception as e:
                # Rollback relational database state if any stage (including vector embedding) fails
                await db_session.rollback()
                logger.error("Failed to ingest content: %s", e)
                raise e

    def _compute_hash(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _extract_faqs_from_text(self, text: str) -> List[Dict[str, str]]:
        """
        Regex-based parser looking for standard Q&A pairs (e.g. Q: Question, A: Answer).
        """
        if not text:
            return []

        # Matches Q/Question: followed by A/Answer: lines with optional leading whitespace
        pattern = re.compile(
            r"\s*(?:Q|Question|Qn):\s*(.*?)\s*\n+\s*(?:A|Answer):\s*(.*?)(?=\n+\s*(?:Q|Question|Qn):|\Z)",
            re.IGNORECASE | re.DOTALL,
        )
        matches = pattern.findall(text)

        faqs = []
        for q, a in matches:
            q_clean = re.sub(r"\s+", " ", q).strip()
            a_clean = re.sub(r"\s+", " ", a).strip()
            if q_clean and a_clean:
                faqs.append({"question": q_clean, "answer": a_clean})
        return faqs
