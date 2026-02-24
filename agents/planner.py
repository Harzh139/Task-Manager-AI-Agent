"""
planner.py - Planner Agent

Responsibilities:
 - Accept a user's natural-language goal
 - Break it into ordered, time-estimated subtasks
 - Return a structured JSON plan
 - Retrieve relevant past plans / preferences from FAISS and inject as context

LangGraph node name: "planner"
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from groq import AsyncGroq

from config.settings import get_settings
from memory.vector_store import get_vector_store

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Prompt ─────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are a world-class AI productivity planner. Your job is to break down a user's
high-level goal into actionable, time-boxed subtasks that can be scheduled on a
calendar and executed by an AI agent.

Rules:
1. Return ONLY valid JSON — no prose, no markdown fences.
2. Each subtask must have a realistic estimated_hours (float).
3. Set priority to "high", "medium", or "low".
4. Include a brief action_type: "deep_work" | "meeting" | "review" | "admin" | "research".
5. Subtasks should be ordered logically (prerequisites first).
6. Use ISO 8601 for any dates (YYYY-MM-DDTHH:MM:SSZ).
7. Maximum {max_subtasks} subtasks.

Output schema:
{{
  "goal": "<original goal>",
  "deadline": "<ISO 8601 deadline or null>",
  "total_estimated_hours": <float>,
  "subtasks": [
    {{
      "id": "task_01",
      "title": "<short title>",
      "description": "<what to do and why>",
      "estimated_hours": <float>,
      "priority": "high|medium|low",
      "action_type": "deep_work|meeting|review|admin|research",
      "depends_on": ["task_id", ...],
      "scheduled_start": "<ISO 8601 or null>"
    }}
  ],
  "notes": "<any planner notes>"
}}
""".strip()


# ── Helper ─────────────────────────────────────────────────────────────────────

def _parse_plan(raw: str) -> dict[str, Any]:
    """Parse the LLM's JSON response, stripping any accidental markdown fences."""
    raw = raw.strip()
    # Strip ```json ... ``` or ``` ... ``` fences
    if raw.startswith("```"):
        lines = raw.splitlines()
        # Drop the opening fence line
        lines = lines[1:]
        # Drop the closing fence line if present
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return json.loads(raw)


# ── Agent Node ─────────────────────────────────────────────────────────────────

async def planner_node(state: dict[str, Any]) -> dict[str, Any]:
    """
    LangGraph node: Planner Agent.

    Reads from state:
        goal (str)            — user's natural-language goal
        deadline (str | None) — optional ISO 8601 deadline

    Writes to state:
        plan (dict)           — structured plan with subtasks
        status (str)          — "planned" | "error"
        error (str | None)    — error message if something went wrong
    """
    goal: str = state.get("goal", "")
    deadline: str | None = state.get("deadline")
    current_time: str = datetime.now(timezone.utc).isoformat()

    if not goal:
        return {**state, "status": "error", "error": "No goal provided to planner."}

    client = AsyncGroq(api_key=settings.GROQ_API_KEY)
    store = get_vector_store()

    # Retrieve past context from FAISS
    memory_ctx = await store.get_relevant_context(goal)

    user_prompt = (
        f"Current UTC time: {current_time}\n"
        f"User goal: {goal}\n"
        f"Deadline: {deadline or 'Not specified'}\n\n"
        f"Relevant past context from memory:\n{memory_ctx}\n\n"
        "Create a detailed plan following the schema above."
    )

    attempt = 0
    last_error: str = ""
    while attempt < settings.RETRY_ATTEMPTS:
        try:
            response = await client.chat.completions.create(
                model=settings.GROQ_MODEL,
                temperature=settings.GROQ_TEMPERATURE,
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT.format(max_subtasks=settings.MAX_SUBTASKS),
                    },
                    {"role": "user", "content": user_prompt},
                ],
            )
            raw = response.choices[0].message.content
            plan = _parse_plan(raw)

            # Persist plan to FAISS for future context
            await store.store_plan(goal, plan)

            logger.info(
                "Planner created %d subtasks for goal='%s'",
                len(plan.get("subtasks", [])),
                goal,
            )
            return {
                **state,
                "plan": plan,
                "status": "planned",
                "error": None,
            }

        except json.JSONDecodeError as exc:
            last_error = f"JSON parse error: {exc}"
            logger.warning("Planner attempt %d JSON error: %s", attempt + 1, exc)
        except Exception as exc:
            last_error = str(exc)
            logger.warning("Planner attempt %d error: %s", attempt + 1, exc)

        attempt += 1

    return {**state, "status": "error", "error": f"Planner failed after {settings.RETRY_ATTEMPTS} attempts: {last_error}"}
