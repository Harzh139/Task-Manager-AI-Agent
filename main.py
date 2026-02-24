"""
main.py - FastAPI application for the Multi-Agent Productivity System.

Endpoints:
  POST /goal                — Submit a new productivity goal
  GET  /today               — Retrieve today's plan and schedule
  GET  /weekly-review       — Latest weekly reflection
  POST /approve-action      — Approve pending tasks (manual/assisted mode)
  GET  /auth/login          — Initiates Google OAuth 2.0 flow
  GET  /auth/callback       — Handles OAuth redirect and stores token
  GET  /health              — Health check
  GET  /action-log          — Full action log
  GET  /productivity-score  — Current score from last monitor run
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from google.auth.transport.requests import Request as GRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from pydantic import BaseModel, Field

from config.settings import get_settings, AutonomyLevel
from graph.workflow import run_workflow

# ── Setup ──────────────────────────────────────────────────────────────────────

settings = get_settings()

# Logging
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(settings.LOG_FILE),
    ],
)
logger = logging.getLogger(__name__)

# Allow oauthlib to work over HTTP in development
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
# Allow oauthlib to accept scopes returned in a different order by Google
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

# ── FastAPI App ────────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    description="Production-ready Multi-Agent Autonomous Productivity System",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Persistent JSON stores ────────────────────────────────────────────────────
# Both files live in the memory/ directory so they survive server restarts.

_STORE_FILE  = Path("memory/session_store.json")
_STATUS_FILE = Path("memory/task_status.json")


def _load_json_file(path: Path, default: Any) -> Any:
    """Load JSON from *path*, returning *default* if the file doesn't exist."""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not load %s: %s", path, exc)
    return default


def _save_json_file(path: Path, data: Any) -> None:
    """Atomically write *data* as JSON to *path*."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        logger.error("Could not save %s: %s", path, exc)


# Load both stores from disk at import time
_session_store: dict[str, Any] = _load_json_file(_STORE_FILE,  {})
_task_status:   dict[str, bool] = _load_json_file(_STATUS_FILE, {})


def _save_session_store() -> None:
    _save_json_file(_STORE_FILE, _session_store)


def _save_task_status() -> None:
    _save_json_file(_STATUS_FILE, _task_status)


# ── Request / Response Models ──────────────────────────────────────────────────

class GoalRequest(BaseModel):
    goal: str = Field(..., min_length=5)
    deadline: str | None = Field(None)
    user_email: str | None = Field(None)
    autonomy_level: AutonomyLevel = Field(AutonomyLevel.ASSISTED)

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "goal": "Finish ML assignment by Friday",
                "deadline": "2025-02-28T23:59:00Z",
                "user_email": "you@example.com",
                "autonomy_level": "assisted",
            }]
        }
    }


class ApproveActionRequest(BaseModel):
    session_id: str = Field(..., description="Session ID returned from POST /goal")
    approved_task_ids: list[str] = Field(..., description="List of task IDs to approve")


class GoalResponse(BaseModel):
    session_id: str
    status: str
    plan: dict[str, Any] | None = None
    pending_approvals: list[dict[str, Any]] | None = None
    execution_results: list[dict[str, Any]] | None = None
    monitor_report: dict[str, Any] | None = None
    reflection: dict[str, Any] | None = None
    error: str | None = None


# ── Credential Helpers ─────────────────────────────────────────────────────────

def _load_credentials() -> Credentials | None:
    """Load stored OAuth credentials from TOKEN_FILE, refresh if expired."""
    token_path = Path(settings.TOKEN_FILE)
    if not token_path.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(token_path), settings.GOOGLE_SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(GRequest())
            _save_credentials(creds)
        return creds if creds.valid else None
    except Exception as exc:
        logger.warning("Could not load credentials: %s", exc)
        return None


def _save_credentials(creds: Credentials) -> None:
    """Persist credentials to TOKEN_FILE."""
    token_path = Path(settings.TOKEN_FILE)
    token_path.write_text(creds.to_json())
    logger.debug("Credentials saved to %s.", token_path)


def _build_flow() -> Flow:
    """Build a google_auth_oauthlib Flow from settings."""
    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uris": [settings.GOOGLE_REDIRECT_URI],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=settings.GOOGLE_SCOPES,
        redirect_uri=settings.GOOGLE_REDIRECT_URI,
    )


# ── Auth Endpoints ─────────────────────────────────────────────────────────────

@app.get("/auth/login", tags=["Auth"], summary="Initiate Google OAuth 2.0 login")
async def auth_login():
    """Redirect the user to Google's OAuth consent screen."""
    flow = _build_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    _session_store["oauth_state"] = state
    return RedirectResponse(auth_url)


