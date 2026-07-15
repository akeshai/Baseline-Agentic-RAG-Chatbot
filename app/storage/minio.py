import asyncio
import io
import logging
from typing import Optional
from minio import Minio
from app.storage.interface import BaseObjectStorage

logger = logging.getLogger(__name__)


class MinioObjectStorage(BaseObjectStorage):
    """
    Production-grade object storage backend using MinIO SDK.
    Runs all blocking synchronous client operations in thread pools.
    """

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool = False,
    ):
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.secure = secure

        # Instantiate the MinIO client
        self.client = Minio(
            endpoint=self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure,
        )

    def _upload_sync(
        self, bucket: str, key: str, data: bytes, content_type: Optional[str]
    ) -> str:
        # Create bucket automatically if it does not exist
        if not self.client.bucket_exists(bucket):
            logger.info("Bucket '%s' not found. Creating bucket...", bucket)
            self.client.make_bucket(bucket)

        data_stream = io.BytesIO(data)
        length = len(data)
        self.client.put_object(
            bucket_name=bucket,
            object_name=key,
            data=data_stream,
            length=length,
            content_type=content_type or "application/octet-stream",
        )
        # Returns standard s3 resource URI representation
        return f"s3://{bucket}/{key}"

    async def upload_file(
        self, bucket: str, key: str, data: bytes, content_type: str = None
    ) -> str:
        return await asyncio.to_thread(
            self._upload_sync, bucket, key, data, content_type
        )

    def _download_sync(self, bucket: str, key: str) -> bytes:
        response = self.client.get_object(bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    async def download_file(self, bucket: str, key: str) -> bytes:
        return await asyncio.to_thread(self._download_sync, bucket, key)

    def _delete_sync(self, bucket: str, key: str) -> None:
        self.client.remove_object(bucket, key)

    async def delete_file(self, bucket: str, key: str) -> None:
        await asyncio.to_thread(self._delete_sync, bucket, key)

    def _list_sync(self, bucket: str, prefix: str) -> list:
        if not self.client.bucket_exists(bucket):
            return []

        objects = self.client.list_objects(bucket, prefix=prefix, recursive=True)
        files_info = []
        for obj in objects:
            if not obj.is_dir:
                files_info.append(
                    {
                        "key": obj.object_name,
                        "filename": obj.object_name.split("/")[-1],
                        "mtime": obj.last_modified,
                    }
                )
        return files_info

    async def list_files(self, bucket: str, prefix: str = "") -> list:
        return await asyncio.to_thread(self._list_sync, bucket, prefix)
