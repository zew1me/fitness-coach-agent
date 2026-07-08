from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal
from urllib.parse import urlencode

import httpx
import sentry_sdk
from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse, RedirectResponse
from postgrest.exceptions import APIError as PostgRESTAPIError
from pydantic import BaseModel, Field, ValidationError

from backend.config import settings
from backend.logging_config import configure_logging
from backend.models.athlete import (
    AthleteProfile as _AthleteProfile,
)
from backend.models.athlete import (
    RecoveryLog,
    ScheduleAvailability,
    ScheduleDayAvailability,
    ScheduleOverride,
    SportThreshold,
)
from backend.models.auth import (
    BrowserSessionRequest,
    BrowserTokenResponse,
    OAuthAuthorizeRequest,
    OAuthRevokeRequest,
    OAuthTokenRequest,
    UserContext,
)
from backend.models.chat import (
    ChatModelState,
    ChatModelStateReplaceRequest,
    ChatPersistRequest,
    ChatTurnLeaseReleaseRequest,
    ChatTurnLeaseRequest,
)
from backend.models.storage import PresignUploadRequest
from backend.models.training import Activity, Goal, PlanWorkout, TrainingPlan
from backend.repos.intervals_repo import IntervalsRepositoryNotConfiguredError
from backend.repos.oauth_repo import OAuthRepositoryNotConfiguredError
from backend.repos.supabase_repo import (
    RecordNotFoundError,
    RepositoryNotConfiguredError,
    SupabaseRepository,
    build_activity_summary_from_fields,
)
from backend.services.auth import (
    AuthService,
    OAuthConsentRequiredError,
    OAuthError,
    OAuthInvalidGrantError,
    OAuthLoginRequiredError,
)
from backend.services.chat import ChatService, ChatUnavailableError
from backend.services.goal_service import (
    GoalService,
    InvalidGoalPayloadError,
    UnknownGoalActionError,
)
from backend.services.intervals import (
    IntervalsConfigurationError,
    IntervalsOAuthExchangeError,
    IntervalsOAuthService,
    IntervalsStateError,
)
from backend.services.r2 import R2Service

configure_logging(debug=settings.app_env == "development")

_sentry_dsn = os.environ.get("SENTRY_DSN")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        environment=settings.app_env,
        enable_logs=True,
        # Match the TS configs (1.0) so traces the browser propagates to /api/ continue
        # server-side instead of breaking at the backend boundary.
        traces_sample_rate=1.0,
    )
else:
    logging.getLogger(__name__).info("SENTRY_DSN is not set; server-side Sentry is disabled.")

logger = logging.getLogger(__name__)
MAX_CHAT_MESSAGE_PAGE_SIZE = 100


async def log_startup() -> None:
    """Emit startup diagnostics so it is clear which optional features are active."""
    features = {
        "openai": bool(settings.openai_api_key),
        "r2_storage": all(
            [settings.r2_access_key_id, settings.r2_secret_access_key, settings.r2_bucket]
        ),
        "supabase": bool(settings.supabase_url and settings.supabase_service_role_key),
        "sentry": bool(_sentry_dsn),
    }
    enabled = [k for k, v in features.items() if v]
    disabled = [k for k, v in features.items() if not v]
    logger.info(
        "startup env=%s features_enabled=%s features_disabled=%s",
        settings.app_env,
        enabled,
        disabled,
    )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await log_startup()
    yield


app = FastAPI(title="Endurance Coaching Agent", lifespan=lifespan)
auth_service = AuthService()
chat_service = ChatService()
goal_service = GoalService()
intervals_service = IntervalsOAuthService()
repo = SupabaseRepository()
r2_service = R2Service()

