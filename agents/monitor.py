"""
monitor.py - Monitor Agent

Responsibilities:
 - Inspect execution results and Calendar events
 - Detect overdue / at-risk tasks
 - Trigger a replan when tasks fall behind schedule
 - Update task statuses in the shared state

LangGraph node name: "monitor"
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from groq import AsyncGroq

from config.settings import get_settings
from tools.calendar_tool import list_events_today, list_events_range

logger = logging.getLogger(__name__)
settings = get_settings()


SYSTEM_PROMPT = """
You are an AI productivity monitor. You receive a plan and the current status of
its tasks (fetched from Google Calendar / execution results). Your job is to:

1. Identify tasks that are overdue or at risk of missing their deadline.
2. Rate overall plan health: "on_track" | "at_risk" | "off_track".
3. Decide whether a full replan is needed.
4. Provide a brief human-readable status report.

Return ONLY valid JSON with this schema:
{
  "health": "on_track|at_risk|off_track",
  "needs_replan": true|false,
  "overdue_tasks": ["task_id", ...],
  "at_risk_tasks": ["task_id", ...],
  "completed_tasks": ["task_id", ...],
  "productivity_score": <int 1-10>,
  "status_report": "<2-3 sentence summary>",
  "recommended_actions": ["<action>", ...]
}
""".strip()


async def monitor_node(state: dict[str, Any]) -> dict[str, Any]:
    """
    LangGraph node: Monitor Agent.

    Reads from state:
        plan (dict)                — current plan
        execution_results (list)   — results from executor
        credentials (Credentials)  — Google credentials (optional)

    Writes to state:
        monitor_report (dict)      — health assessment
        needs_replan (bool)        — whether the planner should re-run
        status (str)               — "monitored" | "replan_needed" | "error"
        error (str | None)
    """
    plan: dict[str, Any] = state.get("plan", {})
    execution_results: list[dict] = state.get("execution_results", [])
    credentials = state.get("credentials")

    if not plan:
        return {**state, "status": "error", "error": "No plan found in state for monitor."}

    current_time = datetime.now(timezone.utc).isoformat()
    subtasks = plan.get("subtasks", [])
    deadline = plan.get("deadline", "Not set")

    # ── Gather calendar reality ────────────────────────────────────────────
    calendar_events: list[dict] = []
    if credentials:
        try:
            calendar_events = await list_events_today(credentials)
        except Exception as exc:
            logger.warning("Monitor: could not fetch calendar events: %s", exc)

    # Map execution results for quick lookup
    exec_map = {r.get("task_id"): r for r in execution_results if r.get("task_id")}

    task_status_lines = []
    for task in subtasks:
        exec_res = exec_map.get(task["id"], {})
        cal_event_id = exec_res.get("event_id", "—")
        # Check if a calendar event with this ID exists in today's fetched events
        on_calendar = any(e.get("id") == cal_event_id for e in calendar_events)
        task_status_lines.append(
            f"- {task['id']} | '{task['title']}' | priority={task.get('priority')} "
            f"| est={task.get('estimated_hours')}h | on_calendar={on_calendar} "
            f"| exec_success={exec_res.get('success', 'unknown')}"
        )

    user_message = (
        f"Current UTC time: {current_time}\n"
        f"Goal: {plan.get('goal', 'N/A')}\n"
        f"Deadline: {deadline}\n"
        f"Total estimated hours: {plan.get('total_estimated_hours', '?')}\n\n"
        f"Task status snapshot:\n" + "\n".join(task_status_lines)
    )

    client = AsyncGroq(api_key=settings.GROQ_API_KEY)
    attempt = 0
    last_error = ""

    while attempt < settings.RETRY_ATTEMPTS:
        try:
            response = await client.chat.completions.create(
                model=settings.GROQ_MODEL,
                temperature=0.1,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
            )
            raw = response.choices[0].message.content.strip()
            # Strip markdown fences if model wraps output
            if raw.startswith("```"):
                lines = raw.splitlines()
                lines = lines[1:]
                if lines and lines[-1].strip().startswith("```"):
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()
            report = json.loads(raw)

            needs_replan: bool = report.get("needs_replan", False)

            logger.info(
                "Monitor: health=%s  needs_replan=%s  score=%s",
                report.get("health"),
                needs_replan,
                report.get("productivity_score"),
            )

            return {
                **state,
                "monitor_report": report,
                "needs_replan": needs_replan,
                "productivity_score": report.get("productivity_score", 5),
                "status": "replan_needed" if needs_replan else "monitored",
                "error": None,
            }

        except json.JSONDecodeError as exc:
            last_error = f"JSON parse error: {exc}"
            logger.warning("Monitor attempt %d JSON error: %s", attempt + 1, exc)
        except Exception as exc:
            last_error = str(exc)
            logger.warning("Monitor attempt %d error: %s", attempt + 1, exc)

        attempt += 1

    return {
        **state,
        "status": "error",
        "error": f"Monitor failed after {settings.RETRY_ATTEMPTS} attempts: {last_error}",
    }
