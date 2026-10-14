"""
app/models/webhook.py — Pydantic models for the Meta Instagram Webhook payload.

These models cover the `ig_reel` attachment type sent when a user
shares a Reel to the bot's Instagram DM inbox.

Meta Webhook docs:
  https://developers.facebook.com/docs/messenger-platform/reference/webhook-events/messages
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════════════════════════════
# Attachment sub-models
# ══════════════════════════════════════════════════════════════════════════════

class AttachmentPayload(BaseModel):
    url: str | None = None          # CDN video URL for ig_reel
    title: str | None = None        # Reel caption / title
    sticker_id: str | None = None

    model_config = {"extra": "allow"}


class Attachment(BaseModel):
    type: str                       # "ig_reel" | "image" | "audio" | "video" | …
    payload: AttachmentPayload

    model_config = {"extra": "allow"}


# ══════════════════════════════════════════════════════════════════════════════
# Message models
# ══════════════════════════════════════════════════════════════════════════════

class QuickReply(BaseModel):
    payload: str

    model_config = {"extra": "allow"}


class IncomingMessage(BaseModel):
    mid: str                        # Message ID
    text: str | None = None
    attachments: list[Attachment] | None = None
    quick_reply: QuickReply | None = None

    model_config = {"extra": "allow"}

    @property
    def reel_attachment(self) -> Attachment | None:
        """Return the first ig_reel attachment, or None."""
        if not self.attachments:
            return None
        for att in self.attachments:
            if att.type in ("ig_reel", "video"):
                return att
        return None

    @property
    def reel_video_id(self) -> str | None:
        """
        Extract a stable video ID from the CDN URL or message id.
        Meta CDN URLs contain the video ID as a query parameter (`efg` or
        the path segment before `?`).  We fall back to `mid` if parsing fails.
        """
        att = self.reel_attachment
        if att and att.payload.url:
            url = att.payload.url
            # Try to extract a unique segment from the CDN path
            try:
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(url)
                # Prefer `efg` param (base64 encoded reel ID used by Meta CDN)
                qs = parse_qs(parsed.query)
                if "efg" in qs:
                    return qs["efg"][0]
                # Otherwise take the last non-empty path segment
                segments = [s for s in parsed.path.split("/") if s]
                if segments:
                    return segments[-1]
            except Exception:
                pass
        return self.mid   # fallback: use message ID as dedup key


class Sender(BaseModel):
    id: str

    model_config = {"extra": "allow"}


class Recipient(BaseModel):
    id: str

    model_config = {"extra": "allow"}


class MessagingEvent(BaseModel):
    sender: Sender
    recipient: Recipient
    timestamp: int
    message: IncomingMessage | None = None
    postback: dict[str, Any] | None = None

    model_config = {"extra": "allow"}


# ══════════════════════════════════════════════════════════════════════════════
# Top-level webhook envelope
# ══════════════════════════════════════════════════════════════════════════════

class WebhookEntry(BaseModel):
    id: str
    time: int
    messaging: list[MessagingEvent] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class WebhookPayload(BaseModel):
    object: str                     # "instagram"
    entry: list[WebhookEntry]

    model_config = {"extra": "allow"}
