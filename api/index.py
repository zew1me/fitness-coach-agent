from collections.abc import Mapping

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.models.auth import OAuthAuthorizeRequest, OAuthTokenRequest, UserContext
from backend.models.planning import CheckInInput
from backend.models.storage import PresignUploadRequest
from backend.repos.supabase_repo import SupabaseRepository
from backend.services.auth import AuthService
from backend.services.planner import PlannerService
from backend.services.r2 import R2Service

app = FastAPI(title="Exercise Training Plan GPT")
auth_service = AuthService()
planner_service = PlannerService()
repo = SupabaseRepository()
r2_service = R2Service()


class PlanRequest(BaseModel):
    effective_date: str | None = None
    image_count: int = 0
    raw_text: str
    user_id: str


class ProfileRequest(BaseModel):
    user_id: str


def require_user_context(authorization: str | None = Header(default=None)) -> UserContext:
    if authorization is None or not authorization.startswith("Bearer "):
        resource = auth_service.protected_resource_metadata()["resource"]
        raise HTTPException(
            status_code=401,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": f'Bearer resource_metadata="{resource}"'},
        )
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return auth_service.get_user_context_from_bearer(token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid bearer token") from exc


@app.get("/health")
async def health() -> Mapping[str, str]:
    return {"status": "ok"}


@app.get("/.well-known/oauth-authorization-server")
async def oauth_authorization_server() -> Mapping[str, object]:
    return auth_service.authorization_metadata()


@app.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource() -> Mapping[str, object]:
    return auth_service.protected_resource_metadata()


@app.get("/api/oauth/authorize")
async def oauth_authorize(
    client_id: str,
    redirect_uri: str,
    scope: str = "profile:read plans:write metrics:write",
    state: str | None = None,
) -> Mapping[str, str]:
    request = OAuthAuthorizeRequest(
        client_id=client_id, redirect_uri=redirect_uri, scope=scope, state=state
    )
    return auth_service.build_authorize_response(request, user_id="demo-user")


@app.post("/api/oauth/token")
async def oauth_token(payload: OAuthTokenRequest) -> JSONResponse:
    bundle = auth_service.exchange_code(payload)
    return JSONResponse(bundle.model_dump(mode="json"))


@app.post("/api/oauth/revoke")
async def oauth_revoke() -> Mapping[str, bool]:
    return {"revoked": True}


@app.post("/api/profile")
async def get_profile(
    payload: ProfileRequest, _: UserContext = Depends(require_user_context)
) -> Mapping[str, object]:
    profile = await repo.get_athlete_profile(payload.user_id)
    return profile.model_dump(mode="json")


@app.post("/api/check-ins")
async def create_check_in(
    payload: PlanRequest, _: UserContext = Depends(require_user_context)
) -> Mapping[str, object]:
    return {"accepted": True, "image_count": payload.image_count, "user_id": payload.user_id}


@app.post("/api/plans/generate")
async def generate_plan(
    payload: PlanRequest, _: UserContext = Depends(require_user_context)
) -> Mapping[str, object]:
    profile = await repo.get_athlete_profile(payload.user_id)
    check_in = CheckInInput(
        user_id=payload.user_id, raw_text=payload.raw_text, image_count=payload.image_count
    )
    plan = planner_service.create_plan(profile, check_in)
    prompt = planner_service.compose_prompt(profile, check_in)
    return {"plan": plan.model_dump(mode="json"), "prompt_preview": prompt}


@app.post("/api/files/presign-upload")
async def presign_upload(
    payload: PresignUploadRequest, user_context: UserContext = Depends(require_user_context)
) -> Mapping[str, object]:
    presigned_upload = r2_service.create_presigned_upload(
        user_id=user_context.user_id, request=payload
    )
    return presigned_upload.model_dump(mode="json")


@app.post("/api/mcp")
async def mcp_endpoint(_: UserContext = Depends(require_user_context)) -> Mapping[str, str]:
    return {"status": "ok", "message": "MCP tool surface will be exposed here."}
