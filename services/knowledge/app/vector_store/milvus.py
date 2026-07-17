import asyncio
import json
import logging
from typing import Any, Dict, List, Optional


from bson import ObjectId
from pymilvus import (
    AsyncMilvusClient,
    DataType,
    Function,
    FunctionType,
    AnnSearchRequest,
    RRFRanker,
)

from app.configs.dbs import settings as db_settings
from app.embeddings import BaseEmbeddingAdapter, get_embedding_adapter
from app.vector_store.interface import BaseVectorStore

logger = logging.getLogger(__name__)


class MilvusVectorStore(BaseVectorStore):
    """
    Concrete implementation of BaseVectorStore utilizing pymilvus AsyncMilvusClient.
    All Milvus operations are fully async — zero event-loop blocking.
    """

    def __init__(
        self,
        embedding_adapter: BaseEmbeddingAdapter | None = None,
        client: AsyncMilvusClient | None = None,
    ):
        self.embedding_adapter = embedding_adapter or get_embedding_adapter()
        self.uri = db_settings.milvus_uri
        self.collection_name = db_settings.milvus_collection
        self.dimension = db_settings.vector_dim
        self._client = client
        self._collection_ready = False
        self._loop = None

    @property
    def client(self) -> AsyncMilvusClient:
        current_loop = None
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        if self._client is None or (
            self._loop is not None and self._loop != current_loop
        ):
            self._client = AsyncMilvusClient(uri=self.uri)
            self._loop = current_loop
            self._collection_ready = False
        return self._client

    async def _ensure_collection(self) -> None:
        """
        Creates the target collection in Milvus if not already present.
        Configures HNSW index automatically. Idempotent — safe to call multiple times.
        """
        if self._collection_ready:
            try:
                if await self.client.has_collection(self.collection_name):
                    await self.client.load_collection(self.collection_name)
                    return
            except Exception:
                pass
            self._collection_ready = False

        try:
            collections = await self.client.list_collections()

            if self.collection_name not in collections:
                schema = self.client.create_schema(
                    auto_id=True,
                    enable_dynamic_field=True,
                    description="ChatBot Vector Chunks",
                )
                schema.add_field(
                    field_name="id",
                    datatype=DataType.INT64,
                    is_primary=True,
                    description="Primary ID",
                )
                schema.add_field(
                    field_name="version_id",
                    datatype=DataType.VARCHAR,
                    max_length=64,
                    description="Document Version ID",
                )
                schema.add_field(
                    field_name="chunk_index",
                    datatype=DataType.INT64,
                    description="Chunk Index",
                )
                schema.add_field(
                    field_name="content",
                    datatype=DataType.VARCHAR,
                    max_length=65535,
                    enable_analyzer=True,
                    enable_match=True,
                    description="Chunk Content",
                )
                schema.add_field(
                    field_name="sparse_vector",
                    datatype=DataType.SPARSE_FLOAT_VECTOR,
                    description="BM25 Sparse Vector",
                )

                bm25_function = Function(
                    name="bm25_text",
                    function_type=FunctionType.BM25,
                    input_field_names=["content"],
                    output_field_names=["sparse_vector"],
                )
                schema.add_function(bm25_function)
                schema.add_field(
                    field_name="embedding",
                    datatype=DataType.FLOAT_VECTOR,
                    dim=self.dimension,
                    description="Vector Embedding",
                )
                schema.add_field(
                    field_name="chunk_metadata",
                    datatype=DataType.VARCHAR,
                    max_length=65535,
                    description="Chunk Metadata",
                )

                index_params = self.client.prepare_index_params()
                index_params.add_index(
                    field_name="embedding",
                    metric_type="COSINE",
                    index_type="HNSW",
                    params={"M": 16, "efConstruction": 200, "efSearch": 64},
                )
                index_params.add_index(
                    field_name="sparse_vector",
                    index_type="SPARSE_INVERTED_INDEX",
                    metric_type="BM25",
                )

                await self.client.create_collection(
                    collection_name=self.collection_name,
                    schema=schema,
                    index_params=index_params,
                )
                logger.info(
                    "Created Milvus collection '%s' with HNSW index",
                    self.collection_name,
                )

            await self.client.load_collection(self.collection_name)
            self._collection_ready = True

        except Exception as e:
            logger.error(
                "Failed to setup Milvus collection '%s': %s", self.collection_name, e
            )
            raise

    async def insert_chunks(
        self,
        version_id: str,
        chunks: List[Dict[str, Any]],
        db_session: Optional[Any] = None,
    ) -> None:
        """
        Embeds chunks and writes them into Milvus asynchronously.
        """
        if not chunks:
            return

        await self._ensure_collection()

        contents = [chunk["content"] for chunk in chunks]
        vectors = await self.embedding_adapter.embed_documents(contents)

        if len(vectors) != len(chunks):
            raise ValueError(
                f"Embedding API returned mismatching number of vectors: "
                f"expected {len(chunks)}, got {len(vectors)}"
            )

        data = []
        for idx, chunk in enumerate(chunks):
            metadata_str = json.dumps(chunk.get("metadata") or {})
            data.append(
                {
                    "version_id": str(version_id),
                    "chunk_index": idx,
                    "content": chunk["content"],
                    "embedding": vectors[idx],
                    "chunk_metadata": metadata_str,
                }
            )

        try:
            await self.client.insert(collection_name=self.collection_name, data=data)
            logger.info(
                "Inserted %d chunks into Milvus collection '%s'",
                len(chunks),
                self.collection_name,
            )
        except Exception as e:
            logger.error("Failed to insert chunks into Milvus: %s", e)
            raise

    async def delete_chunks_by_document(
        self,
        document_id: str,
        db_session: Optional[Any] = None,
    ) -> None:
        """
        Evicts all chunks associated with any version of a document.
        """
        if db_session is None:
            from shared.database.mongo import MongoDBManager

            db_session = MongoDBManager.get_db()

        await self._ensure_collection()

        try:
            cursor = db_session.document_versions.find(
                {"document_id": str(document_id)}, {"_id": 1}
            )
            version_ids = []
            async for doc in cursor:
                version_ids.append(str(doc["_id"]))

            if version_ids:
                expr = f"version_id in [{','.join(f'"{v}"' for v in version_ids)}]"
                await self.client.delete(
                    collection_name=self.collection_name, filter=expr
                )
                logger.info(
                    "Deleted Milvus chunks matching version IDs: %s", version_ids
                )
        except Exception as e:
            logger.error(
                "Failed to delete Milvus chunks for document %s: %s", document_id, e
            )
            raise

    async def query_similarity(
        self,
        query_text: str,
        limit: int = 5,
        db_session: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """
        Queries similarity matches filtered by active version IDs.
        Joins metadata fields dynamically from the MongoDB document store.
        """
        if db_session is None:
            from shared.database.mongo import MongoDBManager

            db_session = MongoDBManager.get_db()

        await self._ensure_collection()

        # 1. Fetch active version IDs from MongoDB
        cursor = db_session.document_versions.find({"status": "active"}, {"_id": 1})
        active_ids = []
        async for doc in cursor:
            active_ids.append(str(doc["_id"]))

        if not active_ids:
            return []

        # 2. Embed the query text
        query_vector = await self.embedding_adapter.embed_query(query_text)

        # 3. Search Milvus with the active status filter
        filter_expr = f"version_id in [{','.join(f'"{v}"' for v in active_ids)}]"
        try:
            dense_req = AnnSearchRequest(
                data=[query_vector],
                anns_field="embedding",
                param={"metric_type": "COSINE"},
                limit=limit,
                expr=filter_expr,
            )
            sparse_req = AnnSearchRequest(
                data=[query_text],
                anns_field="sparse_vector",
                param={"metric_type": "BM25"},
                limit=limit,
                expr=filter_expr,
            )
            search_results = await self.client.hybrid_search(
                collection_name=self.collection_name,
                reqs=[dense_req, sparse_req],
                ranker=RRFRanker(),
                limit=limit,
                output_fields=[
                    "version_id",
                    "chunk_index",
                    "content",
                    "chunk_metadata",
                ],
                consistency_level="Strong",
            )
        except Exception as e:
            logger.error("Milvus search query failed: %s", e)
            return []

        if not search_results or not search_results[0]:
            return []

        hits = search_results[0]
        version_ids = list({hit["entity"]["version_id"] for hit in hits})

        # 4. Fetch document metadata from MongoDB to complete search details
        v_cursor = db_session.document_versions.find(
            {"_id": {"$in": [ObjectId(vid) for vid in version_ids]}}
        )
        version_to_doc: Dict[str, str] = {}
        doc_ids = []
        async for v_doc in v_cursor:
            doc_id = str(v_doc["document_id"])
            version_to_doc[str(v_doc["_id"])] = doc_id
            doc_ids.append(ObjectId(doc_id))

        d_cursor = db_session.ingested_documents.find({"_id": {"$in": doc_ids}})
        doc_details: Dict[str, Dict[str, str]] = {}
        async for d_doc in d_cursor:
            doc_details[str(d_doc["_id"])] = {
                "title": d_doc.get("title", "Unknown"),
                "source_identifier": d_doc.get("source_identifier", ""),
            }

        # 5. Format outputs matching interface contract
        unknown_doc = {"title": "Unknown", "source_identifier": ""}
        results = []
        for hit in hits:
            entity = hit["entity"]
            vid = entity["version_id"]
            doc_id = version_to_doc.get(vid)
            doc_info = (
                doc_details.get(str(doc_id), unknown_doc) if doc_id else unknown_doc
            )

            try:
                meta = json.loads(entity["chunk_metadata"])
            except Exception:
                meta = {}
            meta["version_id"] = vid

            results.append(
                {
                    "id": hit.get("id"),
                    "content": entity["content"],
                    "score": float(hit["distance"]),
                    "title": doc_info["title"],
                    "source": doc_info["source_identifier"],
                    "version_id": vid,
                    "metadata": meta,
                }
            )
        return results

    async def query_chunks(
        self,
        filter_expr: str,
        output_fields: List[str] | None = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """
        Generic async query for retrieving chunks by filter expression.
        Used for table reconstruction and sibling chunk lookups.
        """
        await self._ensure_collection()

        if output_fields is None:
            output_fields = ["version_id", "chunk_index", "content", "chunk_metadata"]

        try:
            results = await self.client.query(
                collection_name=self.collection_name,
                filter=filter_expr,
                output_fields=output_fields,
                limit=limit,
                consistency_level="Strong",
            )
            return results

        except Exception as e:
            logger.error("Milvus query failed (filter=%s): %s", filter_expr, e)
            return []

    async def close(self) -> None:
        """Gracefully close the async Milvus connection."""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            finally:
                self._client = None
                self._collection_ready = False
