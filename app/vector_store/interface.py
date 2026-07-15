from abc import ABC, abstractmethod
from typing import Any, Dict, List
from sqlalchemy.ext.asyncio import AsyncSession


class BaseVectorStore(ABC):
    """
    Abstract Base Class defining vector index operation contracts.
    Enables swapping vector storage engines (e.g. PGVector, Milvus, Qdrant, Pinecone)
    without modifying ingestion service coordination logic.
    """

    @abstractmethod
    async def insert_chunks(
        self,
        version_id: int,
        chunks: List[Dict[str, Any]],
        db_session: AsyncSession | None = None,
    ) -> None:
        """
        Embeds chunk contents and writes chunks + embeddings to the index.
        Reuses db_session if provided to participate in external transactions.
        """
        pass

    @abstractmethod
    async def delete_chunks_by_document(
        self,
        document_id: int,
        db_session: AsyncSession | None = None,
    ) -> None:
        """
        Clears all stored chunks/embeddings linked to any version of a document.
        Reuses db_session if provided to participate in external transactions.
        """
        pass

    @abstractmethod
    async def query_similarity(
        self,
        query_text: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Calculates search embedding and returns top matches.
        """
        pass
