from __future__ import annotations

import logging
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import PurePosixPath
from uuid import uuid4

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from backend.config import settings
from backend.models.storage import PresignUploadRequest, PresignUploadResponse

logger = logging.getLogger(__name__)

DEFAULT_EXPIRATION_SECONDS = 900


class R2Service:
    """Issue presigned upload URLs for user-scoped R2 object keys."""

    def __init__(self, *, client: BaseClient | None = None) -> None:
        self._client = client

    def create_presigned_upload(
        self, *, user_id: str, request: PresignUploadRequest
    ) -> PresignUploadResponse:
        self._ensure_configured()
        object_key = self._build_object_key(user_id=user_id, request=request)
        client = self._get_client()
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

    async def upload_file(
        self, *, user_id: str, object_key: str, file_stream, content_type: str
    ) -> PresignUploadResponse:
        """Upload a file directly to R2 and return upload metadata."""
        self._ensure_configured()
        self._validate_object_key_scope(user_id=user_id, object_key=object_key)

        client = self._get_client()
        await run_in_threadpool(
            client.put_object,
            Bucket=settings.r2_bucket,
            Key=object_key,
            Body=file_stream,
            ContentType=content_type,
        )
        logger.info(
            "r2 upload complete user_id=%s key_ref=%s",
            user_id,
            self._object_key_log_ref(object_key),
        )

        return PresignUploadResponse(
            upload_url="",  # Not used for direct uploads
            object_key=object_key,
            public_url=self._build_public_url(object_key),
            headers={"Content-Type": content_type},
            method="POST",  # Indicate this was a direct upload
        )

    async def delete_file(self, *, user_id: str, object_key: str) -> None:
        """Delete a user-scoped object from R2."""
        self._ensure_configured()
        self._validate_object_key_scope(user_id=user_id, object_key=object_key)
        client = self._get_client()
        await run_in_threadpool(
            client.delete_object,
            Bucket=settings.r2_bucket,
            Key=object_key,
        )
        logger.info(
            "r2 delete complete user_id=%s key_ref=%s",
            user_id,
            self._object_key_log_ref(object_key),
        )

    async def download_file_bytes(self, *, user_id: str, object_key: str) -> bytes:
        """Download a user-scoped object from R2."""
        self._ensure_configured()
        self._validate_object_key_scope(user_id=user_id, object_key=object_key)
        client = self._get_client()
        key_ref = self._object_key_log_ref(object_key)
        logger.debug("r2 download start user_id=%s key_ref=%s", user_id, key_ref)
        response = await run_in_threadpool(
            client.get_object,
            Bucket=settings.r2_bucket,
            Key=object_key,
        )
        body = response["Body"]
        data = await run_in_threadpool(body.read)
        logger.debug("r2 download done user_id=%s key_ref=%s bytes=%d", user_id, key_ref, len(data))
        return data

    def _get_client(self) -> BaseClient:
        if self._client is None:
            self._client = self._build_client()
        return self._client

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
        return self.build_object_key(
            user_id=user_id, filename=request.filename, purpose=request.purpose
        )

    def build_object_key(self, *, user_id: str, filename: str, purpose: str) -> str:
        """Build a fresh user-scoped object key.

        Shared by the presigned-upload flow and direct server-side uploads (e.g. zip
        image members re-uploaded via ``upload_file``).
        """
        purpose_segment = self._sanitize_segment(purpose)
        extension = self._extract_extension(filename)
        date_prefix = datetime.now(UTC).strftime("%Y/%m/%d")
        object_name = f"{uuid4()}{extension}"
        return str(PurePosixPath("users", user_id, purpose_segment, date_prefix, object_name))

    def _build_public_url(self, object_key: str) -> str | None:
        base = self._configured_value(settings.r2_public_base_url)
        if base is None:
            return None
        return f"{base.rstrip('/')}/{object_key}"

    def resolve_object_key(self, *, object_key: str, public_url: str | None) -> str:
        """Return the authoritative object key for a referenced upload.

        The coach passes both an ``object_key`` and a ``public_url`` back into the
        upload-processing tool. The model reliably transcribes the distinctive
        ``public_url`` but corrupts the long opaque ``object_key`` (splicing the
        user-UUID head onto the file-UUID tail), which then fails the per-user
        scope check with a 403. When we can derive the key from ``public_url``
        deterministically, that value wins; otherwise we fall back to the
        model-supplied ``object_key``.
        """
        derived = self._object_key_from_public_url(public_url)
        return derived or object_key

    def _object_key_from_public_url(self, public_url: str | None) -> str | None:
        """Invert ``_build_public_url``: recover the object key from a public URL.

        Only URLs under the configured R2 base are trusted — that distinctive
        prefix is what makes the derivation safe. Anything else (base unset, an
        unrelated host, a model-hallucinated URL) returns ``None`` so the caller
        falls back to the supplied ``object_key``.
        """
        url = self._configured_value(public_url)
        base = self._configured_value(settings.r2_public_base_url)
        if url is None or base is None:
            return None
        prefix = f"{base.rstrip('/')}/"
        if not url.startswith(prefix):
            return None
        return url[len(prefix) :].lstrip("/") or None

    def _object_key_log_ref(self, object_key: str) -> str:
        return sha256(object_key.encode("utf-8")).hexdigest()[:12]

    def _ensure_configured(self) -> None:
        missing = [
            name
            for name, value in {
                "R2_ACCESS_KEY_ID": settings.r2_access_key_id,
                "R2_SECRET_ACCESS_KEY": settings.r2_secret_access_key,
                "R2_BUCKET": settings.r2_bucket,
            }.items()
            if self._configured_value(value) is None
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
        endpoint_url = self._configured_value(settings.r2_endpoint_url)
        if endpoint_url is not None:
            return endpoint_url

        account_id = self._configured_value(settings.r2_account_id)
        if account_id is None:
            raise HTTPException(
                status_code=500,
                detail="R2 endpoint is not configured. Set R2_ENDPOINT_URL or R2_ACCOUNT_ID.",
            )
        return f"https://{account_id}.r2.cloudflarestorage.com"

    def _sanitize_segment(self, value: str) -> str:
        normalized = "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in value.strip().lower()
        ).strip("-")
        if not normalized:
            return "upload"
        return normalized[:64]

    def _validate_object_key_scope(self, *, user_id: str, object_key: str) -> None:
        """Validate that the object_key belongs to the authenticated user."""
        expected_prefix = f"users/{user_id}/"
        if not object_key.startswith(expected_prefix):
            raise HTTPException(
                status_code=403,
                detail="Access denied: object key does not belong to authenticated user",
            )

    def _configured_value(self, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        return stripped
