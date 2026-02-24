"""
reflection.py - Reflection Agent

Responsibilities:
 - Weekly review of completed tasks, productivity score, and behaviour patterns
 - Generate actionable improvement suggestions
 - Persist insights to FAISS for long-term memory
 - Optionally send a weekly review email via Gmail

LangGraph node name: "reflection"
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from groq import AsyncGroq

from config.settings import get_settings
from memory.vector_store import get_vector_store
from tools.gmail_tool import send_weekly_review

logger = logging.getLogger(__name__)
settings = get_settings()


SYSTEM_PROMPT = """
You are a world-class AI productivity coach performing a weekly reflection for
a user. You have access to their completed and incomplete tasks, productivity
scores, and brief history. Your job is to:

1. Summarise accomplishments for the week.
2. Identify recurring patterns (both positive and negative).
3. Provide 3-5 specific, actionable recommendations for next week.
4. Suggest one habit or workflow change.
5. Score the week's productivity from 1 (very poor) to 10 (exceptional).

Return ONLY valid JSON matching this schema:
{
  "week_label": "<e.g. Week of Feb 17, 2025>",
  "completed_tasks": <int>,
  "incomplete_tasks": <int>,
  "productivity_score": <int 1-10>,
  "accomplishments": "<paragraph>",
  "patterns": ["<pattern 1>", ...],
  "insights": "<detailed multi-line insights>",
  "recommendations": ["<recommendation 1>", ...],
  "habit_suggestion": "<one specific habit or workflow change>",
  "next_week_focus": "<one key priority for next week>"
}
""".strip()


async def reflection_node(state: dict[str, Any]) -> dict[str, Any]:
    """
    LangGraph node: Reflection Agent.

    Reads from state:
        plan (dict)               — completed plan
        monitor_report (dict)     — latest monitor assessment
        action_log (list)         — full history of executed actions
        productivity_score (int)  — from monitor
        credentials (Credentials) — Google credentials (optional)
        user_email (str)          — recipient for weekly review email

    Writes to state:
        reflection (dict)         — full weekly reflection object
        status (str)              — "reflected" | "error"
        error (str | None)
    """
    plan: dict[str, Any] = state.get("plan", {})
    monitor_report: dict[str, Any] = state.get("monitor_report", {})
    action_log: list[dict] = state.get("action_log", [])
    productivity_score: int = state.get("productivity_score", 5)
    credentials = state.get("credentials")
    user_email: str = state.get("user_email", "")

    store = get_vector_store()
    now = datetime.now(timezone.utc)
    week_start = (now - timedelta(days=now.weekday())).strftime("%b %d, %Y")
    week_label = f"Week of {week_start}"

    subtasks = plan.get("subtasks", [])
    completed_ids: list[str] = monitor_report.get("completed_tasks", [])

    # Retrieve memory context
    memory_ctx = await store.get_relevant_context(f"weekly review {week_label} {plan.get('goal','')}")

    user_message = (
        f"Week: {week_label}\n"
        f"Goal: {plan.get('goal', 'N/A')}\n"
        f"Total subtasks: {len(subtasks)}\n"
        f"Completed: {len(completed_ids)}\n"
        f"Incomplete: {len(subtasks) - len(completed_ids)}\n"
        f"Productivity score (from monitor): {productivity_score}/10\n"
        f"Monitor health: {monitor_report.get('health', 'unknown')}\n"
        f"Monitor report: {monitor_report.get('status_report', 'N/A')}\n\n"
        f"Relevant memory / past reflections:\n{memory_ctx}\n\n"
        f"Action log summary (last 20 entries):\n"
        + json.dumps(action_log[-20:], default=str, indent=2)
    )

    client = AsyncGroq(api_key=settings.GROQ_API_KEY)
    attempt = 0
    last_error = ""

    while attempt < settings.RETRY_ATTEMPTS:
        try:
            response = await client.chat.completions.create(
                model=settings.GROQ_MODEL,
                temperature=0.4,
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
            reflection: dict[str, Any] = json.loads(raw)
            reflection["week_label"] = week_label  # ensure field present

            # Persist reflection to FAISS
            reflection_text = (
                f"Week {week_label}: score={reflection.get('productivity_score')}, "
                f"insights={reflection.get('insights','')[:300]}"
            )
            await store.store_reflection(reflection_text, week_label)

            logger.info(
                "Reflection agent completed: score=%s  week=%s",
                reflection.get("productivity_score"),
                week_label,
            )

            # Send weekly review email if configured
            if user_email and credentials:
                try:
                    await send_weekly_review(credentials, user_email, reflection, week_label)
                    logger.info("Weekly review email sent to %s.", user_email)
                except Exception as exc:
                    logger.warning("Could not send weekly review email: %s", exc)

            return {
                **state,
                "reflection": reflection,
                "status": "reflected",
                "error": None,
            }

        except json.JSONDecodeError as exc:
            last_error = f"JSON parse error: {exc}"
            logger.warning("Reflection attempt %d JSON error: %s", attempt + 1, exc)
        except Exception as exc:
            last_error = str(exc)
            logger.warning("Reflection attempt %d error: %s", attempt + 1, exc)

        attempt += 1

    return {
        **state,
        "status": "error",
        "error": f"Reflection failed after {settings.RETRY_ATTEMPTS} attempts: {last_error}",
    }
