import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List

import redis

from app.configs.dbs import settings as db_settings
from app.configs.yaml_loader import categories_config
from app.ingest.chunker import TokenAwareChunker
from app.storage import get_object_storage
from app.vector_store import BaseVectorStore, get_vector_store

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

    async def cache_faqs(self, doc_id: str, faqs: List[Dict[str, str]]) -> None:
        if not faqs:
            return
        try:
            await asyncio.to_thread(self._cache_faqs_sync, str(doc_id), faqs)
            logger.info(
                "Successfully cached %d FAQs in Redis for doc %s", len(faqs), doc_id
            )
        except Exception as e:
            logger.warning("Redis FAQ caching failed (graceful bypass): %s", e)

    def _cache_faqs_sync(self, doc_id: str, faqs: List[Dict[str, str]]) -> None:
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

            slug = re.sub(r"[^a-z0-9]+", "_", q.lower()).strip("_")
            faq_key = f"faq:{slug}"

            # Save individual FAQ JSON
            faq_data = {"question": q, "answer": a, "category": cat, "doc_id": doc_id}
            pipe.set(faq_key, json.dumps(faq_data))

            # Index under category set
            category_set = f"category:{cat}:faq_keys"
            pipe.sadd(category_set, faq_key)

            # Index under doc keys set
            pipe.sadd(doc_keys_set, faq_key)

            # Fallback legacy list
            pipe.rpush(doc_key, json.dumps(faq_data))

        pipe.execute()

    async def evict_faqs(self, doc_id: str) -> None:
        try:
            await asyncio.to_thread(self._evict_faqs_sync, str(doc_id))
            logger.info("Evicted FAQs from Redis for doc %s", doc_id)
        except Exception as e:
            logger.warning("Redis FAQ eviction failed (graceful bypass): %s", e)

    def _evict_faqs_sync(self, doc_id: str) -> None:
        doc_key = f"doc:{doc_id}:faqs"
        doc_keys_set = f"doc:{doc_id}:faq_keys"

        # Retrieve all individual FAQ keys matching this doc
        faq_keys = self.client.smembers(doc_keys_set)

        if faq_keys:
            pipe = self.client.pipeline()
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
        self.vector_store = vector_store or get_vector_store()
        self.chunker = chunker or TokenAwareChunker()
        self.faq_cache = FAQCache(redis_url=db_settings.redis_url)
        self.categories = categories_config or []
        self.tag_to_category = self.build_tag_to_category_mapping()

    def build_tag_to_category_mapping(self) -> Dict[str, str]:
        """
        Dynamically compiles a tag-to-category mapping from the loaded categories configuration.
        """
        mapping = {}
        for cat in self.categories:
            cat_id = cat["id"]
            mapping[cat_id] = cat_id
            mapping[cat_id.replace("_", "-")] = cat_id
            mapping[cat_id.replace("_", " ")] = cat_id

            name = cat.get("name", "").lower()
            if name:
                mapping[name] = cat_id
                mapping[name.replace(" ", "-")] = cat_id
                mapping[name.replace(" ", "_")] = cat_id
                if name.endswith("s"):
                    mapping[name[:-1]] = cat_id
                else:
                    mapping[name + "s"] = cat_id

            keywords = cat.get("keywords", {})
            for kw in keywords.get("primary", []) + keywords.get("secondary", []):
                kw_lower = kw.lower().strip()
                if not kw_lower:
                    continue
                mapping[kw_lower] = cat_id
                if " " in kw_lower:
                    mapping[kw_lower.replace(" ", "-")] = cat_id
                    mapping[kw_lower.replace(" ", "_")] = cat_id
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

        segments = path.split("/")
        tags = []
        for segment in segments:
            if segment:
                tags.append(segment.lower())
        return tags

    def categorize_faq(self, tags: List[str], question: str, answer: str) -> str:
        """
        Hybrid categorization strategy.
        """
        scores = {cat["id"]: 0 for cat in self.categories}

        for tag in tags:
            matched_cat = self.tag_to_category.get(tag)
            if matched_cat and matched_cat in scores:
                scores[matched_cat] += 10

        text = (question + " " + answer).lower()
        for cat in self.categories:
            cat_id = cat["id"]
            keywords = cat.get("keywords", {})
            for kw in keywords.get("primary", []):
                if kw.lower() in text:
                    scores[cat_id] += 2

            for kw in keywords.get("secondary", []):
                if kw.lower() in text:
                    scores[cat_id] += 1

        max_score = 0
        best_category = "others"
        for cat_id, score in scores.items():
            if score > max_score:
                max_score = score
                best_category = cat_id

        return best_category

    async def get_metadata(
        self,
        db_session: Any,
        identifier: str = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        query = {}
        if identifier:
            query["source_identifier"] = identifier
        cursor = db_session.ingested_documents.find(query).skip(offset).limit(limit)
        docs = []
        async for doc in cursor:
            doc["id"] = str(doc["_id"])
            version_cursor = db_session.document_versions.find(
                {"document_id": doc["id"]}
            )
            versions = []
            async for v_doc in version_cursor:
                v_doc["id"] = str(v_doc["_id"])
                versions.append(v_doc)
            doc["versions"] = versions
            docs.append(doc)
        return docs

    async def get_successful_pages_by_task(
        self,
        db_session: Any,
        task_id: str,
    ) -> List[Dict[str, Any]]:
        cursor = db_session.crawled_pages.find(
            {"task_id": str(task_id), "status": "success"}
        )
        pages = []
        async for doc in cursor:
            doc["id"] = str(doc["_id"])
            pages.append(doc)
        return pages

    async def get_ingestion_status(
        self,
        db_session: Any,
    ) -> Dict[str, Any]:
        """
        Retrieves ingestion status by scanning storage assets (Local or MinIO) and comparing against database sync points.
        """
        from datetime import timezone

        from app.configs.crawl import settings as crawl_settings

        def _to_naive_utc(dt: datetime) -> datetime:
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt

        cursor_docs = db_session.ingested_documents.find({}, {"source_identifier": 1})
        ingested_urls = set()
        async for doc in cursor_docs:
            if doc.get("source_identifier"):
                ingested_urls.add(doc.get("source_identifier"))

        cursor_pages = db_session.crawled_pages.find(
            {"url": {"$in": list(ingested_urls)}}, {"task_id": 1}
        )
        ingested_task_ids = set()
        async for p in cursor_pages:
            if p.get("task_id"):
                ingested_task_ids.add(p.get("task_id"))

        object_storage = get_object_storage()
        crawls_status = []
        try:
            task_files = await object_storage.list_files(
                crawl_settings.raw_html_bucket, "tasks"
            )

            task_mtime_map = {}
            for f in task_files:
                parts = f["key"].split("/")
                if len(parts) >= 2 and parts[0] == "tasks":
                    t_id = parts[1]
                    mtime = _to_naive_utc(f["mtime"])
                    if t_id not in task_mtime_map or mtime > task_mtime_map[t_id]:
                        task_mtime_map[t_id] = mtime

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

        version_cursor = db_session.document_versions.find(
            {"raw_storage_key": {"$regex": "^manual/uploads/"}, "status": "active"}
        )
        ingested_files_map = {}
        async for v in version_cursor:
            key = v.get("raw_storage_key")
            created_at = v.get("created_at")
            if key and created_at:
                ingested_files_map[key] = _to_naive_utc(created_at)

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
        db_session: Any = None,
    ) -> Dict[str, Any]:
        """
        Coordinates document parsing, deduplication, chunking, embedding updates, and Q&A sync in MongoDB.
        """
        if db_session is None:
            from shared.database.mongo import MongoDBManager

            db_session = MongoDBManager.get_db()

        content_hash = await asyncio.to_thread(self._compute_hash, text_content)

        try:
            doc = await db_session.ingested_documents.find_one(
                {"source_identifier": identifier}
            )

            if doc:
                if doc.get("current_hash") == content_hash and not force_reingest:
                    logger.info(
                        "Ingestion skipped for %s (hash matches latest)", identifier
                    )
                    return {
                        "document_id": str(doc["_id"]),
                        "version": doc.get("current_version", 1),
                        "action": "skipped",
                        "hash": content_hash,
                    }

                old_version = doc.get("current_version", 1)
                new_version = old_version + 1

                await db_session.ingested_documents.update_one(
                    {"_id": doc["_id"]},
                    {
                        "$set": {
                            "current_version": new_version,
                            "current_hash": content_hash,
                            "title": title or doc.get("title"),
                            "updated_at": datetime.utcnow(),
                        }
                    },
                )

                await db_session.document_versions.update_many(
                    {"document_id": str(doc["_id"]), "status": "active"},
                    {"$set": {"status": "superseded"}},
                )

                new_ver_rec = {
                    "document_id": str(doc["_id"]),
                    "version": new_version,
                    "content_hash": content_hash,
                    "raw_storage_key": raw_storage_key,
                    "text_content": text_content,
                    "status": "active",
                    "created_at": datetime.utcnow(),
                }
                res_ver = await db_session.document_versions.insert_one(new_ver_rec)

                doc_id = str(doc["_id"])
                version_num = new_version
                version_rec_id = str(res_ver.inserted_id)
                action_taken = "updated"

            else:
                new_doc = {
                    "source_type": source_type,
                    "source_identifier": identifier,
                    "title": title,
                    "current_version": 1,
                    "current_hash": content_hash,
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                }
                res_doc = await db_session.ingested_documents.insert_one(new_doc)
                doc_id = str(res_doc.inserted_id)

                new_ver_rec = {
                    "document_id": doc_id,
                    "version": 1,
                    "content_hash": content_hash,
                    "raw_storage_key": raw_storage_key,
                    "text_content": text_content,
                    "status": "active",
                    "created_at": datetime.utcnow(),
                }
                res_ver = await db_session.document_versions.insert_one(new_ver_rec)

                version_num = 1
                version_rec_id = str(res_ver.inserted_id)
                action_taken = "created"

            if action_taken == "updated":
                await self.vector_store.delete_chunks_by_document(
                    doc_id, db_session=db_session
                )
                await self.faq_cache.evict_faqs(doc_id)

            chunks = await self.chunker.chunk_document(
                text_or_html=text_content,
                is_html=is_html,
                source_title=title or identifier,
            )

            if chunks:
                await self.vector_store.insert_chunks(
                    version_rec_id, chunks, db_session=db_session
                )

            faqs = await asyncio.to_thread(
                self._extract_faqs_from_text, text_content, identifier
            )
            if faqs:
                await self.faq_cache.cache_faqs(doc_id, faqs)

            return {
                "document_id": doc_id,
                "version": version_num,
                "action": action_taken,
                "hash": content_hash,
            }

        except Exception as e:
            logger.error("Failed to ingest content: %s", e)
            raise e

    def _compute_hash(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _extract_faqs_from_text(self, text: str, url: str = "") -> List[Dict[str, str]]:
        """
        Extracts Q&A pairs inside section id="product-faqs".
        """
        if not text:
            return []

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
                            "div",
                            attrs={"data-accordion-component": "AccordionItemPanel"},
                        )
                        if not panel:
                            continue

                        answer_html = "".join(
                            [str(child) for child in panel.contents]
                        ).strip()
                        answer_markdown = html_converter.handle(answer_html).strip()

                        category = self.categorize_faq(
                            tags, question_text, answer_markdown
                        )
                        faqs.append(
                            {
                                "question": question_text,
                                "answer": answer_markdown,
                                "category": category,
                            }
                        )
                    if faqs:
                        return faqs
            except Exception as e:
                logger.warning("Failed to extract accordion FAQs from HTML: %s", e)

        if "<" in text and ">" in text:
            try:
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(text, "html.parser")
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
                faqs.append(
                    {"question": q_clean, "answer": a_clean, "category": category}
                )
        return faqs
