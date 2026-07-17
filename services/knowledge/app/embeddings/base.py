from abc import ABC, abstractmethod
from typing import List


class BaseEmbeddingAdapter(ABC):
    """
    Abstract Base Class defining the interface for embedding generation.
    """

    @abstractmethod
    async def embed_query(self, text: str) -> List[float]:
        """
        Generates embedding vector for a single query.
        """
        pass

    @abstractmethod
    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        Generates embedding vectors for multiple texts in a single batched call.
        """
        pass
