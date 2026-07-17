import json
import logging
from typing import Any, Dict, List, Tuple

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.vector_store.milvus import MilvusVectorStore

logger = logging.getLogger(__name__)


class SearchService:
    """
    Application service coordinating similarity search, chunk lookups,
    and table resolution across Milvus and MongoDB.
    """

    def __init__(self, vector_store: MilvusVectorStore):
        self._vector_store = vector_store

    async def search_by_query(
        self,
        query_text: str,
        db: AsyncDatabase,
        limit: int = 5,
        resolve_full_tables: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Runs similarity search and optionally resolves full table markdown.
        """
        raw_results = await self._vector_store.query_similarity(
            query_text=query_text, limit=limit, db_session=db
        )

        if not resolve_full_tables:
            return raw_results

        resolved = []
        for item in raw_results:
            meta = item.get("metadata") or {}
            version_id = meta.get("version_id") or item.get("version_id")
            content = item["content"]

            if meta.get("type") == "table_row" and version_id and "table_index" in meta:
                content = await self._resolve_table_content(
                    version_id, meta["table_index"], content
                )

            resolved.append({**item, "content": content})
        return resolved

    async def search_by_chunk_ids(
        self,
        chunk_ids: List[int],
        db: AsyncDatabase,
        resolve_full_tables: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves specific chunks by primary key and enriches with document metadata.
        """
        id_list = ",".join(str(cid) for cid in chunk_ids)
        hits = await self._vector_store.query_chunks(
            filter_expr=f"id in [{id_list}]",
            output_fields=["version_id", "chunk_index", "content", "chunk_metadata"],
        )

        if not hits:
            return []

        version_ids = list(
            {hit.get("version_id") for hit in hits if hit.get("version_id")}
        )
        version_to_doc, doc_details = await self._fetch_doc_metadata(db, version_ids)

        unknown_doc = {"title": "Unknown", "source_identifier": ""}
        results = []

        for hit in hits:
            chunk_id = hit.get("id")
            content = hit.get("content", "")
            version_id = hit.get("version_id")

            try:
                meta = json.loads(hit.get("chunk_metadata", "{}"))
            except Exception:
                meta = {}

            doc_id = version_to_doc.get(version_id)
            doc_info = (
                doc_details.get(str(doc_id), unknown_doc) if doc_id else unknown_doc
            )

            if (
                resolve_full_tables
                and meta.get("type") == "table_row"
                and "table_index" in meta
                and version_id
            ):
                content = await self._resolve_table_content(
                    version_id, meta["table_index"], content
                )

            results.append(
                {
                    "id": chunk_id,
                    "content": content,
                    "score": 1.0,
                    "title": doc_info["title"],
                    "source": doc_info["source_identifier"],
                    "metadata": {
                        "document_id": doc_id,
                        "version_id": version_id,
                        **meta,
                    },
                }
            )

        return results

    async def _fetch_doc_metadata(
        self, db: AsyncDatabase, version_ids: List[str]
    ) -> Tuple[Dict[str, str], Dict[str, Dict[str, str]]]:
        """
        Fetches document metadata from MongoDB for a set of version IDs.
        """
        version_to_doc: Dict[str, str] = {}
        doc_ids = []

        v_cursor = db.document_versions.find(
            {"_id": {"$in": [ObjectId(vid) for vid in version_ids]}}
        )
        async for v_doc in v_cursor:
            doc_id = str(v_doc["document_id"])
            version_to_doc[str(v_doc["_id"])] = doc_id
            doc_ids.append(ObjectId(doc_id))

        doc_details: Dict[str, Dict[str, str]] = {}
        if doc_ids:
            d_cursor = db.ingested_documents.find({"_id": {"$in": doc_ids}})
            async for d_doc in d_cursor:
                doc_details[str(d_doc["_id"])] = {
                    "title": d_doc.get("title", "Unknown"),
                    "source_identifier": d_doc.get("source_identifier", ""),
                }

        return version_to_doc, doc_details

    async def _resolve_table_content(
        self, version_id: str, table_index: int, fallback_content: str
    ) -> str:
        """
        Resolves full table markdown by querying Milvus for sibling table row chunks.
        """
        siblings = await self._vector_store.query_chunks(
            filter_expr=f'version_id == "{version_id}"',
            output_fields=["content", "chunk_metadata"],
        )

        sibling_rows = []

        for sib in siblings:
            try:
                sib_meta = json.loads(sib.get("chunk_metadata", "{}"))
            except Exception:
                sib_meta = {}
            if (
                sib_meta.get("type") == "table_row"
                and sib_meta.get("table_index") == table_index
            ):
                sibling_rows.append(
                    (sib_meta.get("row_index", 0), sib.get("content", ""))
                )

        sibling_rows.sort(key=lambda x: x[0])
        sibling_contents = [r[1] for r in sibling_rows]

        if not sibling_contents:
            return fallback_content

        first_content = sibling_contents[0]
        parts = first_content.split("\nRow ")
        context_str = parts[0]

        headers = None
        dividers = None
        data_rows = []

        for sc in sibling_contents:
            lines = sc.split("\n")
            table_lines = [
                line.strip() for line in lines if line.strip().startswith("|")
            ]
            if len(table_lines) >= 3:
                if headers is None:
                    headers = table_lines[0]
                    dividers = table_lines[1]
                data_rows.append(table_lines[2])

        if headers and dividers and data_rows:
            table_md = "\n".join([headers, dividers] + data_rows)
            return f"{context_str}\nFull Table:\n{table_md}"

        return "\n\n".join(sibling_contents)
