from typing import Any, Dict, List

from sqlalchemy import delete, select, type_coerce, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.configs.dbs import settings as db_settings
from app.database import SessionLocal, engine
from app.ingest.models import DocumentChunk, DocumentVersion, IngestedDocument
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

        # Determine database dialect
        if db_session is not None:
            dialect = db_session.bind.dialect.name
        else:
            dialect = engine.dialect.name
        is_postgres = dialect == "postgresql"

        if db_session is not None:
            # Use shared transaction session
            for idx, chunk in enumerate(chunks):
                db_chunk = DocumentChunk(
                    version_id=version_id,
                    chunk_index=idx,
                    content=chunk["content"],
                    embedding=vectors[idx],
                    tsv_content=func.to_tsvector("english", chunk["content"])
                    if is_postgres
                    else chunk["content"],
                    chunk_metadata=chunk.get("metadata"),
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
                        tsv_content=func.to_tsvector("english", chunk["content"])
                        if is_postgres
                        else chunk["content"],
                        chunk_metadata=chunk.get("metadata"),
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
        or simple table scans on SQLite fallback databases, returning only active chunks.
        """
        async with SessionLocal() as session:
            query_vector = await self.embedding_adapter.embed_query(query_text)
            dialect = session.bind.dialect.name

            if dialect == "postgresql":
                from pgvector.sqlalchemy import Vector

                # 1. Define vector search CTE (top 20 candidates)
                vector_search = (
                    select(
                        DocumentChunk.id,
                        func.row_number().over(
                            order_by=type_coerce(DocumentChunk.embedding, Vector(db_settings.vector_dim)).cosine_distance(query_vector)
                        ).label("rank")
                    )
                    .join(DocumentVersion, DocumentChunk.version_id == DocumentVersion.id)
                    .where(DocumentVersion.status == "active")
                    .limit(20)
                ).cte("vector_search")

                # 2. Define FTS keyword search CTE (top 20 candidates)
                fts_query = func.plainto_tsquery("english", query_text)
                fts_search = (
                    select(
                        DocumentChunk.id,
                        func.row_number().over(
                            order_by=func.ts_rank(DocumentChunk.tsv_content, fts_query).desc()
                        ).label("rank")
                    )
                    .join(DocumentVersion, DocumentChunk.version_id == DocumentVersion.id)
                    .where(
                        (DocumentVersion.status == "active") &
                        (DocumentChunk.tsv_content.op("@@")(fts_query))
                    )
                    .limit(20)
                ).cte("fts_search")

                # 3. Perform RRF join to merge candidates
                stmt = (
                    select(
                        DocumentChunk.id,
                        DocumentChunk.content,
                        IngestedDocument.title,
                        IngestedDocument.source_identifier,
                        (
                            func.coalesce(1.0 / (60.0 + vector_search.c.rank), 0.0) +
                            func.coalesce(1.0 / (60.0 + fts_search.c.rank), 0.0)
                        ).label("rrf_score"),
                        DocumentChunk.version_id,
                        DocumentChunk.chunk_metadata,
                    )
                    .join(DocumentVersion, DocumentChunk.version_id == DocumentVersion.id)
                    .join(IngestedDocument, DocumentVersion.document_id == IngestedDocument.id)
                    .join(vector_search, DocumentChunk.id == vector_search.c.id, isouter=True)
                    .join(fts_search, DocumentChunk.id == fts_search.c.id, isouter=True)
                    .where(
                        (DocumentVersion.status == "active") &
                        ((vector_search.c.id.isnot(None)) | (fts_search.c.id.isnot(None)))
                    )
                    .order_by(
                        (
                            func.coalesce(1.0 / (60.0 + vector_search.c.rank), 0.0) +
                            func.coalesce(1.0 / (60.0 + fts_search.c.rank), 0.0)
                        ).desc()
                    )
                    .limit(limit)
                )

                result = await session.execute(stmt)
                return [
                    {
                        "id": r[0],
                        "content": r[1],
                        "score": float(r[4]),  # Combined RRF Score
                        "title": r[2],
                        "source": r[3],
                        "version_id": r[5],
                        "metadata": r[6],
                    }
                    for r in result.all()
                ]
            else:
                # SQLite fallback join query for test databases
                stmt = (
                    select(
                        DocumentChunk.id,
                        DocumentChunk.content,
                        DocumentChunk.embedding,
                        IngestedDocument.title,
                        IngestedDocument.source_identifier,
                        DocumentChunk.version_id,
                        DocumentChunk.chunk_metadata,
                    )
                    .join(DocumentVersion, DocumentChunk.version_id == DocumentVersion.id)
                    .join(IngestedDocument, DocumentVersion.document_id == IngestedDocument.id)
                    .where(DocumentVersion.status == "active")
                )
                result = await session.execute(stmt)
                rows = result.all()

                matches = []
                for chunk_id, content, emb_raw, title, source, version_id, chunk_metadata in rows:
                    if not emb_raw or len(emb_raw) != len(query_vector):
                        continue
                    # Compute cosine similarity manually for testing compatibility
                    dot_product = sum(a * b for a, b in zip(emb_raw, query_vector))
                    norm_a = sum(a * a for a in emb_raw) ** 0.5
                    norm_b = sum(b * b for b in query_vector) ** 0.5
                    similarity = (
                        dot_product / (norm_a * norm_b) if norm_a and norm_b else 0.0
                    )
                    matches.append(
                        {
                            "id": chunk_id,
                            "content": content,
                            "score": float(similarity),
                            "title": title,
                            "source": source,
                            "version_id": version_id,
                            "metadata": chunk_metadata,
                        }
                    )

                matches.sort(key=lambda x: x["score"], reverse=True)
                return matches[:limit]