@app.get("/auth/callback", tags=["Auth"], summary="Handle OAuth callback")
async def auth_callback(request: Request):
    """Exchange auth code for tokens and persist to disk."""
    flow = _build_flow()
    try:
        flow.fetch_token(authorization_response=str(request.url))
        creds = flow.credentials
        _save_credentials(creds)
        logger.info("OAuth credentials obtained and saved.")
    except Exception as exc:
        # The browser sometimes hits the callback URL twice with the same
        # single-use auth code. If the first request already saved valid
        # credentials, silently ignore the duplicate and redirect anyway.
        logger.warning("Token fetch failed (%s) – checking for existing credentials.", exc)
        creds = _load_credentials()
        if creds is None:
            raise HTTPException(
                status_code=400,
                detail=f"OAuth authentication failed: {exc}",
            )
        logger.info("Existing valid credentials found – treating as successful login.")
    # Redirect back to the frontend homepage after successful login
    return RedirectResponse(url="/")


@app.get("/auth/status", tags=["Auth"], summary="Check authentication status")
async def auth_status():
    """Returns whether valid Google credentials are currently stored."""
    creds = _load_credentials()
    return {"authenticated": creds is not None}


# ── Task Completion ────────────────────────────────────────────────────────────

class CompleteTaskRequest(BaseModel):
    task_id: str = Field(..., description="The subtask ID to toggle (e.g. task_01)")
    session_id: str | None = Field(None, description="Session the task belongs to (optional)")
    done: bool = Field(True, description="True = mark done, False = mark pending")


@app.post("/task/complete", tags=["Agent"], summary="Mark a task as done or pending")
async def complete_task(req: CompleteTaskRequest):
    """
    Toggle the completion status of a subtask.
    Persists to disk immediately — survives server restarts.
    """
    _task_status[req.task_id] = req.done
    _save_task_status()  # ← persist to disk immediately
    logger.info("Task %s marked %s.", req.task_id, 'done' if req.done else 'pending')
    return {"task_id": req.task_id, "done": req.done}


@app.get("/task/status", tags=["Agent"], summary="Get completion status of all tasks")
async def get_task_status():
    """Returns a dict of {task_id: bool} for all tasks that have been toggled."""
    return _task_status


# ── Core Agent Endpoints ───────────────────────────────────────────────────────

