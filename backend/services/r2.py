from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath
from uuid import uuid4

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from fastapi import HTTPException

from backend.config import settings
from backend.models.storage import PresignUploadRequest, PresignUploadResponse

DEFAULT_EXPIRATION_SECONDS = 900


class R2Service:
    """Issue presigned upload URLs for user-scoped R2 object keys."""

    def create_presigned_upload(
        self, *, user_id: str, request: PresignUploadRequest
    ) -> PresignUploadResponse:
        self._ensure_configured()
        object_key = self._build_object_key(user_id=user_id, request=request)
        client = self._build_client()
        upload_url = client.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": settings.r2_bucket,
                "Key": object_key,
                "ContentType": request.content_type,
            },
            ExpiresIn=DEFAULT_EXPIRATION_SECONDS,
            HttpMethod="PUT",
        )
        return PresignUploadResponse(
            upload_url=upload_url,
            object_key=object_key,
            public_url=self._build_public_url(object_key),
            headers={"Content-Type": request.content_type},
        )

    def _build_client(self) -> BaseClient:
        return boto3.client(
            "s3",
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            endpoint_url=self._resolve_endpoint_url(),
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )

    def _build_object_key(self, *, user_id: str, request: PresignUploadRequest) -> str:
        purpose = self._sanitize_segment(request.purpose)
        extension = self._extract_extension(request.filename)
        date_prefix = datetime.now(UTC).strftime("%Y/%m/%d")
        object_name = f"{uuid4()}{extension}"
        return str(PurePosixPath("users", user_id, purpose, date_prefix, object_name))

    def _build_public_url(self, object_key: str) -> str | None:
        if settings.r2_public_base_url is None:
            return None
        return f"{settings.r2_public_base_url.rstrip('/')}/{object_key}"

    def _ensure_configured(self) -> None:
        missing = [
            name
            for name, value in {
                "R2_ACCESS_KEY_ID": settings.r2_access_key_id,
                "R2_SECRET_ACCESS_KEY": settings.r2_secret_access_key,
                "R2_BUCKET": settings.r2_bucket,
            }.items()
            if value is None
        ]
        if missing:
            missing_names = ", ".join(sorted(missing))
            raise HTTPException(
                status_code=500,
                detail=f"R2 upload support is not configured. Missing: {missing_names}",
            )

    def _extract_extension(self, filename: str) -> str:
        suffix = PurePosixPath(filename.strip()).suffix.lower()
        if not suffix:
            return ""
        return suffix[:16]

    def _resolve_endpoint_url(self) -> str:
        if settings.r2_endpoint_url is not None:
            return settings.r2_endpoint_url
        if settings.r2_account_id is None:
            raise HTTPException(
                status_code=500,
                detail="R2 endpoint is not configured. Set R2_ENDPOINT_URL or R2_ACCOUNT_ID.",
            )
        return f"https://{settings.r2_account_id}.r2.cloudflarestorage.com"

    def _sanitize_segment(self, value: str) -> str:
        normalized = "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in value.strip().lower()
        ).strip("-")
        if not normalized:
            return "upload"
        return normalized[:64]
