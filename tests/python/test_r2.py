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
