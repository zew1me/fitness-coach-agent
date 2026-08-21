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


def test_build_object_key_public_method_scopes_to_user_and_purpose() -> None:
    # build_object_key is called directly by the zip-image re-upload path (there is
    # no PresignUploadRequest for that flow), so it needs its own coverage beyond
    # the _build_object_key wrapper above.
    service = R2Service()

    object_key = service.build_object_key(
        user_id="user-123", filename="shot.PNG", purpose="chat-attachment"
    )

    assert object_key.startswith("users/user-123/chat-attachment/")
    assert object_key.endswith(".png")


def test_build_object_key_public_method_sanitizes_purpose_and_generates_unique_names() -> None:
    service = R2Service()

    first = service.build_object_key(
        user_id="user-123", filename="run.gpx", purpose="Zip Activity Member!"
    )
    second = service.build_object_key(
        user_id="user-123", filename="run.gpx", purpose="Zip Activity Member!"
    )

    assert "/zip-activity-member/" in first
    assert first != second


def test_build_object_key_public_method_matches_wrapper_shape() -> None:
    # _build_object_key is now a thin wrapper around build_object_key; both must
    # produce keys with the same scoping/extension shape for the same inputs.
    service = R2Service()
    request = PresignUploadRequest(
        filename="race.fit",
        content_type="application/vnd.garmin.fit",
        content_length=2048,
        purpose="chat-attachment",
    )

    via_wrapper = service._build_object_key(user_id="user-123", request=request)
    via_public_method = service.build_object_key(
        user_id="user-123", filename="race.fit", purpose="chat-attachment"
    )

    assert via_wrapper.rsplit("/", 1)[0] == via_public_method.rsplit("/", 1)[0]
    assert via_wrapper.endswith(".fit")
    assert via_public_method.endswith(".fit")


def test_public_url_uses_configured_base(monkeypatch) -> None:
    service = R2Service()
    monkeypatch.setattr(
        "backend.services.r2.settings.r2_public_base_url",
        "https://cdn.example.com/training",
    )

    public_url = service._build_public_url("users/u/check-in-image/file.png")

    assert public_url == "https://cdn.example.com/training/users/u/check-in-image/file.png"


def test_resolve_object_key_prefers_public_url_over_mangled_key(monkeypatch) -> None:
    # The model reliably transcribes the distinctive public_url but corrupts the
    # long opaque object_key (splices the user-UUID head onto the file-UUID tail),
    # which then fails the per-user scope check with a 403. The public_url wins.
    service = R2Service()
    monkeypatch.setattr(
        "backend.services.r2.settings.r2_public_base_url",
        "https://pub-abc.r2.dev",
    )
    correct_key = (
        "users/c3b8909b-ea1b-4b55-86cd-6679c232edad/chat-attachment/2026/07/08/f7fad21d.fit"
    )
    mangled_key = "users/c3b8909b-ea1b-4bc8-ba17-7706c5e34e44.fit"

    resolved = service.resolve_object_key(
        object_key=mangled_key,
        public_url=f"https://pub-abc.r2.dev/{correct_key}",
    )

    assert resolved == correct_key


def test_resolve_object_key_handles_base_with_path(monkeypatch) -> None:
    service = R2Service()
    monkeypatch.setattr(
        "backend.services.r2.settings.r2_public_base_url",
        "https://cdn.example.com/training",
    )
    key = "users/u/chat-attachment/2026/07/08/file.fit"

    resolved = service.resolve_object_key(
        object_key="mangled",
        public_url=f"https://cdn.example.com/training/{key}",
    )

    assert resolved == key


def test_resolve_object_key_falls_back_when_public_url_absent(monkeypatch) -> None:
    service = R2Service()
    monkeypatch.setattr(
        "backend.services.r2.settings.r2_public_base_url",
        "https://pub-abc.r2.dev",
    )
    key = "users/u/chat-attachment/2026/07/08/file.fit"

    assert service.resolve_object_key(object_key=key, public_url=None) == key
    assert service.resolve_object_key(object_key=key, public_url="  ") == key


def test_resolve_object_key_falls_back_for_url_outside_configured_base(monkeypatch) -> None:
    # A public_url that isn't under the trusted R2 base is not authoritative;
    # keep the model-supplied object_key rather than deriving from a stray host.
    service = R2Service()
    monkeypatch.setattr(
        "backend.services.r2.settings.r2_public_base_url",
        "https://pub-abc.r2.dev",
    )
    key = "users/u/chat-attachment/2026/07/08/file.fit"

    resolved = service.resolve_object_key(
        object_key=key,
        public_url="https://evil.example.com/users/other/file.fit",
    )

    assert resolved == key


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
