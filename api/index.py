from collections.abc import Mapping
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlencode

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
from pydantic import BaseModel

from backend.config import settings
from backend.models.athlete import (
    AthleteProfile as _AthleteProfile,
)
from backend.models.athlete import (
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
from backend.models.chat import ChatSendRequest
from backend.models.storage import PresignUploadRequest
from backend.models.training import Activity
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
from backend.services.chat import ChatService, ChatUnavailableError
from backend.services.r2 import R2Service

app = FastAPI(title="Endurance Coaching Agent")
auth_service = AuthService()
chat_service = ChatService()
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
        raise HTTPException(status_code=401, detail="Invalid bearer token") from exc


def enforce_user_access(requested_user_id: str, user_context: UserContext) -> None:
    if requested_user_id != user_context.user_id:
        raise HTTPException(
            status_code=403,
            detail="Authenticated user cannot access this resource.",
        )


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
        secure=settings.app_base_url.startswith("https://"),
    )
    return {"ok": True}


@app.post("/api/oauth/browser-token")
async def oauth_browser_token(
    coach_browser_session: str | None = Cookie(default=None),
) -> BrowserTokenResponse:
    try:
        browser_session = auth_service.get_browser_session_from_cookie(coach_browser_session)
        return auth_service.create_browser_token(browser_session)
    except OAuthLoginRequiredError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except OAuthRepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
        return RedirectResponse(denial_redirect, status_code=302)
    try:
        redirect_target = auth_service.approve_consent(
            request=request, browser_session=browser_session
        )
    except OAuthRepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
