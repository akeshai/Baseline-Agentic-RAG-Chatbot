import json
import logging
from typing import Any, Dict, List

from pymilvus import MilvusClient

from app.configs.dbs import settings as db_settings
from app.database import SessionLocal
from app.embeddings import BaseEmbeddingAdapter, get_embedding_adapter
from app.ingest.models import DocumentVersion, IngestedDocument
from app.vector_store.interface import BaseVectorStore
from sqlalchemy import select

logger = logging.getLogger(__name__)


class MilvusVectorStore(BaseVectorStore):
    """
    Concrete implementation of BaseVectorStore utilizing Milvus (pymilvus MilvusClient).
    Stores vector embeddings and handles document version status filtering dynamically.
    """

    def __init__(
        self,
        embedding_adapter: BaseEmbeddingAdapter | None = None,
        client: MilvusClient | None = None,
    ):
        self.embedding_adapter = embedding_adapter or get_embedding_adapter()
        self.uri = db_settings.milvus_uri
        self.collection_name = db_settings.milvus_collection
        self.dimension = db_settings.vector_dim
        self._client = client

    @property
    def client(self) -> MilvusClient:
        if self._client is None:
            self._client = MilvusClient(uri=self.uri)
            self._ensure_collection()
        return self._client

    def _ensure_collection(self) -> None:
        """
        Creates the target collection in Milvus if not already present.
        Configures indexes automatically.
        """
        try:
            if not self._client.has_collection(self.collection_name):
                # Create schema definition with fields: id, version_id, chunk_index, content, embedding, chunk_metadata
                from pymilvus import DataType
                schema = self._client.create_schema(
                    auto_id=True,
                    enable_dynamic_field=True,
                    description="ChatBot Vector Chunks"
                )
                schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True, description="Primary ID")
                schema.add_field(field_name="version_id", datatype=DataType.INT64, description="Document Version ID")
                schema.add_field(field_name="chunk_index", datatype=DataType.INT64, description="Chunk Index")
                schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=65535, description="Chunk Content")
                schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=self.dimension, description="Vector Embedding")
                schema.add_field(field_name="chunk_metadata", datatype=DataType.VARCHAR, max_length=65535, description="Chunk Metadata")
                
                index_params = self._client.prepare_index_params()
                index_params.add_index(
                    field_name="embedding",
                    metric_type="COSINE",
                    index_type="AUTOINDEX",
                )
                
                self._client.create_collection(
                    collection_name=self.collection_name,
                    schema=schema,
                    index_params=index_params
                )
                logger.info("Successfully created Milvus collection '%s'", self.collection_name)
            else:
                # Always load collection to memory for search readiness
                self._client.load_collection(self.collection_name)
        except Exception as e:
            logger.error("Failed to setup Milvus collection '%s': %s", self.collection_name, e)

    async def insert_chunks(
        self,
        version_id: int,
        chunks: List[Dict[str, Any]],
        db_session: Any = None,
    ) -> None:
        """
        Embeds chunks and writes them into Milvus.
        """
        if not chunks:
            return

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
            data.append({
                "version_id": version_id,
                "chunk_index": idx,
                "content": chunk["content"],
                "embedding": vectors[idx],
                "chunk_metadata": metadata_str
            })

        try:
            self.client.insert(collection_name=self.collection_name, data=data)
            logger.info("Inserted %d chunks into Milvus collection '%s'", len(chunks), self.collection_name)
        except Exception as e:
            logger.error("Failed to insert chunks into Milvus: %s", e)
            raise e

    async def delete_chunks_by_document(
        self,
        document_id: int,
        db_session: Any = None,
    ) -> None:
        """
        Evicts all chunks associated with any version of a document.
        """
        # Resolve version IDs for this document
        from sqlalchemy.ext.asyncio import AsyncSession
        close_session = False
        if db_session is None:
            db_session = SessionLocal()
            close_session = True

        try:
            version_stmt = select(DocumentVersion.id).where(
                DocumentVersion.document_id == document_id
            )
            version_result = await db_session.execute(version_stmt)
            version_ids = version_result.scalars().all()

            if version_ids:
                # Delete from Milvus
                expr = f"version_id in [{','.join(map(str, version_ids))}]"
                self.client.delete(collection_name=self.collection_name, filter=expr)
                logger.info("Deleted Milvus chunks matching version IDs: %s", version_ids)
        except Exception as e:
            logger.error("Failed to delete Milvus chunks for document %d: %s", document_id, e)
            raise e
        finally:
            if close_session:
                await db_session.close()

    async def query_similarity(
        self,
        query_text: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Queries similarity matches filtered by active version IDs.
        Joins metadata fields dynamically from the SQL document store.
        """
        # 1. Fetch active version IDs from the SQL database
        async with SessionLocal() as db_session:
            active_stmt = select(DocumentVersion.id).where(
                DocumentVersion.status == "active"
            )
            active_res = await db_session.execute(active_stmt)
            active_ids = active_res.scalars().all()

            if not active_ids:
                return []

            # 2. Embed the query text
            query_vector = await self.embedding_adapter.embed_query(query_text)

            # 3. Search Milvus with the active status filter
            filter_expr = f"version_id in [{','.join(map(str, active_ids))}]"
            try:
                search_results = self.client.search(
                    collection_name=self.collection_name,
                    data=[query_vector],
                    limit=limit,
                    filter=filter_expr,
                    output_fields=["version_id", "chunk_index", "content", "chunk_metadata"],
                    metric_type="COSINE"
                )
            except Exception as e:
                logger.error("Milvus search query failed: %s", e)
                return []

            if not search_results or not search_results[0]:
                return []

            hits = search_results[0]
            version_ids = list({hit["entity"]["version_id"] for hit in hits})

            # 4. Fetch documents metadata to complete the search details
            doc_stmt = (
                select(
                    DocumentVersion.id,
                    IngestedDocument.title,
                    IngestedDocument.source_identifier
                )
                .join(IngestedDocument, DocumentVersion.document_id == IngestedDocument.id)
                .where(DocumentVersion.id.in_(version_ids))
            )
            db_res = await db_session.execute(doc_stmt)
            doc_map = {
                row.id: {"title": row.title, "source_identifier": row.source_identifier}
                for row in db_res.all()
            }

            # 5. Format outputs matching interface contract
            results = []
            for hit in hits:
                entity = hit["entity"]
                vid = entity["version_id"]
                doc_info = doc_map.get(vid, {"title": "Unknown", "source_identifier": ""})
                
                try:
                    meta = json.loads(entity["chunk_metadata"])
                except Exception:
                    meta = {}

                # Calculate standard cosine similarity score
                results.append({
                    "id": hit.get("id"),
                    "content": entity["content"],
                    "score": float(hit["distance"]),  # Cosine distance/similarity
                    "title": doc_info["title"],
                    "source": doc_info["source_identifier"],
                    "metadata": meta
                })
            return results
