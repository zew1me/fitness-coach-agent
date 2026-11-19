from pydantic import BaseModel, ConfigDict, Field


class PresignUploadRequest(BaseModel):
    content_length: int = Field(gt=0, le=25 * 1024 * 1024)
    content_type: str = Field(min_length=1, max_length=255)
    filename: str = Field(min_length=1, max_length=255)
    purpose: str = Field(default="check-in-image", min_length=1, max_length=64)


class PresignUploadResponse(BaseModel):
    headers: dict[str, str]
    method: str = "POST"
    object_key: str
    public_url: str | None = None
    # Always empty: browsers upload through the backend proxy and never receive a
    # storage-signed URL (#98). Kept on the wire so existing clients that read the
    # field keep parsing the response.
    upload_url: str = ""

    model_config = ConfigDict(frozen=True)
