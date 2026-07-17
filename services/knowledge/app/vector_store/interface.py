from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseVectorStore(ABC):
    """
    Abstract Base Class defining vector index operation contracts.
    Enables swapping vector storage engines (e.g. Milvus, Qdrant, Pinecone)
    without modifying ingestion service coordination logic.
    """

    @abstractmethod
    async def insert_chunks(
        self,
        version_id: str,
        chunks: List[Dict[str, Any]],
        db_session: Optional[Any] = None,
    ) -> None:
        """
        Embeds chunk contents and writes chunks + embeddings to the index.
        Reuses db_session if provided to participate in external transactions.
        """
        pass

    @abstractmethod
    async def delete_chunks_by_document(
        self,
        document_id: str,
        db_session: Optional[Any] = None,
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
        Calculates search embedding and returns top matches with parent context.
        Each match contains: content, score (similarity), title, and source identifier.
        """
        pass
