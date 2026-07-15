from typing import Any, Dict, List

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

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
        db_session: AsyncSession | None = None,
    ) -> None:
        """
        Generates embeddings and bulk inserts chunks linked to a document version.
        Reuses db_session if provided to avoid nested transaction locking on SQLite.
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

        if db_session is not None:
            # Use shared transaction session
            for idx, chunk in enumerate(chunks):
                db_chunk = DocumentChunk(
                    version_id=version_id,
                    chunk_index=idx,
                    content=chunk["content"],
                    embedding=vectors[idx],
                )
                db_session.add(db_chunk)
            await db_session.flush()
        else:
            # Create isolated transaction session
            async with SessionLocal() as session:
                for idx, chunk in enumerate(chunks):
                    db_chunk = DocumentChunk(
                        version_id=version_id,
                        chunk_index=idx,
                        content=chunk["content"],
                        embedding=vectors[idx],
                    )
                    session.add(db_chunk)
                await session.commit()

    async def delete_chunks_by_document(
        self,
        document_id: int,
        db_session: AsyncSession | None = None,
    ) -> None:
        """
        Evicts all chunks associated with any version of a document.
        Reuses db_session if provided to participate in external transactions.
        """
        if db_session is not None:
            # Fetch version IDs
            version_stmt = select(DocumentVersion.id).where(
                DocumentVersion.document_id == document_id
            )
            version_result = await db_session.execute(version_stmt)
            version_ids = version_result.scalars().all()

            if version_ids:
                # Delete chunks
                del_stmt = delete(DocumentChunk).where(
                    DocumentChunk.version_id.in_(version_ids)
                )
                await db_session.execute(del_stmt)
        else:
            async with SessionLocal() as session:
                version_stmt = select(DocumentVersion.id).where(
                    DocumentVersion.document_id == document_id
                )
                version_result = await session.execute(version_stmt)
                version_ids = version_result.scalars().all()

                if version_ids:
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
                    select(
                        DocumentChunk.content,
                        DocumentChunk.embedding.cosine_distance(query_vector).label(
                            "distance"
                        ),
                    )
                    .order_by("distance")
                    .limit(limit)
                )
                result = await session.execute(stmt)
                return [
                    {"content": r[0], "distance": float(r[1])}
                    for r in result.all()
                ]
            else:
                # Fallback implementation for SQLite (non-production testing)
                stmt = select(DocumentChunk.content, DocumentChunk.embedding)
                result = await session.execute(stmt)
                rows = result.all()

                matches = []
                for content, emb_raw in rows:
                    if not emb_raw or len(emb_raw) != len(query_vector):
                        continue
                    # Compute cosine similarity manually for testing compatibility
                    dot_product = sum(a * b for a, b in zip(emb_raw, query_vector))
                    norm_a = sum(a * a for a in emb_raw) ** 0.5
                    norm_b = sum(b * b for b in query_vector) ** 0.5
                    similarity = (
                        dot_product / (norm_a * norm_b)
                        if norm_a and norm_b
                        else 0.0
                    )
                    distance = 1.0 - similarity
                    matches.append({"content": content, "distance": distance})

                matches.sort(key=lambda x: x["distance"])
                return matches[:limit]
