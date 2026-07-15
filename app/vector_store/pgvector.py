from typing import Any, Dict, List

from sqlalchemy import delete, select

from app.configs.dbs import settings as db_settings
from app.database import SessionLocal
from app.ingest.models import DocumentChunk, DocumentVersion
from app.vector_store.interface import BaseVectorStore
from app.embeddings import BaseEmbeddingAdapter, get_embedding_adapter


class PGVectorStore(BaseVectorStore):
    """
    Concrete implementation of BaseVectorStore utilizing PostgreSQL PGVector
    with automatic JSON-fallback operations for SQLite local development and testing.
    """

    def __init__(self, embedding_adapter: BaseEmbeddingAdapter | None = None):
        self.embedding_adapter = embedding_adapter or get_embedding_adapter()

    async def insert_chunks(
        self,
        version_id: int,
        chunks: List[Dict[str, Any]],
    ) -> None:
        """
        Generates embeddings and bulk inserts chunks linked to a document version.
        """
        if not chunks:
            return

        contents = [chunk["content"] for chunk in chunks]
        vectors = await self.embedding_adapter.embed_documents(contents)

        async with SessionLocal() as session:
            for idx, chunk in enumerate(chunks):
                # Fallback to zero vector if embedding generation returned fewer results
                vector = (
                    vectors[idx]
                    if idx < len(vectors)
                    else [0.0] * db_settings.vector_dim
                )
                db_chunk = DocumentChunk(
                    version_id=version_id,
                    chunk_index=idx,
                    content=chunk["content"],
                    embedding=vector,
                )
                session.add(db_chunk)
            await session.commit()

    async def delete_chunks_by_document(
        self,
        document_id: int,
    ) -> None:
        """
        Evicts all chunks associated with any version of a document.
        """
        async with SessionLocal() as session:
            # 1. Fetch version IDs for the target document
            version_stmt = select(DocumentVersion.id).where(
                DocumentVersion.document_id == document_id
            )
            version_result = await session.execute(version_stmt)
            version_ids = version_result.scalars().all()

            if version_ids:
                # 2. Delete chunks belonging to these versions
                del_stmt = delete(DocumentChunk).where(
                    DocumentChunk.version_id.in_(version_ids)
                )
                await session.execute(del_stmt)
                await session.commit()

    async def query_similarity(
        self,
        query_text: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Returns top semantic matches using cosine distance (<=>) on PostgreSQL,
        or simple table scans on SQLite fallback databases.
        """
        async with SessionLocal() as session:
            query_vector = await self.embedding_adapter.embed_query(query_text)
            dialect = session.bind.dialect.name

            if dialect == "postgresql":
                # Compile-safe Cosine Distance operator
                stmt = (
                    select(DocumentChunk)
                    .order_by(DocumentChunk.embedding.op("<=>")(query_vector))
                    .limit(limit)
                )
            else:
                # SQLite fallback: returns matches (simulates database without vector extension)
                stmt = select(DocumentChunk).limit(limit)

            result = await session.execute(stmt)
            chunks = result.scalars().all()

            return [
                {
                    "id": c.id,
                    "version_id": c.version_id,
                    "chunk_index": c.chunk_index,
                    "content": c.content,
                }
                for c in chunks
            ]
