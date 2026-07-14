from abc import ABC, abstractmethod


class BaseObjectStorage(ABC):
    """
    Abstract interface for production-grade object storage (e.g. S3, MinIO, or local disk).
    Reusable across the entire application for file uploads, manuals, and crawler assets.
    """

    @abstractmethod
    async def upload_file(
        self, bucket: str, key: str, data: bytes, content_type: str = None
    ) -> str:
        """
        Uploads file data to the specified bucket and key.
        Returns the resolved URI or path of the uploaded file.
        """
        pass

    @abstractmethod
    async def download_file(self, bucket: str, key: str) -> bytes:
        """
        Downloads file data from the specified bucket and key.
        Returns the file content in bytes.
        """
        pass

    @abstractmethod
    async def delete_file(self, bucket: str, key: str) -> None:
        """
        Deletes the file at the specified bucket and key.
        """
        pass
