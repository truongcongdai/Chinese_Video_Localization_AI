"""
Storage Backend Abstraction

Supports both local disk and cloud storage (S3/R2) for video files.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional, BinaryIO, Tuple
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


class StorageBackend:
    """Abstract base class for storage backends."""

    def save_file(self, file_obj: BinaryIO, path: str) -> str:
        """Save a file and return the storage path/URL."""
        raise NotImplementedError

    def get_file(self, path: str) -> Optional[bytes]:
        """Get file contents."""
        raise NotImplementedError

    def delete_file(self, path: str) -> bool:
        """Delete a file."""
        raise NotImplementedError

    def file_exists(self, path: str) -> bool:
        """Check if file exists."""
        raise NotImplementedError

    def get_url(self, path: str) -> str:
        """Get public URL for a file."""
        raise NotImplementedError


class LocalStorageBackend(StorageBackend):
    """Local disk storage backend."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, path: str) -> Path:
        """Resolve a path relative to base directory."""
        return self.base_dir / path.lstrip("/")

    def save_file(self, file_obj: BinaryIO, path: str) -> str:
        """Save file to local disk."""
        dest = self._resolve_path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        with dest.open("wb") as f:
            shutil.copyfileobj(file_obj, f)

        logger.info(f"Saved file to local storage: {dest}")
        return str(dest)

    def get_file(self, path: str) -> Optional[bytes]:
        """Get file contents from local disk."""
        dest = self._resolve_path(path)
        if not dest.exists():
            return None
        return dest.read_bytes()

    def delete_file(self, path: str) -> bool:
        """Delete file from local disk."""
        dest = self._resolve_path(path)
        if not dest.exists():
            return False
        dest.unlink(missing_ok=True)
        logger.info(f"Deleted file from local storage: {dest}")
        return True

    def file_exists(self, path: str) -> bool:
        """Check if file exists on local disk."""
        return self._resolve_path(path).exists()

    def get_url(self, path: str) -> str:
        """For local storage, return file:// URL."""
        return f"file://{self._resolve_path(path).absolute()}"


class S3StorageBackend(StorageBackend):
    """S3/R2 cloud storage backend using boto3."""

    def __init__(
        self,
        bucket_name: str,
        access_key: str,
        secret_key: str,
        endpoint_url: Optional[str] = None,
        region: str = "us-east-1",
        public_url_base: Optional[str] = None,
    ):
        self.bucket_name = bucket_name
        self.public_url_base = public_url_base

        try:
            import boto3
            from botocore.client import Config

            self.s3_client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
                config=Config(signature_version="s3v4"),
            )

            # Test connection
            self.s3_client.head_bucket(Bucket=bucket_name)
            logger.info(f"Connected to S3 bucket: {bucket_name}")
        except ImportError:
            logger.error("boto3 not installed, S3 storage unavailable")
            raise
        except Exception as e:
            logger.error(f"Failed to connect to S3: {e}")
            raise

    def save_file(self, file_obj: BinaryIO, path: str) -> str:
        """Save file to S3."""
        try:
            self.s3_client.upload_fileobj(
                file_obj,
                self.bucket_name,
                path.lstrip("/"),
                ExtraArgs={"ContentType": "video/mp4"} if path.endswith(".mp4") else {},
            )
            logger.info(f"Saved file to S3: {path}")
            return path
        except Exception as e:
            logger.error(f"Failed to save file to S3: {e}")
            raise

    def get_file(self, path: str) -> Optional[bytes]:
        """Get file contents from S3."""
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=path.lstrip("/"))
            return response["Body"].read()
        except self.s3_client.exceptions.NoSuchKey:
            return None
        except Exception as e:
            logger.error(f"Failed to get file from S3: {e}")
            return None

    def delete_file(self, path: str) -> bool:
        """Delete file from S3."""
        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=path.lstrip("/"))
            logger.info(f"Deleted file from S3: {path}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete file from S3: {e}")
            return False

    def file_exists(self, path: str) -> bool:
        """Check if file exists in S3."""
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=path.lstrip("/"))
            return True
        except self.s3_client.exceptions.NoSuchKey:
            return False
        except Exception as e:
            logger.error(f"Failed to check file existence in S3: {e}")
            return False

    def get_url(self, path: str) -> str:
        """Get public URL for S3 file."""
        if self.public_url_base:
            return urljoin(self.public_url_base, path.lstrip("/"))

        # Generate presigned URL if no public URL base
        try:
            return self.s3_client.generate_presigned_url(
                "get_object",
                Bucket=self.bucket_name,
                Key=path.lstrip("/"),
                ExpiresIn=3600,  # 1 hour
            )
        except Exception as e:
            logger.error(f"Failed to generate presigned URL: {e}")
            return ""


def get_storage_backend() -> StorageBackend:
    """
    Get the configured storage backend based on environment variables.

    Environment variables:
    - STORAGE_TYPE: "local" or "s3" (default: local)
    - STORAGE_BASE_DIR: Base directory for local storage (default: ./local_data/storage)
    - S3_BUCKET_NAME: S3 bucket name
    - S3_ACCESS_KEY: S3 access key
    - S3_SECRET_KEY: S3 secret key
    - S3_ENDPOINT_URL: S3 endpoint URL (for R2, MinIO, etc.)
    - S3_REGION: S3 region (default: us-east-1)
    - S3_PUBLIC_URL_BASE: Base URL for public access (optional)
    """
    storage_type = os.getenv("STORAGE_TYPE", "local").lower()

    if storage_type == "s3":
        bucket_name = os.getenv("S3_BUCKET_NAME")
        access_key = os.getenv("S3_ACCESS_KEY")
        secret_key = os.getenv("S3_SECRET_KEY")

        if not all([bucket_name, access_key, secret_key]):
            logger.warning("S3 configuration incomplete, falling back to local storage")
            storage_type = "local"
        else:
            return S3StorageBackend(
                bucket_name=bucket_name,
                access_key=access_key,
                secret_key=secret_key,
                endpoint_url=os.getenv("S3_ENDPOINT_URL"),
                region=os.getenv("S3_REGION", "us-east-1"),
                public_url_base=os.getenv("S3_PUBLIC_URL_BASE"),
            )

    # Default to local storage
    base_dir = Path(os.getenv("STORAGE_BASE_DIR", "./local_data/storage"))
    return LocalStorageBackend(base_dir)


# Global storage backend instance
_storage_backend: Optional[StorageBackend] = None


def get_storage() -> StorageBackend:
    """Get or create the global storage backend instance."""
    global _storage_backend
    if _storage_backend is None:
        _storage_backend = get_storage_backend()
    return _storage_backend
