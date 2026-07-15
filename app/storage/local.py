import asyncio
from pathlib import Path

from app.storage.interface import BaseObjectStorage


class LocalObjectStorage(BaseObjectStorage):
    """
    Simulates object storage by saving files to the local file system.
    Safely handles subdirectories and prevents path traversal attacks.
    """

    def __init__(self, root_dir: str = "storage_buckets"):
        self.root_dir = Path(root_dir).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _get_path(self, bucket: str, key: str) -> Path:
        """
        Determines the safe filesystem path for a given bucket and key.
        Prevents directory traversal (e.g. key containing '../../').
        """
        # Normalize target path
        target_path = Path(
            self.root_dir / bucket / key.replace("\\", "/").lstrip("/")
        ).resolve()
        # Verify the path is within self.root_dir
        if not str(target_path).startswith(str(self.root_dir)):
            raise ValueError(
                f"Directory traversal detected for bucket={bucket}, key={key}"
            )
        return target_path

    async def upload_file(
        self, bucket: str, key: str, data: bytes, content_type: str = None
    ) -> str:
        target_path = self._get_path(bucket, key)

        def _write():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_path, "wb") as f:
                f.write(data)

        await asyncio.to_thread(_write)
        # Return path as standard string with forward slashes for cross-platform compatibility
        relative_path = target_path.relative_to(self.root_dir)
        return str(relative_path.as_posix())

    async def download_file(self, bucket: str, key: str) -> bytes:
        target_path = self._get_path(bucket, key)
        if not target_path.exists():
            raise FileNotFoundError(f"Object not found: {bucket}/{key}")

        def _read():
            with open(target_path, "rb") as f:
                return f.read()

        return await asyncio.to_thread(_read)

    async def delete_file(self, bucket: str, key: str) -> None:
        target_path = self._get_path(bucket, key)
        if not target_path.exists():
            return

        def _delete():
            target_path.unlink()
            # Clean up empty parent directories up to root_dir
            parent = target_path.parent
            while parent != self.root_dir and parent.exists():
                try:
                    # Will fail if directory is not empty, which is desired
                    parent.rmdir()
                    parent = parent.parent
                except OSError:
                    break

        await asyncio.to_thread(_delete)

    async def list_files(self, bucket: str, prefix: str = "") -> list:
        from datetime import datetime

        target_dir = self._get_path(bucket, prefix)
        if not target_dir.exists():
            return []

        def _list():
            files_info = []
            for p in target_dir.rglob("*"):
                if p.is_file():
                    rel_key = p.relative_to(self.root_dir / bucket).as_posix()
                    stat = p.stat()
                    files_info.append(
                        {
                            "key": rel_key,
                            "filename": p.name,
                            "mtime": datetime.fromtimestamp(stat.st_mtime),
                        }
                    )
            return files_info

        return await asyncio.to_thread(_list)
