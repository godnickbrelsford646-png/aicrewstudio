"""Real-API skeletons for each channel.

These classes are intentionally not registered – the mock registry is what
runs in this offline build. To enable a real adapter, replace the corresponding
entry in publishers/mock_adapters.py with the matching class below and provide
network access + valid credentials.

Each class shows the minimal API surface required to ship that channel.
"""

from __future__ import annotations

from typing import Any

from .base import PublicationResult


class TelegramRealAdapter:
    kind = "telegram"

    def validate_credentials(self, creds: dict[str, str]) -> None:
        if not creds.get("bot_token") or not creds.get("chat_id"):
            raise ValueError("telegram: bot_token and chat_id are required")

    def publish(self, *, post: dict[str, Any], creds: dict[str, str], settings) -> PublicationResult:
        # POST https://api.telegram.org/bot{bot_token}/sendMessage
        # body: {chat_id, text, parse_mode='HTML'}
        # If image: sendPhoto with caption.
        raise NotImplementedError("wire requests/httpx and uncomment when network is available")


class VKRealAdapter:
    kind = "vk"

    def validate_credentials(self, creds: dict[str, str]) -> None:
        for k in ("access_token", "owner_id"):
            if not creds.get(k):
                raise ValueError(f"vk: {k} is required")

    def publish(self, *, post: dict[str, Any], creds: dict[str, str], settings) -> PublicationResult:
        # 1) photos.getWallUploadServer -> upload_url
        # 2) upload local image -> server, photo, hash
        # 3) photos.saveWallPhoto
        # 4) wall.post(message=..., attachments=photo<owner>_<id>)
        raise NotImplementedError


class XRealAdapter:
    kind = "x"

    def validate_credentials(self, creds: dict[str, str]) -> None:
        for k in ("consumer_key", "consumer_secret", "access_token", "access_secret"):
            if not creds.get(k):
                raise ValueError(f"x: {k} is required")

    def publish(self, *, post: dict[str, Any], creds: dict[str, str], settings) -> PublicationResult:
        # POST https://api.twitter.com/2/tweets with OAuth1.0a
        # body: {"text": ...}
        raise NotImplementedError


class YouTubeShortsRealAdapter:
    kind = "youtube_shorts"

    def validate_credentials(self, creds: dict[str, str]) -> None:
        for k in ("client_id", "client_secret", "refresh_token"):
            if not creds.get(k):
                raise ValueError(f"youtube_shorts: {k} is required")

    def publish(self, *, post: dict[str, Any], creds: dict[str, str], settings) -> PublicationResult:
        # OAuth2 refresh -> access_token
        # videos.insert (resumable upload) with snippet (title/description/tags) and status.privacyStatus
        raise NotImplementedError