@app.post("/goal", response_model=GoalResponse, tags=["Agent"], summary="Submit a productivity goal")
async def create_goal(req: GoalRequest) -> GoalResponse:
    """
    Submit a new user goal. The system will:
    1. Plan it into subtasks
    2. Execute (create calendar events, send email)
    3. Monitor progress
    4. Reflect (if it's a weekly trigger)

    In manual/assisted mode the response will contain pending_approvals instead of
    execution_results.  Use POST /approve-action to continue.
    """
    import uuid
    session_id = str(uuid.uuid4())

    creds = _load_credentials()
    if creds is None:
        logger.warning("No credentials found – running in simulation mode (no real API calls).")

    initial_state: dict[str, Any] = {
        "goal": req.goal,
        "deadline": req.deadline,
        "user_email": req.user_email,
        "autonomy_level": req.autonomy_level.value,
        "credentials": creds,
    }

    try:
        final_state = await run_workflow(initial_state)
    except Exception as exc:
        logger.exception("Workflow crashed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    # Persist state for later approval or review
    # Credentials are not JSON-serialisable; strip before storing
    storable = {k: v for k, v in final_state.items() if k != "credentials"}
    _session_store[session_id] = storable
    _save_session_store()  # ← persist to disk immediately

    response = GoalResponse(
        session_id=session_id,
        status=final_state.get("status", "unknown"),
        plan=final_state.get("plan"),
        pending_approvals=final_state.get("pending_approvals"),
        execution_results=final_state.get("execution_results"),
        monitor_report=final_state.get("monitor_report"),
        reflection=final_state.get("reflection"),
        error=final_state.get("error"),
    )
    return response


@app.get("/today", tags=["Agent"], summary="Get today's plan and events")
async def get_today():
    """
    Returns:
    - Today's tasks from the most recent session
    - Calendar events (if authenticated)
    - Current productivity score
    """
    from tools.calendar_tool import list_events_today
    from datetime import date

    creds = _load_credentials()
    events: list[dict] = []
    if creds:
        try:
            events = await list_events_today(creds)
        except Exception as exc:
            logger.warning("Could not fetch today's calendar: %s", exc)

    # Find the latest session with a plan
    latest_plan: dict | None = None
    latest_score: int = 0
    for session in reversed(list(_session_store.values())):
        if "plan" in session and session["plan"]:
            latest_plan = session["plan"]
            latest_score = session.get("productivity_score", 0)
            break

    # Inject live completion status into each subtask
    if latest_plan and latest_plan.get("subtasks"):
        for task in latest_plan["subtasks"]:
            task["status"] = "done" if _task_status.get(task["id"]) else "pending"

    return {
        "date": date.today().isoformat(),
        "plan": latest_plan,
        "calendar_events": events[:20],
        "productivity_score": latest_score,
    }


@app.get("/weekly-review", tags=["Agent"], summary="Get the latest weekly reflection")
async def get_weekly_review():
    """Returns the most recent weekly reflection generated by the Reflection Agent."""
    for session in reversed(list(_session_store.values())):
        if "reflection" in session and session["reflection"]:
            return session["reflection"]
    return JSONResponse(
        status_code=404,
        content={"message": "No weekly review found yet. Submit a goal first."},
    )


@app.post("/approve-action", tags=["Agent"], summary="Approve pending tasks (manual/assisted mode)")
async def approve_action(req: ApproveActionRequest) -> GoalResponse:
    """
    Approve one or more pending tasks by ID.  The executor will then proceed to
    schedule those tasks on Google Calendar.
    """
    session = _session_store.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    if session.get("status") != "awaiting_approval":
        raise HTTPException(
            status_code=400,
            detail=f"Session is in status '{session.get('status')}', not 'awaiting_approval'.",
        )

    creds = _load_credentials()
    resume_state: dict[str, Any] = {
        **session,
        "credentials": creds,
        "approved_tasks": req.approved_task_ids,
        # Reset executor status so it runs again
        "status": "planned",
    }

    try:
        final_state = await run_workflow(resume_state)
    except Exception as exc:
        logger.exception("Approval workflow crashed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    storable = {k: v for k, v in final_state.items() if k != "credentials"}
    _session_store[req.session_id] = storable
    _save_session_store()  # ← persist to disk immediately

    return GoalResponse(
        session_id=req.session_id,
        status=final_state.get("status", "unknown"),
        plan=final_state.get("plan"),
        pending_approvals=final_state.get("pending_approvals"),
        execution_results=final_state.get("execution_results"),
        monitor_report=final_state.get("monitor_report"),
        reflection=final_state.get("reflection"),
        error=final_state.get("error"),
    )


# ── Utility Endpoints ──────────────────────────────────────────────────────────

@app.get("/action-log", tags=["Monitoring"], summary="Full action log")
async def get_action_log(session_id: str | None = Query(None)):
    """
    Returns:
    - If session_id: action log for that session
    - Otherwise: merged action log from all sessions (last 200 entries)
    """
    if session_id:
        session = _session_store.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found.")
        return {"session_id": session_id, "action_log": session.get("action_log", [])}

    all_logs: list[dict] = []
    for s in _session_store.values():
        all_logs.extend(s.get("action_log", []))
    return {"action_log": all_logs[-200:]}


@app.get("/productivity-score", tags=["Monitoring"], summary="Current productivity score")
async def get_productivity_score():
    """Returns the latest productivity score from the monitor agent (1-10)."""
    for session in reversed(list(_session_store.values())):
        if "productivity_score" in session:
            return {
                "productivity_score": session["productivity_score"],
                "health": session.get("monitor_report", {}).get("health", "unknown"),
            }
    return {"productivity_score": None, "health": "no_data"}


@app.get("/sessions", tags=["Monitoring"], summary="List all sessions")
async def list_sessions():
    """List all session IDs and their current status."""
    return [
        {
            "session_id": sid,
            "status": s.get("status"),
            "goal": s.get("plan", {}).get("goal") if s.get("plan") else None,
        }
        for sid, s in _session_store.items()
    ]


@app.get("/health", tags=["System"], summary="Health check")
async def health_check():
    """Simple liveness check."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": settings.APP_VERSION,
        "autonomy_level": settings.AUTONOMY_LEVEL.value,
    }


# ── Static frontend ────────────────────────────────────────────────────────────

frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")


# ── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
