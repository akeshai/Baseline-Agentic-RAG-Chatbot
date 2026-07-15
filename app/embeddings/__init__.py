from app.embeddings.base import BaseEmbeddingAdapter
from app.embeddings.oci import OCIEmbeddingAdapter

_embedding_adapter: BaseEmbeddingAdapter | None = None


def get_embedding_adapter() -> BaseEmbeddingAdapter:
    """
    Returns a singleton instance of the embedding adapter.
    """
    global _embedding_adapter
    if _embedding_adapter is None:
        _embedding_adapter = OCIEmbeddingAdapter()
    return _embedding_adapter


__all__ = ["BaseEmbeddingAdapter", "OCIEmbeddingAdapter", "get_embedding_adapter"]
