import logging
from typing import cast

import pytest
from botocore.client import BaseClient
from fastapi import HTTPException

from backend.models.storage import PresignUploadRequest
from backend.services.r2 import R2Service


def test_build_object_key_scopes_to_user_and_purpose() -> None:
    service = R2Service()
    request = PresignUploadRequest(
        filename="Whoop Screenshot.PNG",
        content_type="image/png",
        content_length=1024,
        purpose="check in image",
    )

    object_key = service._build_object_key(user_id="user-123", request=request)

    assert object_key.startswith("users/user-123/check-in-image/")
    assert object_key.endswith(".png")


def test_public_url_uses_configured_base(monkeypatch) -> None:
    service = R2Service()
    monkeypatch.setattr(
        "backend.services.r2.settings.r2_public_base_url",
        "https://cdn.example.com/training",
    )

    public_url = service._build_public_url("users/u/check-in-image/file.png")

    assert public_url == "https://cdn.example.com/training/users/u/check-in-image/file.png"


def test_object_key_log_ref_does_not_expose_storage_path() -> None:
    service = R2Service()
    object_key = "users/user-123/check-in-image/2026/04/26/private-race-file.gpx"

    log_ref = service._object_key_log_ref(object_key)

    assert len(log_ref) == 12
    assert log_ref != object_key
    assert "users/user-123" not in log_ref
    assert "private-race-file" not in log_ref


def test_blank_endpoint_url_without_account_id_is_missing_config(monkeypatch) -> None:
    service = R2Service()
    monkeypatch.setattr("backend.services.r2.settings.r2_endpoint_url", "")
    monkeypatch.setattr("backend.services.r2.settings.r2_account_id", None)

    with pytest.raises(HTTPException) as exc_info:
        service._resolve_endpoint_url()

    assert exc_info.value.status_code == 500
    assert (
        exc_info.value.detail
        == "R2 endpoint is not configured. Set R2_ENDPOINT_URL or R2_ACCOUNT_ID."
    )


def test_blank_endpoint_url_falls_back_to_account_id(monkeypatch) -> None:
    service = R2Service()
    monkeypatch.setattr("backend.services.r2.settings.r2_endpoint_url", "")
    monkeypatch.setattr("backend.services.r2.settings.r2_account_id", "account-123")

    endpoint_url = service._resolve_endpoint_url()

    assert endpoint_url == "https://account-123.r2.cloudflarestorage.com"


def test_get_client_reuses_built_client(monkeypatch) -> None:
    service = R2Service()
    built_clients = []

    def build_client():
        client = object()
        built_clients.append(client)
        return client

    monkeypatch.setattr(service, "_build_client", build_client)

    first_client = service._get_client()
    second_client = service._get_client()

    assert first_client is second_client
    assert built_clients == [first_client]


@pytest.mark.asyncio
async def test_download_file_logs_key_ref_not_storage_path(monkeypatch, caplog) -> None:
    class Body:
        def read(self) -> bytes:
            return b"activity"

    class Client:
        def get_object(self, **kwargs):
            return {"Body": Body()}

    service = R2Service(client=cast(BaseClient, Client()))
    object_key = "users/user-123/check-in-image/2026/04/26/private-race-file.gpx"
    monkeypatch.setattr("backend.services.r2.settings.r2_access_key_id", "access-key")
    monkeypatch.setattr("backend.services.r2.settings.r2_secret_access_key", "secret-key")
    monkeypatch.setattr("backend.services.r2.settings.r2_bucket", "bucket")
    caplog.set_level(logging.DEBUG, logger="backend.services.r2")

    data = await service.download_file_bytes(user_id="user-123", object_key=object_key)

    assert data == b"activity"
    assert object_key not in caplog.text
    assert "private-race-file" not in caplog.text
    assert f"key_ref={service._object_key_log_ref(object_key)}" in caplog.text
