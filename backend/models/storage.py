from pydantic import BaseModel, ConfigDict, Field


class PresignUploadRequest(BaseModel):
    content_length: int = Field(gt=0, le=25 * 1024 * 1024)
    content_type: str = Field(min_length=1, max_length=255)
    filename: str = Field(min_length=1, max_length=255)
    purpose: str = Field(default="check-in-image", min_length=1, max_length=64)


class PresignUploadResponse(BaseModel):
    headers: dict[str, str]
    method: str = "PUT"
    object_key: str
    public_url: str | None = None
    upload_url: str = ""  # Empty for direct uploads

    model_config = ConfigDict(frozen=True)
