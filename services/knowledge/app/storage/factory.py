from app.configs.crawl import settings as crawl_settings
from app.storage.interface import BaseObjectStorage
from app.storage.local import LocalObjectStorage
from app.storage.minio import MinioObjectStorage


def get_object_storage() -> BaseObjectStorage:
    """
    Factory function instantiating the configured object storage backend
    (either LocalObjectStorage or MinioObjectStorage).
    """
    provider = crawl_settings.object_storage_provider.lower().strip()
    if provider == "minio":
        return MinioObjectStorage(
            endpoint=crawl_settings.minio_endpoint,
            access_key=crawl_settings.minio_access_key,
            secret_key=crawl_settings.minio_secret_key,
            secure=crawl_settings.minio_secure,
        )
    return LocalObjectStorage(root_dir=crawl_settings.object_storage_root)
