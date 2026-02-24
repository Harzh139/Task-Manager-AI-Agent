"""
gmail_tool.py - Gmail integration for the Multi-Agent Productivity System.

Provides async wrappers around the Gmail REST API for:
  - Sending plain-text and HTML emails
  - Reading recent unread messages
  - Sending daily summaries and weekly review emails

All functions accept an authenticated google.oauth2.credentials.Credentials object
(obtained via the OAuth flow in main.py) rather than managing auth internally.
"""

from __future__ import annotations

import base64
import email as emaillib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _build_service(creds: Credentials):
    """Build and return an authenticated Gmail API service object."""
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _encode_message(msg: MIMEMultipart | MIMEText) -> dict[str, str]:
    """Encode a MIME message to the base64url format required by the Gmail API."""
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return {"raw": raw}


async def send_email(
    creds: Credentials,
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> dict[str, Any]:
    """
    Send an email via Gmail API.

    Args:
        creds:      Authenticated Google credentials.
        to:         Recipient email address.
        subject:    Email subject line.
        body_text:  Plain-text body (always required).
        body_html:  Optional HTML body (if provided, sends multipart/alternative).

    Returns:
        The Gmail API send response dict (contains 'id', 'threadId', 'labelIds').
    """
    try:
        if body_html:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body_text, "plain"))
            msg.attach(MIMEText(body_html, "html"))
        else:
            msg = MIMEText(body_text, "plain")

        msg["To"] = to
        msg["Subject"] = subject

        service = _build_service(creds)
        result = (
            service.users()
            .messages()
            .send(userId="me", body=_encode_message(msg))
            .execute()
        )
        logger.info("Email sent to %s  subject='%s'  id=%s", to, subject, result.get("id"))
        return result
    except HttpError as exc:
        logger.error("Failed to send email to %s: %s", to, exc)
        raise


async def send_daily_summary(
    creds: Credentials,
    recipient: str,
    tasks: list[dict[str, Any]],
    date_str: str,
) -> dict[str, Any]:
    """
    Compose and send a formatted daily summary email.

    Args:
        creds:      Authenticated Google credentials.
        recipient:  Recipient email address.
        tasks:      List of task dicts with keys: title, status, estimated_hours.
        date_str:   Human-readable date string (e.g. "Monday, 24 Feb 2025").
    """
    subject = f"📋 Your AI Productivity Plan — {date_str}"

    lines_text = [f"Daily Plan — {date_str}", "=" * 40]
    rows_html = ""
    for i, t in enumerate(tasks, 1):
        status_icon = "✅" if t.get("status") == "done" else "🔲"
        lines_text.append(
            f"{i}. {status_icon} {t['title']}  ({t.get('estimated_hours', '?')}h)"
        )
        bg = "#e8f5e9" if t.get("status") == "done" else "#fff"
        rows_html += (
            f"<tr style='background:{bg}'>"
            f"<td style='padding:8px'>{i}</td>"
            f"<td style='padding:8px'>{status_icon} {t['title']}</td>"
            f"<td style='padding:8px;text-align:center'>{t.get('estimated_hours', '?')}h</td>"
            f"<td style='padding:8px;text-align:center'>{t.get('status','pending').capitalize()}</td>"
            f"</tr>"
        )

    body_text = "\n".join(lines_text)
    body_html = f"""
    <html><body>
    <h2 style='color:#1a73e8'>📋 Daily Productivity Plan</h2>
    <p style='color:#555'>{date_str}</p>
    <table border='1' cellspacing='0' cellpadding='0'
           style='border-collapse:collapse;font-family:sans-serif;font-size:14px'>
      <thead style='background:#1a73e8;color:#fff'>
        <tr>
          <th style='padding:8px'>#</th>
          <th style='padding:8px'>Task</th>
          <th style='padding:8px'>Est. Hours</th>
          <th style='padding:8px'>Status</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
    <p style='margin-top:16px;color:#888;font-size:12px'>
      Sent by your AI Productivity Agent
    </p>
    </body></html>
    """

    return await send_email(creds, recipient, subject, body_text, body_html)


async def send_weekly_review(
    creds: Credentials,
    recipient: str,
    review: dict[str, Any],
    week_label: str,
) -> dict[str, Any]:
    """
    Send a formatted weekly review / reflection email.

    Args:
        creds:       Authenticated Google credentials.
        recipient:   Recipient email address.
        review:      Dict produced by the Reflection Agent.
        week_label:  Week label string (e.g. "Week of Feb 17, 2025").
    """
    subject = f"🔍 Weekly Productivity Review — {week_label}"

    body_text = (
        f"Weekly Review — {week_label}\n"
        f"Completed: {review.get('completed_tasks', 0)} tasks\n"
        f"Productivity Score: {review.get('productivity_score', 'N/A')}/10\n\n"
        f"Insights:\n{review.get('insights', 'No insights available.')}\n\n"
        f"Recommendations:\n{review.get('recommendations', 'No recommendations.')}"
    )

    body_html = f"""
    <html><body>
    <h2 style='color:#1a73e8'>🔍 Weekly Productivity Review</h2>
    <h3 style='color:#555'>{week_label}</h3>
    <table style='font-family:sans-serif;font-size:14px'>
      <tr><td><b>Tasks Completed:</b></td><td>{review.get('completed_tasks', 0)}</td></tr>
      <tr><td><b>Productivity Score:</b></td><td>{review.get('productivity_score', 'N/A')}/10</td></tr>
    </table>
    <h4>💡 Insights</h4>
    <p>{str(review.get('insights', '')).replace(chr(10), '<br>')}</p>
    <h4>🎯 Recommendations</h4>
    <p>{str(review.get('recommendations', '')).replace(chr(10), '<br>')}</p>
    <p style='margin-top:16px;color:#888;font-size:12px'>
      Sent by your AI Productivity Agent
    </p>
    </body></html>
    """

    return await send_email(creds, recipient, subject, body_text, body_html)


async def get_recent_emails(
    creds: Credentials,
    max_results: int = 10,
    query: str = "is:unread",
) -> list[dict[str, Any]]:
    """
    Fetch recent Gmail messages matching *query*.

    Returns a list of message dicts with keys: id, subject, from, snippet, date.
    """
    try:
        service = _build_service(creds)
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        message_stubs = resp.get("messages", [])

        messages: list[dict[str, Any]] = []
        for stub in message_stubs:
            msg_raw = (
                service.users()
                .messages()
                .get(userId="me", id=stub["id"], format="metadata",
                     metadataHeaders=["Subject", "From", "Date"])
                .execute()
            )
            headers = {h["name"]: h["value"] for h in msg_raw.get("payload", {}).get("headers", [])}
            messages.append(
                {
                    "id": stub["id"],
                    "subject": headers.get("Subject", "(no subject)"),
                    "from": headers.get("From", ""),
                    "date": headers.get("Date", ""),
                    "snippet": msg_raw.get("snippet", ""),
                }
            )

        logger.info("Retrieved %d emails (query='%s').", len(messages), query)
        return messages
    except HttpError as exc:
        logger.error("Failed to fetch emails: %s", exc)
        raise