RECOVERY_WEEK_AGE_BREAKPOINT = 40


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
        logger.warning("bearer auth failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=401, detail="Invalid bearer token") from exc


async def _run_chat_model_state_operation(
    operation: Callable[[], Awaitable[ChatModelState]],
    *,
    failure_log_message: str,
) -> Mapping[str, object]:
    try:
        state = await operation()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (PostgRESTAPIError, httpx.HTTPError) as exc:
        logger.exception(failure_log_message, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Chat session service unavailable") from exc
    return state.model_dump(mode="json")


# ── Health ────────────────────────────────────────────────────


@app.get("/health")
async def health() -> Mapping[str, str]:
    return {"status": "ok"}


# ── OAuth (unchanged) ────────────────────────────────────────


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
    logger.info("oauth authorize client_id=%s scope=%s", client_id, scope)
    try:
        auth_service.parse_authorize_request(request)
        browser_session = auth_service.get_browser_session_from_cookie(coach_browser_session)
    except OAuthLoginRequiredError:
        logger.info("oauth authorize login required, redirecting client_id=%s", client_id)
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
        logger.info("oauth authorize consent required client_id=%s", client_id)
        return RedirectResponse(auth_service.build_consent_redirect(request), status_code=302)
    except OAuthRepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("oauth authorize approved, issuing code client_id=%s", client_id)
    return RedirectResponse(redirect_target, status_code=302)


@app.post("/api/oauth/token")
async def oauth_token(payload: OAuthTokenRequest) -> JSONResponse:
    try:
        bundle = auth_service.exchange_token_request(payload)
    except OAuthRepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OAuthInvalidGrantError as exc:
        logger.warning("oauth token exchange failed: invalid grant client_id=%s", payload.client_id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OAuthError as exc:
        logger.warning("oauth token exchange failed client_id=%s error=%s", payload.client_id, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("oauth token issued client_id=%s", payload.client_id)
    return JSONResponse(bundle.model_dump(mode="json"))


@app.post("/api/oauth/revoke")
async def oauth_revoke(payload: OAuthRevokeRequest) -> Mapping[str, bool]:
    try:
        revoked = auth_service.revoke(payload)
    except OAuthRepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    logger.info("oauth revoke revoked=%s", revoked)
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
        logger.warning("browser session creation failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=401, detail="Unable to verify browser session.") from exc
    response.set_cookie(
        key=auth_service.browser_session_cookie_name,
        value=auth_service.create_browser_session_token(session),
        httponly=True,
        max_age=12 * 60 * 60,
        path="/",
        samesite="lax",
        secure=settings.base_url.startswith("https://"),
    )
    logger.info("browser session created user_id=%s", session.user_id)
    return {"ok": True}


@app.post("/api/oauth/browser-session/logout")
async def oauth_browser_session_logout() -> Response:
    logger.info("browser session logout")
    response = RedirectResponse("/login?return_to=/", status_code=303)
    response.delete_cookie(
        key=auth_service.browser_session_cookie_name,
        httponly=True,
        path="/",
        samesite="lax",
        secure=settings.app_base_url.startswith("https://"),
    )
    return response


@app.post("/api/oauth/browser-token")
async def oauth_browser_token(
    coach_browser_session: str | None = Cookie(default=None),
) -> BrowserTokenResponse:
    try:
        browser_session = auth_service.get_browser_session_from_cookie(coach_browser_session)
        token = auth_service.create_browser_token(browser_session)
    except OAuthLoginRequiredError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except OAuthRepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    else:
        logger.debug("browser token issued user_id=%s", browser_session.user_id)
        return token


# ── Intervals.icu OAuth ───────────────────────────────────────


@app.post("/api/intervals/authorize")
async def intervals_authorize(
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    try:
        response = intervals_service.build_authorization_url(user_context.user_id)
    except IntervalsConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return response.model_dump(mode="json")


@app.get("/api/intervals/callback")
async def intervals_callback(
    code: str | None = None,
    error: str | None = None,
    state: str | None = None,
) -> RedirectResponse:
    profile_error_url = f"{settings.base_url}/profile?intervals=error"
    if error is not None:
        logger.info("intervals callback denied error=%s", error)
        return RedirectResponse(profile_error_url, status_code=302)
    if not code or not state:
        logger.warning("intervals callback missing required parameters")
        return RedirectResponse(profile_error_url, status_code=302)

    try:
        await intervals_service.exchange_code_for_connection(code=code, state=state)
    except (
        IntervalsConfigurationError,
        IntervalsOAuthExchangeError,
        IntervalsRepositoryNotConfiguredError,
        IntervalsStateError,
    ) as exc:
        logger.warning("intervals callback failed error_type=%s", type(exc).__name__)
        return RedirectResponse(profile_error_url, status_code=302)

    logger.info("intervals callback completed")
    return RedirectResponse(f"{settings.base_url}/profile?intervals=connected", status_code=302)


@app.get("/api/intervals/status")
async def intervals_status(
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    try:
        status = intervals_service.get_status(user_context.user_id)
    except IntervalsRepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return status.model_dump(mode="json")


@app.delete("/api/intervals/connection")
async def intervals_disconnect(
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    try:
        status = intervals_service.disconnect(user_context.user_id)
    except IntervalsRepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return status.model_dump(mode="json")


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
        logger.info("oauth consent decision: session expired client_id=%s", client_id)
        return RedirectResponse(
            auth_service.build_login_redirect(auth_service.build_consent_redirect(request)),
            status_code=302,
        )
    if decision != "approve":
        logger.info(
            "oauth consent denied client_id=%s user_id=%s", client_id, browser_session.user_id
        )
        denial_redirect = (
            f"{redirect_uri}?error=access_denied&state={state}"
            if state
            else f"{redirect_uri}?error=access_denied"
        )
        return RedirectResponse(denial_redirect, status_code=302)
    try:
        redirect_target = auth_service.approve_consent(
            request=request, browser_session=browser_session
        )
    except OAuthRepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "oauth consent approved client_id=%s user_id=%s", client_id, browser_session.user_id
    )
    return RedirectResponse(redirect_target, status_code=302)


# ── Chat ──────────────────────────────────────────────────────


@app.get("/api/chat/thread")
async def get_chat_thread(
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    try:
        bootstrap = await chat_service.bootstrap_thread(user_context.user_id)
    except ChatUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return bootstrap.model_dump(mode="json")


@app.post("/api/chat/messages")
async def persist_chat_message(
    payload: ChatPersistRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    try:
        message = await chat_service.persist_message(
            user_context.user_id,
            role=payload.role,
            parts=payload.parts,
            metadata=payload.metadata,
            attachments=payload.attachments,
            message_id=str(payload.id) if payload.id is not None else None,
        )
    except ChatUnavailableError as exc:
        logger.exception("chat unavailable user_id=%s", user_context.user_id)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RepositoryNotConfiguredError as exc:
        logger.exception("repository not configured")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    logger.info(
        "chat message saved user_id=%s role=%s message_id=%s parts=%d attachments=%d",
        user_context.user_id,
        payload.role,
        message.id,
        len(payload.parts),
        len(payload.attachments),
    )
    return message.model_dump(mode="json")


@app.get("/api/chat/messages")
async def list_chat_messages(
    limit: int = 50,
    before: str | None = None,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    if limit < 1 or limit > MAX_CHAT_MESSAGE_PAGE_SIZE:
        raise HTTPException(
            status_code=422,
            detail=f"limit must be between 1 and {MAX_CHAT_MESSAGE_PAGE_SIZE}",
        )
    try:
        page = await chat_service.list_messages(user_context.user_id, limit=limit, before=before)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (PostgRESTAPIError, httpx.HTTPError) as exc:
        logger.exception("chat messages list failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Chat session service unavailable") from exc
    return page.model_dump(mode="json")


@app.get("/api/chat/model-state")
async def get_chat_model_state(
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    try:
        state = await chat_service.get_model_state(user_context.user_id)
    except RepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (PostgRESTAPIError, httpx.HTTPError) as exc:
        logger.exception("chat model state get failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Chat session service unavailable") from exc
    return state.model_dump(mode="json")


@app.put("/api/chat/model-state")
async def replace_chat_model_state(
    payload: ChatModelStateReplaceRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    return await _run_chat_model_state_operation(
        lambda: chat_service.replace_model_state(user_context.user_id, payload),
        failure_log_message="chat model state replace failed error_type=%s",
    )


@app.post("/api/chat/model-state/lease")
async def acquire_chat_turn_lease(
    payload: ChatTurnLeaseRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    return await _run_chat_model_state_operation(
        lambda: chat_service.acquire_turn_lease(
            user_context.user_id, payload.lease_id, ttl_seconds=payload.ttl_seconds
        ),
        failure_log_message="chat lease acquire failed error_type=%s",
    )


@app.delete("/api/chat/model-state/lease")
async def release_chat_turn_lease(
    payload: ChatTurnLeaseReleaseRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    return await _run_chat_model_state_operation(
        lambda: chat_service.release_turn_lease(user_context.user_id, payload.lease_id),
        failure_log_message="chat lease release failed error_type=%s",
    )


_ALLOWED_UPLOAD_TYPES: frozenset[str] = frozenset(
    {
        # Images — sent to the model as input_image via the Responses API
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
        # Activity files — parsed server-side or described as text for the coach
        "application/gpx+xml",
        "application/vnd.garmin.fit",
        "application/vnd.garmin.tcx+xml",
    }
)


def _check_upload_content_type(content_type: str) -> None:
    if content_type not in _ALLOWED_UPLOAD_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Uploading {content_type} files is not supported. "
                "Supported types: images (GIF, JPEG, PNG, WebP) "
                "and activity files (.gpx, .fit, .tcx)."
            ),
        )


@app.post("/api/chat/attachments/presign")
async def presign_chat_upload(
    payload: PresignUploadRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    _check_upload_content_type(payload.content_type)
    try:
        presigned_upload = r2_service.create_presigned_upload(
            user_id=user_context.user_id, request=payload
        )
    except Exception as exc:
        logger.exception("chat presign failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Storage service unavailable") from exc
    logger.info(
        "chat attachment presign issued user_id=%s content_type=%s",
        user_context.user_id,
        payload.content_type,
    )
    return presigned_upload.model_dump(mode="json")


@app.post("/api/files/presign-upload")
async def presign_upload(
    payload: PresignUploadRequest, user_context: UserContext = Depends(require_user_context)
) -> Mapping[str, object]:
    _check_upload_content_type(payload.content_type)
    try:
        presigned_upload = r2_service.create_presigned_upload(
            user_id=user_context.user_id, request=payload
        )
    except Exception as exc:
        logger.exception("file presign failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Storage service unavailable") from exc
    logger.info(
        "file presign issued user_id=%s content_type=%s",
        user_context.user_id,
        payload.content_type,
    )
    return presigned_upload.model_dump(mode="json")


@app.post("/api/chat/attachments/upload")
async def upload_chat_attachment(
    object_key: str = Form(...),
    file: UploadFile = File(...),
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    """Upload a file directly to R2 storage via backend proxy."""
    expected_prefix = f"users/{user_context.user_id}/"
    if not object_key.startswith(expected_prefix):
        raise HTTPException(
            status_code=403, detail="object_key does not belong to authenticated user"
        )
    upload_result = await r2_service.upload_file(
        user_id=user_context.user_id,
        object_key=object_key,
        file_stream=file.file,
        content_type=file.content_type or "application/octet-stream",
    )
    logger.info(
        "chat attachment uploaded user_id=%s content_type=%s object_key_suffix=...%s",
        user_context.user_id,
        file.content_type,
        object_key[-12:],
    )
    return upload_result.model_dump(mode="json")


# Guards the calendar endpoint against unbounded scans; the UI window is
# ~15 weeks (42 days back + 8 weeks ahead), so this leaves generous headroom.
_CALENDAR_MAX_RANGE_DAYS = 200


@app.get("/api/calendar")
async def get_calendar(
    start: date,
    end: date,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    """Planned workouts and recorded activities for the agenda/calendar view."""
    if start > end:
        raise HTTPException(status_code=400, detail="start must be on or before end")
    if (end - start).days > _CALENDAR_MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"date range too large; maximum is {_CALENDAR_MAX_RANGE_DAYS} days",
        )

    planned, activities = await asyncio.gather(
        repo.list_plan_workouts_between(user_context.user_id, start=start, end=end),
        repo.list_activities_between(user_context.user_id, start=start, end=end),
    )
    logger.debug(
        "calendar user_id=%s start=%s end=%s planned=%d activities=%d",
        user_context.user_id,
        start,
        end,
        len(planned),
        len(activities),
    )
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "planned_workouts": [workout.model_dump(mode="json") for workout in planned],
        "activities": [activity.model_dump(mode="json") for activity in activities],
    }


# ── Engine endpoints ──────────────────────────────────────────


class CalculateZonesRequest(BaseModel):
    sport: str
    ftp_watts: int | None = None
    lt1_power_watts: int | None = None
    lt2_pace_sec_km: int | None = None
    lt1_pace_sec_km: int | None = None
    max_hr: int | None = None
    lt2_hr: int | None = None
    lt1_hr: int | None = None


@app.post("/api/engine/calculate-zones")
async def calculate_zones(
    payload: CalculateZonesRequest,
    _: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    from backend.engine.zones import compute_zones

    logger.info("calculate zones sport=%s", payload.sport)
    zones = compute_zones(
        payload.sport,
        ftp_watts=payload.ftp_watts,
        lt1_power_watts=payload.lt1_power_watts,
        lt2_pace_sec_km=payload.lt2_pace_sec_km,
        lt1_pace_sec_km=payload.lt1_pace_sec_km,
        max_hr=payload.max_hr,
        lt2_hr=payload.lt2_hr,
        lt1_hr=payload.lt1_hr,
    )
    return {"zones": [z.to_dict() for z in zones]}


class ComputeTSSRequest(BaseModel):
    duration_seconds: int
    sport: str = "general"
    normalized_power: int | None = None
    ftp: int | None = None
    avg_pace_sec_km: int | None = None
    threshold_pace_sec_km: int | None = None
    avg_hr: int | None = None
    resting_hr: int | None = None
    max_hr: int | None = None
    biological_sex: str = "not_specified"
    rpe: int | None = None


@app.post("/api/engine/compute-tss")
async def compute_tss_endpoint(
    payload: ComputeTSSRequest,
    _: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    from backend.engine.tss import compute_tss

    if payload.normalized_power:
        method = "power"
    elif payload.avg_pace_sec_km:
        method = "pace"
    elif payload.avg_hr:
        method = "hr"
    else:
        method = "rpe"
    logger.debug(
        "compute tss sport=%s method=%s duration_s=%d",
        payload.sport,
        method,
        payload.duration_seconds,
    )
    tss = compute_tss(
        payload.duration_seconds,
        sport=payload.sport,
        normalized_power=payload.normalized_power,
        ftp=payload.ftp,
        avg_pace_sec_km=payload.avg_pace_sec_km,
        threshold_pace_sec_km=payload.threshold_pace_sec_km,
        avg_hr=payload.avg_hr,
        resting_hr=payload.resting_hr,
        max_hr=payload.max_hr,
        biological_sex=payload.biological_sex,
        rpe=payload.rpe,
    )
    return {"tss": round(tss, 1)}


class EstimateThresholdsRequest(BaseModel):
    sport: str
    # Running
    race_time_seconds: int | None = None
    race_distance_meters: int | None = None
    # Cycling
    test_power_watts: int | None = None
    test_duration_minutes: int | None = None


@app.post("/api/engine/estimate-thresholds")
async def estimate_thresholds_endpoint(
    payload: EstimateThresholdsRequest,
    _: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    from backend.engine.thresholds import estimate_cycling_thresholds, estimate_running_thresholds

    logger.info("estimate thresholds sport=%s", payload.sport)
    if payload.sport == "running" and payload.race_time_seconds:
        result = estimate_running_thresholds(
            payload.race_time_seconds,
            payload.race_distance_meters or 5000,
        )
        return {
            "sport": "running",
            "vdot": result.vdot,
            "lt2_pace_sec_km": result.lt2_pace_sec_km,
            "lt1_pace_sec_km": result.lt1_pace_sec_km,
            "easy_pace_sec_km": result.easy_pace_sec_km,
        }

    if payload.sport == "cycling" and payload.test_power_watts and payload.test_duration_minutes:
        result = estimate_cycling_thresholds(
            payload.test_power_watts,
            payload.test_duration_minutes,
        )
        return {
            "sport": "cycling",
            "ftp_watts": result.ftp_watts,
            "lt1_watts": result.lt1_watts,
        }

    raise HTTPException(status_code=400, detail="Insufficient data for threshold estimation.")


class RecalibrateThresholdsRequest(BaseModel):
    pass


@app.post("/api/engine/recalibrate-thresholds")
async def recalibrate_thresholds_endpoint(
    payload: RecalibrateThresholdsRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    """Re-estimate thresholds from recent athlete-owned performance evidence.

    Persists a candidate only when the evaluation returns status
    "recalibrated"; every other status (insufficient_evidence,
    already_user_confirmed, no_change) is a non-mutating response.
    """
    from backend.services.recalibration import (
        ESTIMABLE_SPORTS,
        RECALIBRATION_LOOKBACK_DAYS,
        evaluate_all,
    )

    user_id = user_context.user_id
    since = date.today() - timedelta(days=RECALIBRATION_LOOKBACK_DAYS)

    current_thresholds = await _activity_repo_call(
        repo.get_active_thresholds(user_id),
        detail="Failed to load thresholds.",
        log_message=f"get_active_thresholds failed for user_id={user_id}",
    )
    current_by_sport = {}
    for threshold in current_thresholds:
        current_by_sport.setdefault(threshold.sport, threshold)

    activities_by_sport = {}
    for sport in ESTIMABLE_SPORTS:
        activities_by_sport[sport] = await _activity_repo_call(
            repo.list_activities(user_id, sport=sport, since=since, limit=200),
            detail="Failed to load activities.",
            log_message=f"list_activities failed for user_id={user_id} sport={sport}",
        )

    results = evaluate_all(activities_by_sport, current_by_sport, user_id)

    for result in results:
        if result.status == "recalibrated" and result.candidate is not None:
            saved = await _activity_repo_call(
                repo.upsert_sport_threshold(result.candidate),
                detail="Failed to save recalibrated threshold.",
                log_message=(
                    f"upsert_sport_threshold failed for user_id={user_id} sport={result.sport}"
                ),
            )
            logger.info(
                "threshold recalibrated user_id=%s sport=%s confidence=%s method=%s",
                user_id,
                result.sport,
                result.confidence,
                saved.estimation_method,
            )
        else:
            logger.info(
                "threshold recalibration skipped user_id=%s sport=%s status=%s",
                user_id,
                result.sport,
                result.status,
            )

    return {
        "results": [
            {
                "sport": r.sport,
                "status": r.status,
                "confidence": r.confidence,
                "explanation": r.explanation,
                "evidence_activity_id": r.evidence_activity_id,
            }
            for r in results
        ]
    }


class RecomputeLoadRequest(BaseModel):
    since: date | None = None
    sport: str | None = None


@app.post("/api/engine/recompute-load")
async def recompute_load_endpoint(
    payload: RecomputeLoadRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    from backend.engine.training_load import recompute_load_series

    user_id = user_context.user_id
    since = payload.since or date.today()
    activities = await repo.list_activities(user_id, sport=payload.sport, since=since, limit=500)
    logger.debug(
        "recompute_load user_id=%s sport=%s since=%s activities=%d",
        user_id,
        payload.sport,
        since,
        len(activities),
    )

    daily_tss: dict[date, float] = {}
    for a in activities:
        daily_tss[a.activity_date] = daily_tss.get(a.activity_date, 0) + (a.tss or 0)

    prev = await repo.get_latest_load(user_id, sport=payload.sport)
    initial_ctl = prev.ctl if prev else 0.0
    initial_atl = prev.atl if prev else 0.0

    snapshots = recompute_load_series(daily_tss, since, date.today(), initial_ctl, initial_atl)

    await repo.upsert_load_snapshots(user_id, snapshots, sport=payload.sport)

    latest = snapshots[-1] if snapshots else {}
    logger.info(
        "load recomputed user_id=%s sport=%s snapshots=%d ctl=%.1f atl=%.1f tsb=%.1f",
        user_id,
        payload.sport,
        len(snapshots),
        latest.get("ctl", 0),
        latest.get("atl", 0),
        latest.get("tsb", 0),
    )
    return {
        "snapshots_written": len(snapshots),
        "latest_ctl": latest.get("ctl", 0),
        "latest_atl": latest.get("atl", 0),
        "latest_tsb": latest.get("tsb", 0),
    }


class AnalyzeScreenshotRequest(BaseModel):
    image_url: str


class ProcessUploadedFileRequest(BaseModel):
    content_type: str
    filename: str
    object_key: str
    public_url: str | None = None


@app.post("/api/engine/analyze-screenshot")
async def analyze_screenshot_endpoint(
    payload: AnalyzeScreenshotRequest,
    _: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    from backend.services.screenshot import analyze_screenshot

    result = await analyze_screenshot(payload.image_url)
    return {
        "screenshot_type": result.screenshot_type,
        "data": result.data,
        "raw_response": result.raw_response,
    }


def _activity_source_for_filename(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".fit":
        return "fit_upload"
    if suffix == ".gpx":
        return "gpx_upload"
    if suffix == ".tcx":
        return "tcx_upload"
    return "file_upload"


def _parse_uploaded_activity_file(filename: str, content_type: str, file_bytes: bytes):
    from backend.engine.gpx_parser import parse_fit, parse_gpx, parse_tcx

    suffix = Path(filename).suffix.lower()
    if content_type == "application/gpx+xml" or suffix == ".gpx":
        parser = parse_gpx
        suffix = ".gpx"
    elif content_type == "application/vnd.garmin.fit" or suffix == ".fit":
        parser = parse_fit
        suffix = ".fit"
    elif content_type == "application/vnd.garmin.tcx+xml" or suffix == ".tcx":
        parser = parse_tcx
        suffix = ".tcx"
    else:
        raise HTTPException(status_code=415, detail="Unsupported activity file type.")

    with NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp.flush()
        return parser(tmp.name)


@app.post("/api/engine/process-uploaded-file")
async def process_uploaded_file_endpoint(
    payload: ProcessUploadedFileRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    logger.info(
        "processing uploaded file user_id=%s filename_suffix=%s content_type=%s",
        user_context.user_id,
        Path(payload.filename).suffix.lower()[:16] or "none",
        payload.content_type,
    )
    file_bytes = await r2_service.download_file_bytes(
        user_id=user_context.user_id,
        object_key=payload.object_key,
    )
    parsed = _parse_uploaded_activity_file(payload.filename, payload.content_type, file_bytes)
    activity = Activity(
        user_id=user_context.user_id,
        sport=parsed.sport,
        activity_date=parsed.activity_date,
        started_at=parsed.started_at,
        duration_seconds=parsed.duration_seconds,
        distance_meters=parsed.distance_meters,
        elevation_gain_meters=parsed.elevation_gain_meters,
        avg_hr_bpm=parsed.avg_hr_bpm,
        max_hr_bpm=parsed.max_hr_bpm,
        avg_power_watts=parsed.avg_power_watts,
        avg_cadence_rpm=parsed.avg_cadence_rpm,
        source=_activity_source_for_filename(payload.filename),
        source_file_key=payload.object_key,
        raw_extraction={
            "content_type": payload.content_type,
            "filename": payload.filename,
            "hrv": parsed.hrv_summary,
            "public_url": payload.public_url,
            "rr_interval_count": len(parsed.rr_intervals_ms or []),
        },
    )
    activity = activity.model_copy(
        update={"activity_summary": build_activity_summary_from_fields(activity)}
    )
    logger.info(
        "activity parsed user_id=%s sport=%s date=%s distance_m=%.0f",
        user_context.user_id,
        parsed.sport,
        parsed.activity_date,
        parsed.distance_meters or 0,
    )
    return {"activity": activity.model_dump(mode="json")}


def _build_fitness_metrics(
    profile: _AthleteProfile, thresholds: list[SportThreshold]
) -> dict[str, object]:
    """Assemble ThresholdValue objects from profile + active sport thresholds."""
    metrics: dict[str, object] = {}

    max_hr_tv = profile.max_hr_threshold_value()
    if max_hr_tv:
        metrics["max_hr"] = max_hr_tv.model_dump(mode="json")

    weight_tv = profile.weight_threshold_value()
    if weight_tv:
        metrics["weight"] = weight_tv.model_dump(mode="json")

    metrics["best_times"] = [bt.model_dump(mode="json") for bt in profile.best_times]

    for t in thresholds:
        if t.sport == "cycling" and t.lt2_power_watts is not None:
            metrics["cycling_ftp"] = t.as_threshold_value(t.lt2_power_watts, "W").model_dump(
                mode="json"
            )
        if t.sport == "running" and t.lt2_pace_sec_per_km is not None:
            metrics["run_threshold_pace"] = t.as_threshold_value(
                t.lt2_pace_sec_per_km, "sec/km"
            ).model_dump(mode="json")
        if t.sport == "swimming" and t.css_sec_per_100 is not None:
            metrics["swim_css"] = t.as_threshold_value(t.css_sec_per_100, "sec/100m").model_dump(
                mode="json"
            )

    return metrics


@app.post("/api/engine/get-athlete-summary")
async def get_athlete_summary(
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    from backend.engine.thresholds import estimate_ctl_ceiling

    user_id = user_context.user_id
    try:
        profile = await repo.get_athlete_profile(user_id)
    except RecordNotFoundError:
        logger.info("athlete summary: no profile yet user_id=%s (onboarding stub)", user_id)
        profile = _AthleteProfile(user_id=user_id, coaching_state="onboarding")

    thresholds = await repo.get_active_thresholds(user_id)
    goals = await repo.list_active_goals(user_id)
    latest_load = await repo.get_latest_load(user_id)
    recovery = await repo.list_recovery_logs(user_id, limit=7)
    schedule = await repo.get_schedule(user_id)
    active_plan = await repo.get_active_plan(user_id)

    age = profile.age
    ctl_ceiling = estimate_ctl_ceiling(age, profile.biological_sex or "not_specified")

    logger.debug(
        "athlete summary assembled user_id=%s state=%s thresholds=%d goals=%d has_load=%s",
        user_id,
        profile.coaching_state,
        len(thresholds),
        len(goals),
        latest_load is not None,
    )
    return {
        "profile": profile.model_dump(mode="json"),
        "computed_age": age,
        "thresholds": [t.model_dump(mode="json") for t in thresholds],
        "fitness_metrics": _build_fitness_metrics(profile, thresholds),
        "goals": [g.model_dump(mode="json") for g in goals],
        "current_load": latest_load.model_dump(mode="json") if latest_load else None,
        "recent_recovery": [r.model_dump(mode="json") for r in recovery],
        "schedule": schedule.model_dump(mode="json") if schedule else None,
        "active_plan": active_plan.model_dump(mode="json") if active_plan else None,
        "ctl_ceiling_guidance": {
            "age_bracket": ctl_ceiling.age_bracket,
            "elite_ctl": ctl_ceiling.elite_ctl,
            "committed_amateur_ctl": ctl_ceiling.committed_amateur_ctl,
            "recreational_ctl": ctl_ceiling.recreational_ctl,
            "recovery_week_frequency": ctl_ceiling.recovery_week_frequency,
            "notes": ctl_ceiling.notes,
        },
    }


class UpdateAthleteProfileRequest(BaseModel):
    fields: dict[str, object]


@app.post("/api/engine/update-athlete-profile")
async def update_athlete_profile(
    payload: UpdateAthleteProfileRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    logger.info(
        "profile update user_id=%s fields=%s",
        user_context.user_id,
        list(payload.fields.keys()),
    )
    try:
        profile = await repo.update_athlete_profile_fields(user_context.user_id, payload.fields)
    except RepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("profile update unavailable user_id=%s", user_context.user_id)
        raise HTTPException(status_code=503, detail="Unable to update athlete profile.") from exc
    return profile.model_dump(mode="json")


class GetRecentActivitiesRequest(BaseModel):
    sport: str | None = None
    limit: int = 20


class UpdateGoalsRequest(BaseModel):
    action: str
    goal: dict[str, object] | None = None
    goal_id: str | None = None


@app.post("/api/engine/update-goals")
async def update_goals_endpoint(
    payload: UpdateGoalsRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    if payload.action in ("update", "complete", "abandon") and not payload.goal_id:
        raise HTTPException(
            status_code=400,
            detail="goal_id required for update/complete/abandon",
        )
    try:
        result = await goal_service.apply_action(
            user_context.user_id,
            payload.action,
            payload.goal or {},
            payload.goal_id,
            repo=repo,
        )
    except InvalidGoalPayloadError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    except UnknownGoalActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("update_goals failed user_id=%s", user_context.user_id)
        raise HTTPException(status_code=503, detail="Unable to update goals.") from exc
    return result.model_dump(mode="json")


class UpdateScheduleRequest(BaseModel):
    # Merge semantics (issue #232):
    #   weekly_pattern — full replacement of the recurring weekday template.
    #   overrides      — per-date upsert (on_conflict=user_id,override_date).
    # Typed per-day validation (max_hours in [0, 24]) rejects bad input as 422.
    weekly_pattern: dict[str, ScheduleDayAvailability] | None = None
    overrides: list[dict[str, object]] | None = None


@app.post("/api/engine/update-schedule")
async def update_schedule_endpoint(
    payload: UpdateScheduleRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    user_id = user_context.user_id
    updated: list[str] = []
    try:
        if payload.weekly_pattern is not None:
            # weekly_pattern is a full replacement of the recurring weekday template.
            weekly_pattern = {
                day: entry.model_dump(mode="json") for day, entry in payload.weekly_pattern.items()
            }
            schedule = ScheduleAvailability(user_id=user_id, weekly_pattern=weekly_pattern)
            await repo.upsert_schedule(schedule)
            updated.append("weekly_pattern")
        if payload.overrides:
            # Each override upserts on (user_id, override_date).
            for ov in payload.overrides:
                override = ScheduleOverride.model_validate({"user_id": user_id, **ov})
                await repo.upsert_schedule_override(override)
            updated.append("overrides")
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except Exception as exc:
        logger.exception("update_schedule failed user_id=%s", user_id)
        raise HTTPException(status_code=503, detail="Unable to update schedule.") from exc
    return {"updated": updated}


class SaveRecoveryDataRequest(BaseModel):
    entries: list[dict[str, object]] = Field(min_length=1)


def _recovery_log_from_entry(entry: Mapping[str, object], user_id: str) -> RecoveryLog:
    # user_id is always derived from the bearer token; never trust the client payload.
    fields = {key: value for key, value in entry.items() if key != "user_id"}
    if fields.get("log_date") is None:
        fields["log_date"] = datetime.now(UTC).date().isoformat()
    fields["user_id"] = user_id
    return RecoveryLog.model_validate(fields)


@app.post("/api/engine/save-recovery-data")
async def save_recovery_data_endpoint(
    payload: SaveRecoveryDataRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    user_id = user_context.user_id
    # Validate every entry up front so a malformed entry never leaves earlier
    # entries partially persisted with a 422 that implies nothing was saved.
    try:
        logs = [_recovery_log_from_entry(entry, user_id) for entry in payload.entries]
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    saved: list[Mapping[str, object]] = []
    try:
        for log in logs:
            persisted = await repo.upsert_recovery_log(log)
            saved.append(persisted.model_dump(mode="json"))
    except RepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("save_recovery_data failed user_id=%s", user_id)
        raise HTTPException(status_code=503, detail="Unable to save recovery data.") from exc
    logger.info("save_recovery_data user_id=%s count=%d", user_id, len(saved))
    return {"saved": saved, "count": len(saved)}


@app.post("/api/engine/get-recent-activities")
async def get_recent_activities(
    payload: GetRecentActivitiesRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    activities = await repo.list_activities(
        user_context.user_id,
        sport=payload.sport,
        limit=min(max(payload.limit, 1), 100),
    )
    logger.debug(
        "recent activities user_id=%s sport=%s count=%d",
        user_context.user_id,
        payload.sport,
        len(activities),
    )
    return {"activities": [activity.model_dump(mode="json") for activity in activities]}


class SaveActivityFromTextRequest(BaseModel):
    text: str = Field(min_length=1)
    activity_id: str | None = None


async def _activity_repo_call(awaitable, *, detail: str, log_message: str):
    try:
        return await awaitable
    except RepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (PostgRESTAPIError, httpx.HTTPError) as exc:
        logger.exception("%s error_type=%s", log_message, type(exc).__name__)
        raise HTTPException(status_code=503, detail=detail) from exc


async def _update_activity_from_text(
    user_id: str,
    activity_id: str,
    text: str,
) -> Mapping[str, object]:
    from backend.services.activity_text import (
        ActivityTextExtractionUnavailable,
        merge_activity_text_update,
    )

    try:
        existing = await _activity_repo_call(
            repo.get_activity(user_id, activity_id),
            detail="Failed to load activity.",
            log_message=f"get_activity failed for user_id={user_id} activity_id={activity_id}",
        )
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Activity not found.") from exc
    try:
        updated = await merge_activity_text_update(existing, text)
    except ActivityTextExtractionUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        activity = await _activity_repo_call(
            repo.update_activity(updated),
            detail="Failed to update activity.",
            log_message=f"update_activity failed for user_id={user_id} activity_id={activity_id}",
        )
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Activity not found.") from exc
    except RuntimeError as exc:
        logger.exception(
            "update_activity failed for user_id=%s activity_id=%s", user_id, activity_id
        )
        raise HTTPException(status_code=503, detail="Failed to update activity.") from exc
    logger.info(
        "save_activity_from_text user_id=%s activity_id=%s status=updated", user_id, activity_id
    )
    return {"activity": activity.model_dump(mode="json"), "status": "updated"}


@app.post("/api/engine/save-activity-from-text")
async def save_activity_from_text(
    payload: SaveActivityFromTextRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    from backend.services.activity_text import (
        ActivityTextExtractionUnavailable,
        build_activity_from_text,
    )

    if not payload.text.strip():
        raise HTTPException(status_code=422, detail="Activity text must not be empty.")

    if payload.activity_id is not None:
        activity_id = payload.activity_id.strip()
        if not activity_id:
            raise HTTPException(status_code=422, detail="Activity id must not be empty.")
        return await _update_activity_from_text(user_context.user_id, activity_id, payload.text)

    try:
        profile = await _activity_repo_call(
            repo.get_athlete_profile(user_context.user_id),
            detail="Failed to load athlete profile.",
            log_message=f"get_athlete_profile failed for user_id={user_context.user_id}",
        )
    except RecordNotFoundError:
        profile = _AthleteProfile(user_id=user_context.user_id)
    thresholds = await _activity_repo_call(
        repo.get_active_thresholds(user_context.user_id),
        detail="Failed to load athlete thresholds.",
        log_message=f"get_active_thresholds failed for user_id={user_context.user_id}",
    )
    try:
        result = await build_activity_from_text(
            payload.text,
            user_id=user_context.user_id,
            profile=profile,
            thresholds=thresholds,
        )
    except ActivityTextExtractionUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if result.activity is None:
        logger.info(
            "save_activity_from_text user_id=%s status=needs_clarification missing=%s",
            user_context.user_id,
            result.missing,
        )
        return {
            "missing": result.missing,
            "raw_extraction": result.raw_extraction,
            "status": "needs_clarification",
        }
    return await _persist_extracted_activity(user_context.user_id, result.activity)


async def _persist_extracted_activity(user_id: str, extracted: Activity) -> Mapping[str, object]:
    try:
        activity = await _activity_repo_call(
            repo.create_activity(extracted),
            detail="Failed to save activity.",
            log_message=f"create_activity failed for user_id={user_id}",
        )
    except RuntimeError as exc:
        logger.exception("create_activity failed for user_id=%s", user_id)
        raise HTTPException(status_code=503, detail="Failed to save activity.") from exc
    logger.info("save_activity_from_text user_id=%s status=saved", user_id)
    matched = await _try_match_activity_to_plan(user_id, activity)
    response: dict[str, object] = {"activity": activity.model_dump(mode="json"), "status": "saved"}
    if matched is not None:
        response["matched_plan_workout"] = matched
    return response


TrainingModelRequest = Literal["auto", "longevity", "performance", "recovery_return"]


class GeneratePlanStructureRequest(BaseModel):
    goal_id: str | None = None
    training_model: TrainingModelRequest = "auto"


def _select_training_model(
    requested: TrainingModelRequest, target_goal: Goal | None
) -> tuple[Literal["longevity", "performance", "recovery_return"], str]:
    if requested != "auto":
        return requested, "explicit"
    if target_goal is not None and target_goal.goal_type in {"event", "mountain", "improvement"}:
        return "performance", "auto"
    return "longevity", "auto"


@app.post("/api/engine/generate-plan-structure")
async def generate_plan_structure(  # noqa: C901
    payload: GeneratePlanStructureRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    from backend.engine.periodization import build_plan_skeleton
    from backend.engine.thresholds import estimate_ctl_ceiling

    user_id = user_context.user_id
    profile = await repo.get_athlete_profile(user_id)
    goals = await repo.list_active_goals(user_id)
    latest_load = await repo.get_latest_load(user_id)

    target_goal = None
    if payload.goal_id:
        target_goal = next((g for g in goals if g.id == payload.goal_id), None)
        if target_goal is None:
            logger.warning(
                "generate_plan: goal_id not found, falling back to first goal"
                " user_id=%s goal_id=%s",
                user_id,
                payload.goal_id,
            )
    elif goals:
        target_goal = goals[0]

    current_ctl = latest_load.ctl if latest_load else 0.0
    available_hours = profile.weekly_available_hours or 6.0

    age = profile.age
    ceiling = estimate_ctl_ceiling(age, profile.biological_sex or "not_specified")
    recovery_freq = 4 if age is None or age < RECOVERY_WEEK_AGE_BREAKPOINT else 3

    skeleton = build_plan_skeleton(
        current_ctl=current_ctl,
        target_date=target_goal.target_date if target_goal else None,
        available_hours_per_week=available_hours,
        goal_type=target_goal.goal_type if target_goal else "maintenance",
        recovery_week_frequency=recovery_freq,
    )

    from backend.services.plan_composer import PlanComposerPolicy, compose_plan_workouts

    plan_sport = (
        (target_goal.sport if target_goal else None)
        or (profile.primary_sports[0] if profile.primary_sports else None)
        or "cycling"
    )
    training_model, training_model_source = _select_training_model(
        payload.training_model, target_goal
    )
    plan = TrainingPlan(
        user_id=user_id,
        title=(f"Plan: {target_goal.title}" if target_goal else "Rolling training plan"),
        plan_type="full_cycle" if target_goal and target_goal.target_date else "weekly",
        status="active",
        start_date=skeleton.start_date,
        end_date=skeleton.end_date,
        target_goal_id=target_goal.id if target_goal else None,
        phases=[p.to_dict() for p in skeleton.phases],
        generation_context={
            "training_model": training_model,
            "training_model_source": training_model_source,
        },
        weekly_tss_target=round(skeleton.starting_weekly_tss, 1),
        weekly_hours_target=available_hours,
    )
    persisted_plan = None
    try:
        schedule = await repo.get_schedule(user_id)
        overrides = await repo.list_schedule_overrides_between(
            user_id, start=skeleton.start_date, end=skeleton.end_date
        )
        # Capture the plan being superseded *before* the insert so we can clean up
        # its future scheduled workouts and leave one coherent calendar timeline.
        prior_plan = await repo.get_active_plan(user_id)
        persisted_plan = await repo.create_training_plan(plan)
        workouts = compose_plan_workouts(
            skeleton,
            user_id=user_id,
            plan_id=persisted_plan.id or "",
            sport=plan_sport,
            weekly_pattern=schedule.weekly_pattern if schedule else None,
            overrides=overrides,
            policy=PlanComposerPolicy(training_model=training_model),
        )
        persisted_workouts = await repo.create_plan_workouts(workouts)
        # Clean up the superseded plan's future scheduled workouts only *after* the
        # new plan's workouts are safely persisted — a failed insert must never leave
        # the athlete with the prior plan's future deleted and no replacement.
        if prior_plan is not None and prior_plan.id:
            await repo.delete_future_scheduled_workouts(user_id, prior_plan.id, skeleton.start_date)
    except RepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (PostgRESTAPIError, httpx.HTTPError, RuntimeError, ValueError) as exc:
        logger.exception("generate_plan: persistence failed user_id=%s", user_id)
        if persisted_plan is not None and persisted_plan.id:
            # Don't leave a workout-less plan active (create_training_plan
            # already superseded the previous one). Best-effort cleanup.
            try:
                await repo.update_training_plan_status(user_id, persisted_plan.id, "superseded")
            except Exception:
                logger.exception(
                    "generate_plan: failed to supersede partial plan user_id=%s plan_id=%s",
                    user_id,
                    persisted_plan.id,
                )
        raise HTTPException(status_code=503, detail="Failed to persist training plan.") from exc

    logger.info(
        "plan generated user_id=%s plan_id=%s weeks=%d workouts=%d goal_type=%s recovery_freq=%d",
        user_id,
        persisted_plan.id,
        skeleton.total_weeks,
        len(persisted_workouts),
        target_goal.goal_type if target_goal else "maintenance",
        recovery_freq,
    )
    return {
        "plan_id": persisted_plan.id,
        "sport": plan_sport,
        "training_model": training_model,
        "workouts_created": len(persisted_workouts),
        "total_weeks": skeleton.total_weeks,
        "start_date": skeleton.start_date.isoformat(),
        "end_date": skeleton.end_date.isoformat(),
        "starting_weekly_tss": round(skeleton.starting_weekly_tss),
        "phases": [p.to_dict() for p in skeleton.phases],
        "target_goal": target_goal.model_dump(mode="json") if target_goal else None,
        "ctl_ceiling": {
            "age_bracket": ceiling.age_bracket,
            "committed_amateur_ctl": ceiling.committed_amateur_ctl,
        },
    }


class AdjustPlanRequest(BaseModel):
    plan_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


def _rebuild_skeleton(plan: TrainingPlan):
    """Reconstruct a PlanSkeleton from a persisted plan's stored ``phases``.

    Recomposing from the plan's *own* skeleton (rather than a fresh one) keeps the
    periodization ramp continuous across an adjust, so future weeks stay faithful
    to the original build.
    """
    from backend.engine.periodization import PhasePlan, PlanSkeleton

    phases = [
        PhasePlan(
            name=str(p["name"]),
            start_week=int(p["start_week"]),
            end_week=int(p["end_week"]),
            focus=str(p["focus"]),
            target_weekly_tss=float(p["target_weekly_tss"]),
            z1_z2_pct=int(p["z1_z2_pct"]),
            max_hiit_per_week=int(p["max_hiit_per_week"]),
            description=str(p.get("description", "")),
        )
        for p in plan.phases
    ]
    total_weeks = max((phase.end_week for phase in phases), default=0)
    return PlanSkeleton(
        phases=phases,
        total_weeks=total_weeks,
        start_date=plan.start_date,
        end_date=plan.end_date,
        starting_weekly_tss=plan.weekly_tss_target or 0.0,
    )


@app.post("/api/engine/adjust-plan")
async def adjust_plan(
    payload: AdjustPlanRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    """Edit the active plan's *future scheduled* workouts in place (feedback loop).

    Unlike full generation, adjust never spawns a new plan: it recomposes only the
    remaining weeks on the same ``plan_id``, preserving completed/matched history
    (and therefore compliance %) untouched.
    """
    from typing import cast

    from backend.services.plan_composer import (
        PlanComposerPolicy,
        TrainingModel,
        compose_plan_workouts,
    )

    user_id = user_context.user_id
    today = datetime.now(UTC).date()
    from_date = today + timedelta(days=1)

    plan = await repo.get_active_plan(user_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="No active training plan to adjust.")
    if plan.id != payload.plan_id:
        raise HTTPException(
            status_code=409,
            detail="plan_id does not match the athlete's active plan.",
        )
    # plan.id == payload.plan_id (a validated non-empty str); use it downstream.
    plan_id = payload.plan_id

    try:
        # Sport lives on the workouts, not the plan; read it from surviving history.
        existing = await repo.list_plan_workouts(plan_id)
        plan_sport = existing[0].sport if existing else "cycling"
        # Future completed/matched workouts are history — never double-book a
        # recomposed session onto their dates. Derive this from the rows we already
        # fetched (no second query), so it is known *before* the destructive delete.
        surviving_dates = {
            w.workout_date
            for w in existing
            if w.workout_date >= from_date
            and (w.actual_activity_id is not None or w.status != "scheduled")
        }

        skeleton = _rebuild_skeleton(plan)
        schedule = await repo.get_schedule(user_id)
        overrides = await repo.list_schedule_overrides_between(
            user_id, start=from_date, end=plan.end_date
        )
        raw_model = str((plan.generation_context or {}).get("training_model", "performance"))
        training_model: TrainingModel = (
            cast(TrainingModel, raw_model)
            if raw_model in ("performance", "longevity", "recovery_return")
            else "performance"
        )

        # Compose the replacement rows (pure computation) *before* deleting anything,
        # so a recomposition failure can never leave the plan with its future wiped.
        # delete and re-insert both target this plan_id, so insert-before-delete would
        # clobber the new rows; keep them adjacent to minimise the non-atomic window.
        recomposed = compose_plan_workouts(
            skeleton,
            user_id=user_id,
            plan_id=plan_id,
            sport=plan_sport,
            weekly_pattern=schedule.weekly_pattern if schedule else None,
            overrides=overrides,
            policy=PlanComposerPolicy(training_model=training_model),
            from_date=from_date,
        )
        to_insert = [w for w in recomposed if w.workout_date not in surviving_dates]
        deleted = await repo.delete_future_scheduled_workouts(user_id, plan_id, from_date)
        inserted = await repo.create_plan_workouts(to_insert)

        # Append an audit entry to generation_context so adjusts are traceable.
        context = dict(plan.generation_context or {})
        adjustments = list(context.get("adjustments", []))
        adjustments.append({"reason": payload.reason, "at": datetime.now(UTC).isoformat()})
        context["adjustments"] = adjustments
        await repo.update_training_plan_generation_context(user_id, plan_id, context)
    except RepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (PostgRESTAPIError, httpx.HTTPError, RuntimeError, ValueError) as exc:
        logger.exception("adjust_plan: persistence failed user_id=%s plan_id=%s", user_id, plan_id)
        raise HTTPException(status_code=503, detail="Failed to adjust training plan.") from exc

    logger.info(
        "plan adjusted user_id=%s plan_id=%s deleted=%d inserted=%d reason=%r",
        user_id,
        plan_id,
        deleted,
        len(inserted),
        payload.reason,
    )
    return {
        "plan_id": plan_id,
        "status": "adjusted",
        "workouts_removed": deleted,
        "workouts_created": len(inserted),
        "from_date": from_date.isoformat(),
    }


async def _persist_workout_match(user_id: str, workout: PlanWorkout, activity: Activity) -> None:
    """Link a confident plan↔activity match on both sides."""
    await repo.match_plan_workout_to_activity(
        user_id=user_id,
        workout_id=workout.id or "",
        activity_id=activity.id or "",
        completion_source="auto_matched",
    )


async def _try_match_activity_to_plan(user_id: str, activity: Activity) -> dict[str, object] | None:
    """Write-time glue: opportunistically match a just-saved activity.

    Best-effort by design — a matching failure must never fail the save that
    triggered it, so errors are logged and swallowed.
    """
    from backend.services.compliance import MATCH_MAX_DAY_OFFSET, match_activities_to_workouts

    try:
        window = timedelta(days=MATCH_MAX_DAY_OFFSET)
        planned = await repo.list_plan_workouts_between(
            user_id,
            start=activity.activity_date - window,
            end=activity.activity_date + window,
        )
        matches = match_activities_to_workouts(planned, [activity], today=datetime.now(UTC).date())
        if not matches:
            return None
        match = matches[0]
        await _persist_workout_match(user_id, match.workout, activity)
        logger.info(
            "activity auto-matched user_id=%s activity_id=%s plan_workout_id=%s",
            user_id,
            activity.id,
            match.workout.id,
        )
        return {
            "plan_workout_id": match.workout.id,
            "workout_date": match.workout.workout_date.isoformat(),
            "title": match.workout.title,
        }
    except Exception:
        logger.exception("post-save plan matching failed user_id=%s", user_id)
        return None


@app.post("/api/engine/get-compliance-summary")
async def get_compliance_summary(
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    """Read-time reconciliation + planned-versus-done summary for the coach."""
    from backend.services.compliance import (
        MATCH_MAX_DAY_OFFSET,
        build_compliance_summary,
        compliance_window,
        match_activities_to_workouts,
    )

    user_id = user_context.user_id
    today = datetime.now(UTC).date()

    try:
        plan = await repo.get_active_plan(user_id)
        if plan is None:
            return {
                "status": "no_active_plan",
                "message": "No active training plan; generate one to track compliance.",
            }

        start, _ = compliance_window(plan.start_date, today)
        match_margin = timedelta(days=MATCH_MAX_DAY_OFFSET)
        planned, activities = await asyncio.gather(
            repo.list_plan_workouts_between(user_id, start=start, end=today + match_margin),
            repo.list_activities_between(
                user_id, start=start - match_margin, end=today + match_margin
            ),
        )

        matches = match_activities_to_workouts(planned, activities, today=today)
        persisted_matches = []
        for match in matches:
            try:
                await _persist_workout_match(user_id, match.workout, match.activity)
            except (RecordNotFoundError, RuntimeError):
                # A stale match (e.g. the workout or activity was deleted/reassigned
                # since the match was computed) must not abort the whole summary —
                # skip it and reconcile it again on the next read.
                logger.exception(
                    "get_compliance_summary: skipping stale match user_id=%s "
                    "plan_workout_id=%s activity_id=%s",
                    user_id,
                    match.workout.id,
                    match.activity.id,
                )
                continue
            persisted_matches.append(match)

        # Reflect the persisted matches in memory so the summary is built from
        # post-reconciliation state without a second round-trip.
        workout_to_activity = {m.workout.id: m.activity.id for m in persisted_matches}
        activity_to_workout = {m.activity.id: m.workout.id for m in persisted_matches}
        planned = [
            w.model_copy(
                update={
                    "status": "completed",
                    "actual_activity_id": workout_to_activity[w.id],
                    "completion_source": "auto_matched",
                }
            )
            if w.id in workout_to_activity
            else w
            for w in planned
        ]
        activities = [
            a.model_copy(update={"planned_workout_id": activity_to_workout[a.id]})
            if a.id in activity_to_workout
            else a
            for a in activities
        ]
    except RepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (PostgRESTAPIError, httpx.HTTPError) as exc:
        logger.exception("get_compliance_summary failed user_id=%s", user_id)
        raise HTTPException(status_code=503, detail="Failed to load compliance data.") from exc

    summary = build_compliance_summary(plan, planned, activities, today=today)
    logger.info(
        "compliance summary user_id=%s plan_id=%s matches=%d pct=%s unconfirmed=%d",
        user_id,
        plan.id,
        len(persisted_matches),
        summary["compliance_pct"],
        summary["totals"]["unconfirmed"],
    )
    return summary


class ResolvePlanWorkoutRequest(BaseModel):
    """Explicit athlete/coach resolution of a planned workout."""

    plan_workout_id: str = Field(min_length=1)
    outcome: Literal["completed", "skipped"]
    activity_id: str | None = None
    source: Literal["athlete", "coach"] = "coach"


async def _apply_workout_resolution(
    user_id: str, workout: PlanWorkout, payload: ResolvePlanWorkoutRequest
) -> PlanWorkout:
    if payload.outcome == "completed" and payload.activity_id:
        await repo.get_activity(user_id, payload.activity_id)
    return await repo.resolve_plan_workout_atomic(
        user_id=user_id,
        workout_id=workout.id or payload.plan_workout_id,
        outcome=payload.outcome,
        activity_id=payload.activity_id,
        source=payload.source,
    )


@app.post("/api/engine/resolve-plan-workout")
async def resolve_plan_workout(
    payload: ResolvePlanWorkoutRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    user_id = user_context.user_id
    try:
        workout = await repo.get_plan_workout(user_id, payload.plan_workout_id)
        updated = await _apply_workout_resolution(user_id, workout, payload)
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (PostgRESTAPIError, httpx.HTTPError) as exc:
        logger.exception(
            "resolve_plan_workout failed user_id=%s workout_id=%s",
            user_id,
            payload.plan_workout_id,
        )
        raise HTTPException(status_code=503, detail="Failed to resolve plan workout.") from exc

    logger.info(
        "plan workout resolved user_id=%s workout_id=%s outcome=%s source=%s",
        user_id,
        payload.plan_workout_id,
        payload.outcome,
        payload.source,
    )
    return {"workout": updated.model_dump(mode="json")}


class ConfirmThresholdRequest(BaseModel):
    """Promote an estimated/file-derived sport threshold to user-confirmed."""

    sport: str  # cycling | running | swimming | ...


@app.post("/api/engine/confirm-threshold")
async def confirm_threshold(
    payload: ConfirmThresholdRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    """Mark the active sport threshold as user-confirmed.

    Sets estimation_method=manual, confidence=high, source=user.
    """
    user_id = user_context.user_id
    client = repo._require_client()
    client.table("sport_thresholds").update(
        {"estimation_method": "manual", "confidence": "high", "source": "user"}
    ).eq("user_id", user_id).eq("sport", payload.sport).is_("superseded_at", "null").execute()
    logger.info("threshold confirmed user_id=%s sport=%s", user_id, payload.sport)

    thresholds = await repo.get_active_thresholds(user_id)
    confirmed = next((t for t in thresholds if t.sport == payload.sport), None)
    return confirmed.model_dump(mode="json") if confirmed else {}


class ConfirmProfileMetricRequest(BaseModel):
    """Promote a profile-level estimated metric (max_hr, weight) to user-confirmed."""

    metric: str  # "max_hr" | "weight"


@app.post("/api/engine/confirm-profile-metric")
async def confirm_profile_metric(
    payload: ConfirmProfileMetricRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    allowed_metrics = {"max_hr", "weight"}
    if payload.metric not in allowed_metrics:
        raise HTTPException(status_code=400, detail=f"metric must be one of {allowed_metrics}")

    source_field = f"{payload.metric}_source"
    profile = await repo.update_athlete_profile_fields(user_context.user_id, {source_field: "user"})
    logger.info(
        "profile metric confirmed user_id=%s metric=%s", user_context.user_id, payload.metric
    )
    return profile.model_dump(mode="json")


@app.post("/api/mcp")
async def mcp_endpoint(_: UserContext = Depends(require_user_context)) -> Mapping[str, str]:
    return {"status": "ok", "message": "MCP tool surface will be exposed here."}
