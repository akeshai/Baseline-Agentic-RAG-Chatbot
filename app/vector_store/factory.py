from app.configs.dbs import settings as db_settings
from app.vector_store.interface import BaseVectorStore
from app.vector_store.pgvector import PGVectorStore


def get_vector_store() -> BaseVectorStore:
    """
    Factory helper that instantiates the active vector store engine based on configurations.
    """
    if db_settings.vector_store_type == "milvus":
        from app.vector_store.milvus import MilvusVectorStore

        return MilvusVectorStore()
    return PGVectorStore()
