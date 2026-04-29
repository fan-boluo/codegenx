"""Tencent COS manager."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from qcloud_cos import CosConfig, CosS3Client


class CosManager:
    """Tencent Cloud COS manager."""

    def __init__(
        self,
        secret_id: str | None = None,
        secret_key: str | None = None,
        region: str = "ap-beijing",
        bucket: str = "codegenx-1234567890",
    ):
        self.secret_id = secret_id or os.getenv("COS_SECRET_ID")
        self.secret_key = secret_key or os.getenv("COS_SECRET_KEY")
        self.region = region
        self.bucket = bucket

        if not self.secret_id or not self.secret_key:
            raise ValueError("COS credentials not provided")

        config = CosConfig(
            Region=self.region,
            SecretId=self.secret_id,
            SecretKey=self.secret_key,
        )
        self.client = CosS3Client(config)

    def upload_file(self, local_path: str, key: str) -> str:
        """Upload a file to COS.

        Args:
            local_path: Local file path
            key: COS object key

        Returns:
            COS URL
        """
        self.client.upload_file(
            Bucket=self.bucket,
            LocalFilePath=local_path,
            Key=key,
        )
        return f"https://{self.bucket}.cos.{self.region}.myqcloud.com/{key}"

    def upload_bytes(self, data: bytes, key: str, content_type: str = "application/octet-stream") -> str:
        """Upload bytes to COS.

        Args:
            data: File data
            key: COS object key
            content_type: Content type

        Returns:
            COS URL
        """
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return f"https://{self.bucket}.cos.{self.region}.myqcloud.com/{key}"

    def download_file(self, key: str, local_path: str) -> None:
        """Download a file from COS.

        Args:
            key: COS object key
            local_path: Local file path
        """
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(
            Bucket=self.bucket,
            Key=key,
            DestFilePath=local_path,
        )

    def delete_file(self, key: str) -> None:
        """Delete a file from COS.

        Args:
            key: COS object key
        """
        self.client.delete_object(
            Bucket=self.bucket,
            Key=key,
        )

    def generate_presigned_url(self, key: str, expiration: int = 3600) -> str:
        """Generate presigned URL for COS object.

        Args:
            key: COS object key
            expiration: URL expiration time in seconds

        Returns:
            Presigned URL
        """
        return self.client.get_presigned_url(
            Method="GET",
            Bucket=self.bucket,
            Key=key,
            Expired=expiration,
        )

    @staticmethod
    def generate_file_key(original_filename: str, prefix: str = "") -> str:
        """Generate a unique file key for COS.

        Args:
            original_filename: Original filename
            prefix: Key prefix

        Returns:
            Generated key
        """
        timestamp = datetime.now().strftime("%Y/%m/%d")
        import uuid
        unique_id = str(uuid.uuid4())[:8]
        extension = Path(original_filename).suffix
        return f"{prefix.rstrip('/')}/{timestamp}/{unique_id}{extension}"