"""
calendar_tool.py - Google Calendar integration.

Provides async wrappers around the Google Calendar REST API for:
  - Listing today's / upcoming events
  - Creating new events
  - Updating / deleting events
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _build_service(creds: Credentials):
    """Build and return an authenticated Calendar API service object."""
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


async def list_events_today(creds: Credentials) -> list[dict[str, Any]]:
    """
    Return all Calendar events for the current calendar day (UTC).
    """
    now = datetime.now(timezone.utc)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1)

    return await list_events_range(creds, start_of_day, end_of_day)


async def list_events_range(
    creds: Credentials,
    start: datetime,
    end: datetime,
    max_results: int = 50,
) -> list[dict[str, Any]]:
    """
    Return Calendar events between *start* and *end*.
    """
    try:
        service = _build_service(creds)
        body = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        events = body.get("items", [])
        logger.info("Fetched %d events from Calendar (%s → %s).", len(events), start.date(), end.date())
        return events
    except HttpError as exc:
        logger.error("Calendar API error: %s", exc)
        raise


async def create_event(
    creds: Credentials,
    summary: str,
    description: str,
    start_dt: datetime,
    end_dt: datetime,
    timezone_str: str = "UTC",
    color_id: str | None = None,
) -> dict[str, Any]:
    """
    Create a new Calendar event and return the created event resource.

    Args:
        creds:        Authenticated Google credentials.
        summary:      Event title.
        description:  Event description / body.
        start_dt:     Start datetime (timezone-aware).
        end_dt:       End datetime (timezone-aware).
        timezone_str: IANA timezone name (default: "UTC").
        color_id:     Optional Calendar color ID (1-11).

    Returns:
        The created event dict from the Calendar API.
    """
    try:
        service = _build_service(creds)
        event_body: dict[str, Any] = {
            "summary": summary,
            "description": description,
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": timezone_str,
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": timezone_str,
            },
        }
        if color_id:
            event_body["colorId"] = color_id

        created = service.events().insert(calendarId="primary", body=event_body).execute()
        logger.info("Created Calendar event: %s  id=%s", summary, created.get("id"))
        return created
    except HttpError as exc:
        logger.error("Failed to create Calendar event '%s': %s", summary, exc)
        raise


async def update_event(
    creds: Credentials,
    event_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """
    Patch an existing Calendar event with *updates* and return the updated resource.
    """
    try:
        service = _build_service(creds)
        updated = (
            service.events()
            .patch(calendarId="primary", eventId=event_id, body=updates)
            .execute()
        )
        logger.info("Updated Calendar event id=%s.", event_id)
        return updated
    except HttpError as exc:
        logger.error("Failed to update Calendar event id=%s: %s", event_id, exc)
        raise


async def delete_event(creds: Credentials, event_id: str) -> None:
    """Delete a Calendar event by ID."""
    try:
        service = _build_service(creds)
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        logger.info("Deleted Calendar event id=%s.", event_id)
    except HttpError as exc:
        logger.error("Failed to delete Calendar event id=%s: %s", event_id, exc)
        raise


async def create_task_event(
    creds: Credentials,
    task: dict[str, Any],
    base_date: datetime | None = None,
) -> dict[str, Any]:
    """
    Convenience wrapper: create a Calendar event from a planner subtask dict.

    Expects the task dict to have at minimum:
        - title: str
        - description: str
        - estimated_hours: float
        - scheduled_start: str  (ISO 8601, optional – defaults to base_date)
    """
    start = (
        datetime.fromisoformat(task["scheduled_start"])
        if task.get("scheduled_start")
        else (base_date or datetime.now(timezone.utc))
    )
    end = start + timedelta(hours=float(task.get("estimated_hours", 1)))

    return await create_event(
        creds=creds,
        summary=f"[AI Agent] {task['title']}",
        description=task.get("description", ""),
        start_dt=start,
        end_dt=end,
        color_id="9",  # Blueberry
    )
