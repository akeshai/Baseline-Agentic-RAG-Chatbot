from app.vector_store.interface import BaseVectorStore
from app.vector_store.milvus import MilvusVectorStore


def get_vector_store() -> BaseVectorStore:
    """
    Factory helper that instantiates the active vector store engine based on configurations.
    """
    return MilvusVectorStore()
