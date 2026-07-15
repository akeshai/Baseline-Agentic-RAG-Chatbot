from abc import ABC, abstractmethod
from typing import List, Dict, Any


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
    ) -> None:
        """
        Embeds chunk contents and writes chunks + embeddings to the index.
        """
        pass

    @abstractmethod
    async def delete_chunks_by_document(
        self,
        document_id: int,
    ) -> None:
        """
        Clears all stored chunks/embeddings linked to any version of a document.
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
