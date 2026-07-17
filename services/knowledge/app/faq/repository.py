import json
import logging
import re
from typing import Dict, List, Optional

import redis.asyncio as aioredis
from app.configs.dbs import settings as db_settings

logger = logging.getLogger(__name__)


class FAQRepository:
    """
    Interacts directly with Redis to query, aggregate, and manage FAQs.
    Uses async redis client and pipeline executions to guarantee
    high-performance, non-blocking queries.
    """

    def __init__(self, redis_url: str | None = None):
        self.redis_url = redis_url or db_settings.redis_url
        self._client: aioredis.Redis | None = None

    @property
    def client(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.from_url(self.redis_url, decode_responses=True)
        return self._client

    def _slugify(self, question: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", question.lower())
        return slug.strip("_")

    async def get_faq_by_question(self, question: str) -> Optional[dict]:
        """
        Retrieves a single FAQ object by matching slug representation.
        Runs in O(1) time.
        """
        try:
            slug = self._slugify(question)
            faq_key = f"faq:{slug}"
            data_str = await self.client.get(faq_key)
            if data_str:
                return json.loads(data_str)
        except Exception as e:
            logger.error("Failed to query Redis FAQ by question: %s", e)
        return None

    async def get_questions_by_category(self, category_id: str) -> List[dict]:
        """
        Retrieves all FAQ objects belonging to a category.
        Uses pipeline MGET to retrieve in O(1) execution trip.
        """
        try:
            category_set = f"category:{category_id}:faq_keys"
            keys = await self.client.smembers(category_set)
            if not keys:
                return []

            # Fetch all details in one pipelined call
            async with self.client.pipeline(transaction=False) as pipe:
                for key in keys:
                    pipe.get(key)
                results = await pipe.execute()

            faqs = []
            for r in results:
                if r:
                    faqs.append(json.loads(r))
            return faqs
        except Exception as e:
            logger.error(
                "Failed to query Redis FAQs by category %s: %s", category_id, e
            )
        return []

    async def get_category_summary(self) -> Dict[str, int]:
        """
        Aggregates FAQ counts per category.
        Runs in O(C) where C is number of configured categories.
        """
        from app.configs.yaml_loader import categories_config

        summary = {}
        try:
            categories = [cat["id"] for cat in categories_config]
            async with self.client.pipeline(transaction=False) as pipe:
                for cat_id in categories:
                    pipe.scard(f"category:{cat_id}:faq_keys")
                counts = await pipe.execute()
            for cat_id, count in zip(categories, counts):
                summary[cat_id] = count or 0
        except Exception as e:
            logger.error("Failed to query category counts summary: %s", e)
        return summary

    async def delete_faqs_by_category(self, category_id: str) -> bool:
        """
        Deletes all FAQs in a category.
        Cleans up individual FAQ keys, their entries in doc keys sets, and the category set.
        """
        try:
            category_set = f"category:{category_id}:faq_keys"
            keys = await self.client.smembers(category_set)
            if not keys:
                return True

            async with self.client.pipeline(transaction=False) as pipe:
                for key in keys:
                    # Fetch key details first to retrieve doc_id association for cleanup
                    data_str = await self.client.get(key)
                    if data_str:
                        try:
                            data = json.loads(data_str)
                            doc_id = data.get("doc_id")
                            if doc_id:
                                # Remove this key from doc index sets
                                pipe.srem(f"doc:{doc_id}:faq_keys", key)
                                # Remove from legacy doc cache list
                                pipe.lrem(f"doc:{doc_id}:faqs", 0, data_str)
                        except Exception:
                            pass
                    # Delete individual FAQ key
                    pipe.delete(key)

                # Delete category set key
                pipe.delete(category_set)
                await pipe.execute()
            return True
        except Exception as e:
            logger.error("Failed to delete FAQs in category %s: %s", category_id, e)
        return False

    async def close(self) -> None:
        """Gracefully close the async Redis connection."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
