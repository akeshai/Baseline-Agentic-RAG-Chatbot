import hashlib
import random
from typing import List, Dict, Any
from sqlalchemy import select, delete
from app.database import SessionLocal
from app.ingest.models import DocumentVersion, DocumentChunk
from app.configs.dbs import settings as db_settings
from app.vector_store.interface import BaseVectorStore


class PGVectorStore(BaseVectorStore):
    """
    Concrete implementation of BaseVectorStore utilizing PostgreSQL PGVector
    with automatic JSON-fallback operations for SQLite local development and testing.
    """

    async def _get_embedding(self, text: str) -> List[float]:
        """
        Generates a deterministic mock embedding vector based on text content.
        This provides consistent behavior for RAG indices and unit tests without external API calls.
        """
        dim = db_settings.vector_dim
        if not text:
            return [0.0] * dim
        
        # Create deterministic seed from content
        seed = int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16) % 100000
        rng = random.Random(seed)
        return [rng.uniform(-1.0, 1.0) for _ in range(dim)]

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

        async with SessionLocal() as session:
            for idx, chunk in enumerate(chunks):
                vector = await self._get_embedding(chunk["content"])
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
            query_vector = await self._get_embedding(query_text)
            dialect = session.bind.dialect.name

            if dialect == "postgresql":
                # Compile-safe Cosine Distance operator
                stmt = select(DocumentChunk).order_by(
                    DocumentChunk.embedding.op("<=>")(query_vector)
                ).limit(limit)
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
