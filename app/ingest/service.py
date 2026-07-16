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
from app.configs.yaml_loader import categories_config
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
        doc_key = f"doc:{doc_id}:faqs"
        doc_keys_set = f"doc:{doc_id}:faq_keys"
        
        # Evict old ones first to prevent dead keys
        self._evict_faqs_sync(doc_id)
        
        pipe = self.client.pipeline()
        for faq in faqs:
            q = faq.get("question", "").strip()
            a = faq.get("answer", "").strip()
            cat = faq.get("category", "others").strip()
            if not q or not a:
                continue
            
            slug = re.sub(r'[^a-z0-9]+', '_', q.lower()).strip('_')
            faq_key = f"faq:{slug}"
            
            # Save individual FAQ JSON
            faq_data = {
                "question": q,
                "answer": a,
                "category": cat,
                "doc_id": doc_id
            }
            pipe.set(faq_key, json.dumps(faq_data))
            
            # Index under category set
            category_set = f"category:{cat}:faq_keys"
            pipe.sadd(category_set, faq_key)
            
            # Index under doc keys set
            pipe.sadd(doc_keys_set, faq_key)
            
            # Fallback legacy list
            pipe.rpush(doc_key, json.dumps(faq_data))
            
        pipe.execute()

    async def evict_faqs(self, doc_id: int) -> None:
        try:
            await asyncio.to_thread(self._evict_faqs_sync, doc_id)
            logger.info("Evicted FAQs from Redis for doc %d", doc_id)
        except Exception as e:
            logger.warning("Redis FAQ eviction failed (graceful bypass): %s", e)

    def _evict_faqs_sync(self, doc_id: int) -> None:
        doc_key = f"doc:{doc_id}:faqs"
        doc_keys_set = f"doc:{doc_id}:faq_keys"
        
        # Retrieve all individual FAQ keys matching this doc
        faq_keys = self.client.smembers(doc_keys_set)
        
        if faq_keys:
            pipe = self.client.pipeline()
            # For each key, get it to clean up the category set it belongs to
            for key in faq_keys:
                faq_data_str = self.client.get(key)
                if faq_data_str:
                    try:
                        faq_data = json.loads(faq_data_str)
                        cat = faq_data.get("category", "others")
                        pipe.srem(f"category:{cat}:faq_keys", key)
                    except Exception:
                        pass
                pipe.delete(key)
            pipe.execute()
            
        self.client.delete(doc_key)
        self.client.delete(doc_keys_set)


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
        self.categories = categories_config or []
        self.tag_to_category = self.build_tag_to_category_mapping()

    def build_tag_to_category_mapping(self) -> Dict[str, str]:
        """
        Dynamically compiles a tag-to-category mapping from the loaded categories configuration.
        Any new categories or keywords added to the config will automatically be mapped.
        """
        mapping = {}
        for cat in self.categories:
            cat_id = cat["id"]
            # Map category ID and variants
            mapping[cat_id] = cat_id
            mapping[cat_id.replace("_", "-")] = cat_id
            mapping[cat_id.replace("_", " ")] = cat_id
            
            # Map category name and variants
            name = cat.get("name", "").lower()
            if name:
                mapping[name] = cat_id
                mapping[name.replace(" ", "-")] = cat_id
                mapping[name.replace(" ", "_")] = cat_id
                # Singular/plural variants
                if name.endswith("s"):
                    mapping[name[:-1]] = cat_id
                else:
                    mapping[name + "s"] = cat_id

            # Map from keywords
            keywords = cat.get("keywords", {})
            for kw in keywords.get("primary", []) + keywords.get("secondary", []):
                kw_lower = kw.lower().strip()
                if not kw_lower:
                    continue
                mapping[kw_lower] = cat_id
                if " " in kw_lower:
                    mapping[kw_lower.replace(" ", "-")] = cat_id
                    mapping[kw_lower.replace(" ", "_")] = cat_id
                # Handle singular/plural variant
                if kw_lower.endswith("s"):
                    mapping[kw_lower[:-1]] = cat_id
                else:
                    mapping[kw_lower + "s"] = cat_id
                    
        return mapping

    def extract_tags_from_url(self, url: str) -> List[str]:
        """Generate tags from all URL path segments."""
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        if not path:
            return ["home"]

        # Split by / to get individual path segments
        segments = path.split("/")
        tags = []
        for segment in segments:
            if segment:
                tags.append(segment.lower())
        return tags

    def categorize_faq(self, tags: List[str], question: str, answer: str) -> str:
        """
        Hybrid categorization strategy:
        1. Base score derived from URL segments (tags).
        2. Content-based keyword density scoring (from primary and secondary keywords).
        """
        scores = {cat["id"]: 0 for cat in self.categories}
        
        # 1. Score based on URL path tags
        for tag in tags:
            matched_cat = self.tag_to_category.get(tag)
            if matched_cat and matched_cat in scores:
                scores[matched_cat] += 10  # High weight for URL segment match
                
        # 2. Score based on content keywords
        text = (question + " " + answer).lower()
        for cat in self.categories:
            cat_id = cat["id"]
            keywords = cat.get("keywords", {})
            # Check primary keywords (weight 2)
            for kw in keywords.get("primary", []):
                if kw.lower() in text:
                    scores[cat_id] += 2
                    
            # Check secondary keywords (weight 1)
            for kw in keywords.get("secondary", []):
                if kw.lower() in text:
                    scores[cat_id] += 1
                    
        # 3. Select category with highest score
        max_score = 0
        best_category = "others"
        for cat_id, score in scores.items():
            if score > max_score:
                max_score = score
                best_category = cat_id
                
        return best_category

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
        force_reingest: bool = False,
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
                    if doc.current_hash == content_hash and not force_reingest:
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
                    self._extract_faqs_from_text, text_content, identifier
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

    def _extract_faqs_from_text(self, text: str, url: str = "") -> List[Dict[str, str]]:
        """
        Extracts Q&A pairs.
        Supports HTML accordion layouts (AccordionItem Heading/Panel tags) as well as
        standard text-based Q/Question & A/Answer matching. All extracted FAQs
        are automatically categorized using a hybrid URL tag + keyword density score.
        """
        if not text:
            return []

        # 1. Try Accordion Item Extraction if HTML tags are present
        if "<" in text and ">" in text:
            try:
                from bs4 import BeautifulSoup
                import html2text
                soup = BeautifulSoup(text, "html.parser")
                faq_container = soup.find(id="product-faqs")
                accordion_items = []
                if faq_container:
                    accordion_items = faq_container.find_all(
                        "div", attrs={"data-accordion-component": "AccordionItem"}
                    )
                if accordion_items:
                    html_converter = html2text.HTML2Text()
                    html_converter.ignore_images = True
                    html_converter.ignore_emphasis = True
                    html_converter.ignore_links = True
                    html_converter.body_width = 0

                    faqs = []
                    tags = self.extract_tags_from_url(url)
                    for item in accordion_items:
                        heading_div = item.find(
                            "div",
                            attrs={"data-accordion-component": "AccordionItemHeading"},
                        )
                        if not heading_div:
                            continue
                        button = heading_div.find("div", class_="accordion__button")
                        if not button:
                            continue
                        question_text = button.get_text(strip=True)

                        panel = item.find(
                            "div", attrs={"data-accordion-component": "AccordionItemPanel"}
                        )
                        if not panel:
                            continue
                        
                        # Extract panel content HTML and convert to clean markdown
                        answer_html = "".join([str(child) for child in panel.contents]).strip()
                        answer_markdown = html_converter.handle(answer_html).strip()

                        # Categorize the FAQ
                        category = self.categorize_faq(tags, question_text, answer_markdown)
                        faqs.append({
                            "question": question_text,
                            "answer": answer_markdown,
                            "category": category
                        })
                    if faqs:
                        return faqs
            except Exception as e:
                logger.warning("Failed to extract accordion FAQs from HTML: %s", e)

        # 2. Fallback to standard text regex parser (strip HTML tags first if present)
        if "<" in text and ">" in text:
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(text, "html.parser")
                # Decompose non-content tags
                for tag in soup(["script", "style", "head", "title", "meta"]):
                    tag.decompose()
                text = soup.get_text(separator="\n")
            except Exception as e:
                logger.warning("Failed to parse HTML for FAQ extraction: %s", e)

        pattern = re.compile(
            r"\s*(?:Q|Question|Qn):\s*(.*?)\s*\n+\s*(?:A|Answer):\s*(.*?)(?=\n+\s*(?:Q|Question|Qn):|\Z)",
            re.IGNORECASE | re.DOTALL,
        )
        matches = pattern.findall(text)

        faqs = []
        tags = self.extract_tags_from_url(url)
        for q, a in matches:
            q_clean = re.sub(r"\s+", " ", q).strip()
            a_clean = re.sub(r"\s+", " ", a).strip()
            if q_clean and a_clean:
                category = self.categorize_faq(tags, q_clean, a_clean)
                faqs.append({
                    "question": q_clean,
                    "answer": a_clean,
                    "category": category
                })
        return faqs