async def create_chat_message(
    payload: ChatSendRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    try:
        response = await chat_service.send_message(
            user_context.user_id,
            payload.content,
            payload.attachments,
        )
    except ChatUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RepositoryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return response.model_dump(mode="json")


@app.post("/api/chat/attachments/presign")
async def presign_chat_upload(
    payload: PresignUploadRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    presigned_upload = r2_service.create_presigned_upload(
        user_id=user_context.user_id, request=payload
    )
    return presigned_upload.model_dump(mode="json")


@app.post("/api/files/presign-upload")
async def presign_upload(
    payload: PresignUploadRequest, user_context: UserContext = Depends(require_user_context)
) -> Mapping[str, object]:
    presigned_upload = r2_service.create_presigned_upload(
        user_id=user_context.user_id, request=payload
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
    return upload_result.model_dump(mode="json")


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


class RecomputeLoadRequest(BaseModel):
    user_id: str
    since: date | None = None
    sport: str | None = None


@app.post("/api/engine/recompute-load")
async def recompute_load_endpoint(
    payload: RecomputeLoadRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    from backend.engine.training_load import recompute_load_series

    enforce_user_access(payload.user_id, user_context)

    since = payload.since or date.today()
    activities = await repo.list_activities(
        payload.user_id, sport=payload.sport, since=since, limit=500
    )

    daily_tss: dict[date, float] = {}
    for a in activities:
        daily_tss[a.activity_date] = daily_tss.get(a.activity_date, 0) + (a.tss or 0)

    prev = await repo.get_latest_load(payload.user_id, sport=payload.sport)
    initial_ctl = prev.ctl if prev else 0.0
    initial_atl = prev.atl if prev else 0.0

    snapshots = recompute_load_series(daily_tss, since, date.today(), initial_ctl, initial_atl)

    await repo.upsert_load_snapshots(payload.user_id, snapshots, sport=payload.sport)

    latest = snapshots[-1] if snapshots else {}
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
    user_id: str


@app.post("/api/engine/analyze-screenshot")
async def analyze_screenshot_endpoint(
    payload: AnalyzeScreenshotRequest,
    _: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    from backend.engine.screenshot_analyzer import analyze_screenshot

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
    return {"activity": activity.model_dump(mode="json")}


def _build_fitness_metrics(
    profile: "_AthleteProfile", thresholds: list["SportThreshold"]
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


class GetAthleteSummaryRequest(BaseModel):
    user_id: str


@app.post("/api/engine/get-athlete-summary")
async def get_athlete_summary(
    payload: GetAthleteSummaryRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    enforce_user_access(payload.user_id, user_context)

    from backend.engine.thresholds import estimate_ctl_ceiling

    try:
        profile = await repo.get_athlete_profile(payload.user_id)
    except RecordNotFoundError:
        profile = _AthleteProfile(user_id=payload.user_id, coaching_state="onboarding")

    thresholds = await repo.get_active_thresholds(payload.user_id)
    goals = await repo.list_active_goals(payload.user_id)
    latest_load = await repo.get_latest_load(payload.user_id)
    recovery = await repo.list_recovery_logs(payload.user_id, limit=7)
    schedule = await repo.get_schedule(payload.user_id)
    active_plan = await repo.get_active_plan(payload.user_id)

    age = profile.age
    ctl_ceiling = estimate_ctl_ceiling(age, profile.biological_sex or "not_specified")

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
    user_id: str
    fields: dict[str, object]


@app.post("/api/engine/update-athlete-profile")
async def update_athlete_profile(
    payload: UpdateAthleteProfileRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    enforce_user_access(payload.user_id, user_context)
    profile = await repo.update_athlete_profile_fields(payload.user_id, payload.fields)
    return profile.model_dump(mode="json")


class GetRecentActivitiesRequest(BaseModel):
    user_id: str
    sport: str | None = None
    limit: int = 20


@app.post("/api/engine/get-recent-activities")
async def get_recent_activities(
    payload: GetRecentActivitiesRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    enforce_user_access(payload.user_id, user_context)

    activities = await repo.list_activities(
        payload.user_id,
        sport=payload.sport,
        limit=min(max(payload.limit, 1), 100),
    )

    return {"activities": [activity.model_dump(mode="json") for activity in activities]}


class GeneratePlanStructureRequest(BaseModel):
    user_id: str
    goal_id: str | None = None


@app.post("/api/engine/generate-plan-structure")
async def generate_plan_structure(
    payload: GeneratePlanStructureRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    from backend.engine.periodization import build_plan_skeleton
    from backend.engine.thresholds import estimate_ctl_ceiling

    enforce_user_access(payload.user_id, user_context)

    profile = await repo.get_athlete_profile(payload.user_id)
    goals = await repo.list_active_goals(payload.user_id)
    latest_load = await repo.get_latest_load(payload.user_id)

    target_goal = None
    if payload.goal_id:
        target_goal = next((g for g in goals if g.id == payload.goal_id), None)
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

    return {
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


class ConfirmThresholdRequest(BaseModel):
    """Promote an estimated/file-derived sport threshold to user-confirmed."""

    user_id: str
    sport: str  # cycling | running | swimming | ...


@app.post("/api/engine/confirm-threshold")
async def confirm_threshold(
    payload: ConfirmThresholdRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    """Mark the active sport threshold as user-confirmed.

    Sets estimation_method=manual, confidence=high, source=user.
    """
    enforce_user_access(payload.user_id, user_context)
    client = repo._require_client()
    client.table("sport_thresholds").update(
        {"estimation_method": "manual", "confidence": "high", "source": "user"}
    ).eq("user_id", payload.user_id).eq("sport", payload.sport).is_(
        "superseded_at", "null"
    ).execute()

    thresholds = await repo.get_active_thresholds(payload.user_id)
    confirmed = next((t for t in thresholds if t.sport == payload.sport), None)
    return confirmed.model_dump(mode="json") if confirmed else {}


class ConfirmProfileMetricRequest(BaseModel):
    """Promote a profile-level estimated metric (max_hr, weight) to user-confirmed."""

    user_id: str
    metric: str  # "max_hr" | "weight"


@app.post("/api/engine/confirm-profile-metric")
async def confirm_profile_metric(
    payload: ConfirmProfileMetricRequest,
    user_context: UserContext = Depends(require_user_context),
) -> Mapping[str, object]:
    enforce_user_access(payload.user_id, user_context)
    allowed_metrics = {"max_hr", "weight"}
    if payload.metric not in allowed_metrics:
        raise HTTPException(status_code=400, detail=f"metric must be one of {allowed_metrics}")

    source_field = f"{payload.metric}_source"
    profile = await repo.update_athlete_profile_fields(payload.user_id, {source_field: "user"})
    return profile.model_dump(mode="json")


@app.post("/api/mcp")
async def mcp_endpoint(_: UserContext = Depends(require_user_context)) -> Mapping[str, str]:
    return {"status": "ok", "message": "MCP tool surface will be exposed here."}
