from collections.abc import Mapping
from datetime import date
from urllib.parse import urlencode

from fastapi import Cookie, Depends, FastAPI, Form, Header, HTTPException, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from backend.models.auth import (
    BrowserSessionRequest,
    OAuthAuthorizeRequest,
    OAuthRevokeRequest,
    OAuthTokenRequest,
    UserContext,
)
from backend.models.planning import AthleteProfile, CheckInInput
from backend.models.storage import PresignUploadRequest
from backend.repos.oauth_repo import OAuthRepositoryNotConfiguredError
from backend.repos.supabase_repo import (
    RecordNotFoundError,
    RepositoryNotConfiguredError,
    SupabaseRepository,
)
from backend.services.auth import (
    AuthService,
    OAuthConsentRequiredError,
    OAuthError,
    OAuthInvalidGrantError,
    OAuthLoginRequiredError,
)
from backend.services.planner import PlannerService
from backend.services.r2 import R2Service

app = FastAPI(title="Exercise Training Plan GPT")
auth_service = AuthService()
planner_service = PlannerService()
repo = SupabaseRepository()
r2_service = R2Service()


class PlanRequest(BaseModel):
    effective_date: date | None = None
    image_count: int = 0
    raw_text: str
    user_id: str


class ProfileRequest(BaseModel):
    user_id: str


class ProfileUpsertRequest(BaseModel):
    age: int | None = None
    constraints: list[str] = Field(default_factory=list)
    cycling_ftp_watts: int | None = None
    goals: list[str] = Field(default_factory=list)
    injuries_rehab: list[str] = Field(default_factory=list)
    notes: str | None = None
    user_id: str
    weight_kg: float | None = None


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
    except OAuthRepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid bearer token") from exc


def enforce_user_access(requested_user_id: str, user_context: UserContext) -> None:
    if requested_user_id != user_context.user_id:
        raise HTTPException(
            status_code=403,
            detail="Authenticated user cannot access this resource.",
        )


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
async def oauth_authorize(  # noqa: PLR0913
    client_id: str,
    redirect_uri: str,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    prompt: str | None = None,
    scope: str = "profile:read plans:write metrics:write",
    state: str | None = None,
    coach_browser_session: str | None = Cookie(default=None),
) -> RedirectResponse:
    request = OAuthAuthorizeRequest(
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        state=state,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        prompt=prompt,
    )
    try:
        auth_service.parse_authorize_request(request)
        browser_session = auth_service.get_browser_session_from_cookie(coach_browser_session)
    except OAuthLoginRequiredError:
        authorize_url = "/api/oauth/authorize?" + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": code_challenge or "",
                "code_challenge_method": code_challenge_method or "",
                "scope": scope,
                "state": state or "",
                "prompt": prompt or "",
            }
        )
        return RedirectResponse(auth_service.build_login_redirect(authorize_url), status_code=302)
    except OAuthRepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        redirect_target = auth_service.build_authorize_redirect(
            request=request, browser_session=browser_session
        )
    except OAuthConsentRequiredError:
        return RedirectResponse(auth_service.build_consent_redirect(request), status_code=302)
    except OAuthRepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(redirect_target, status_code=302)


@app.post("/api/oauth/token")
async def oauth_token(payload: OAuthTokenRequest) -> JSONResponse:
    try:
        bundle = auth_service.exchange_token_request(payload)
    except OAuthRepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OAuthInvalidGrantError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(bundle.model_dump(mode="json"))


@app.post("/api/oauth/revoke")
async def oauth_revoke(payload: OAuthRevokeRequest) -> Mapping[str, bool]:
    try:
        revoked = auth_service.revoke(payload)
    except OAuthRepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"revoked": revoked}


@app.post("/api/oauth/browser-session")
async def oauth_browser_session(
    payload: BrowserSessionRequest, response: Response
) -> Mapping[str, bool]:
    try:
        session = auth_service.create_browser_session(payload.access_token)
    except OAuthRepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Unable to verify browser session.") from exc
    response.set_cookie(
        key=auth_service.browser_session_cookie_name,
        value=auth_service.create_browser_session_token(session),
        httponly=True,
        max_age=12 * 60 * 60,
        path="/",
        samesite="lax",
        secure=False,
    )
    return {"ok": True}


@app.post("/api/oauth/authorize/decision")
async def oauth_authorize_decision(  # noqa: PLR0913
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    scope: str = Form(...),
    code_challenge: str = Form(...),
    code_challenge_method: str = Form(...),
    state: str = Form(default=""),
    decision: str = Form(...),
    coach_browser_session: str | None = Cookie(default=None),
) -> RedirectResponse:
    request = OAuthAuthorizeRequest(
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        state=state or None,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
    )
    try:
        browser_session = auth_service.get_browser_session_from_cookie(coach_browser_session)
    except OAuthLoginRequiredError:
        return RedirectResponse(
            auth_service.build_login_redirect(auth_service.build_consent_redirect(request)),
            status_code=302,
        )
    if decision != "approve":
        denial_redirect = (
            f"{redirect_uri}?error=access_denied&state={state}"
            if state
            else f"{redirect_uri}?error=access_denied"
        )
        return RedirectResponse(
            denial_redirect,
            status_code=302,
        )
    try:
        redirect_target = auth_service.approve_consent(
            request=request, browser_session=browser_session
        )
    except OAuthRepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(redirect_target, status_code=302)


@app.post("/api/profile")
async def get_profile(
    payload: ProfileRequest, user_context: UserContext = Depends(require_user_context)
) -> Mapping[str, object]:
    enforce_user_access(payload.user_id, user_context)
    try:
        profile = await repo.get_athlete_profile(payload.user_id)
    except RepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return profile.model_dump(mode="json")


@app.put("/api/profile")
async def upsert_profile(
    payload: ProfileUpsertRequest, user_context: UserContext = Depends(require_user_context)
) -> Mapping[str, object]:
    enforce_user_access(payload.user_id, user_context)
    profile = payload.model_dump()
    try:
        saved_profile = await repo.upsert_athlete_profile(AthleteProfile.model_validate(profile))
    except RepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return saved_profile.model_dump(mode="json")


@app.post("/api/check-ins")
async def create_check_in(
    payload: PlanRequest, user_context: UserContext = Depends(require_user_context)
) -> Mapping[str, object]:
    enforce_user_access(payload.user_id, user_context)
    check_in = CheckInInput(
        user_id=payload.user_id,
        raw_text=payload.raw_text,
        image_count=payload.image_count,
        effective_date=payload.effective_date,
    )
    try:
        record = await repo.create_check_in(check_in)
    except RepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"accepted": True, "check_in": record.model_dump(mode="json")}


@app.post("/api/plans/generate")
async def generate_plan(
    payload: PlanRequest, user_context: UserContext = Depends(require_user_context)
) -> Mapping[str, object]:
    enforce_user_access(payload.user_id, user_context)
    try:
        profile = await repo.get_athlete_profile(payload.user_id)
    except RepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    check_in = CheckInInput(
        user_id=payload.user_id,
        raw_text=payload.raw_text,
        image_count=payload.image_count,
        effective_date=payload.effective_date,
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
