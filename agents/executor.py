"""
executor.py - Executor Agent

Responsibilities:
 - Receive the structured plan from the Planner Agent
 - Create Google Calendar events for each subtask
 - Send a daily summary email via Gmail
 - Respect the system's autonomy level (manual / assisted / autonomous)
 - Log all actions taken for the audit trail

LangGraph node name: "executor"
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from config.settings import get_settings, AutonomyLevel
from tools.calendar_tool import create_task_event
from tools.gmail_tool import send_daily_summary

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Agent Node ─────────────────────────────────────────────────────────────────

async def executor_node(state: dict[str, Any]) -> dict[str, Any]:
    """
    LangGraph node: Executor Agent.

    Reads from state:
        plan (dict)                — structured plan produced by Planner
        credentials (Credentials) — authenticated Google OAuth credentials
        autonomy_level (str)       — "manual" | "assisted" | "autonomous"
        approved_tasks (list[str]) — task IDs approved by user (for manual/assisted)
        user_email (str)           — recipient email for daily summary

    Writes to state:
        execution_results (list)   — list of action result dicts
        pending_approvals (list)   — tasks awaiting user approval (manual/assisted)
        status (str)               — "executed" | "awaiting_approval" | "error"
        error (str | None)
    """
    plan: dict[str, Any] = state.get("plan", {})
    credentials = state.get("credentials")
    autonomy: str = state.get("autonomy_level", settings.AUTONOMY_LEVEL.value)
    approved_ids: list[str] = state.get("approved_tasks", [])
    user_email: str = state.get("user_email", "")
    action_log: list[dict[str, Any]] = state.get("action_log", [])

    subtasks: list[dict[str, Any]] = plan.get("subtasks", [])
    if not subtasks:
        return {**state, "status": "error", "error": "No subtasks to execute."}

    if credentials is None:
        # In demo / test mode – simulate without calling real APIs
        logger.warning("No credentials provided to executor; running in simulation mode.")
        results = _simulate_execution(subtasks)
        return {
            **state,
            "execution_results": results,
            "action_log": action_log + results,
            "status": "executed",
            "error": None,
        }

    # ── Manual / Assisted: request approval for ALL tasks at once ─────────
    if autonomy in (AutonomyLevel.MANUAL, AutonomyLevel.ASSISTED):
        # Only ask for approval if NO tasks have been approved yet.
        # Once the user has submitted approvals, we execute only what they picked.
        if not approved_ids:
            logger.info(
                "Autonomy=%s: requesting approval for all %d task(s).", autonomy, len(subtasks)
            )
            return {
                **state,
                "pending_approvals": [_task_summary(t) for t in subtasks],
                "status": "awaiting_approval",
                "error": None,
            }
        # else: user already approved some tasks – proceed to execute below

    # ── Execute (autonomous or all tasks approved) ─────────────────────────
    execution_results: list[dict[str, Any]] = []
    base_date = _next_work_slot()

    for task in subtasks:
        # Skip tasks not approved in non-autonomous modes
        if autonomy != AutonomyLevel.AUTONOMOUS and task["id"] not in approved_ids:
            continue

        result = await _execute_task(credentials, task, base_date)
        execution_results.append(result)
        if result["success"]:
            base_date = base_date + timedelta(hours=float(task.get("estimated_hours", 1)) + 0.5)

    # ── Send daily summary email ───────────────────────────────────────────
    if user_email and credentials:
        date_str = datetime.now(timezone.utc).strftime("%A, %d %b %Y")
        try:
            await send_daily_summary(
                credentials,
                user_email,
                subtasks,
                date_str,
            )
            execution_results.append(
                {"action": "send_daily_summary", "success": True, "email": user_email}
            )
        except Exception as exc:
            logger.error("Failed to send daily summary: %s", exc)
            execution_results.append(
                {"action": "send_daily_summary", "success": False, "error": str(exc)}
            )

    logger.info(
        "Executor completed: %d actions, %d succeeded.",
        len(execution_results),
        sum(1 for r in execution_results if r.get("success")),
    )

    return {
        **state,
        "execution_results": execution_results,
        "action_log": action_log + execution_results,
        "status": "executed",
        "error": None,
    }


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _execute_task(
    credentials,
    task: dict[str, Any],
    base_date: datetime,
) -> dict[str, Any]:
    """Attempt to create a Calendar event for *task*."""
    attempt = 0
    while attempt < settings.RETRY_ATTEMPTS:
        try:
            event = await create_task_event(credentials, task, base_date)
            return {
                "task_id": task["id"],
                "action": "create_calendar_event",
                "event_id": event.get("id"),
                "event_link": event.get("htmlLink"),
                "success": True,
            }
        except Exception as exc:
            attempt += 1
            logger.warning(
                "Executor: attempt %d for task '%s' failed: %s",
                attempt,
                task["id"],
                exc,
            )

    return {
        "task_id": task["id"],
        "action": "create_calendar_event",
        "success": False,
        "error": f"Failed after {settings.RETRY_ATTEMPTS} attempts.",
    }


def _simulate_execution(subtasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return simulated execution results (used when no credentials provided)."""
    results = []
    for task in subtasks:
        results.append(
            {
                "task_id": task["id"],
                "action": "create_calendar_event (simulated)",
                "success": True,
                "event_id": f"sim_{task['id']}",
            }
        )
    return results


def _next_work_slot() -> datetime:
    """Return the next 9 AM slot (UTC) to start scheduling tasks."""
    now = datetime.now(timezone.utc)
    slot = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if now.hour >= 9:
        slot = slot + timedelta(days=1)
    return slot


def _task_summary(task: dict[str, Any]) -> dict[str, Any]:
    """Return a concise dict for pending-approval display."""
    return {
        "id": task["id"],
        "title": task["title"],
        "estimated_hours": task.get("estimated_hours"),
        "priority": task.get("priority"),
        "description": task.get("description", ""),
    }
