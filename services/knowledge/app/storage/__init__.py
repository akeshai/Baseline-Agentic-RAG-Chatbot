from app.storage.factory import get_object_storage
from app.storage.interface import BaseObjectStorage
from app.storage.local import LocalObjectStorage
from app.storage.minio import MinioObjectStorage

__all__ = [
    "BaseObjectStorage",
    "LocalObjectStorage",
    "MinioObjectStorage",
    "get_object_storage",
]
