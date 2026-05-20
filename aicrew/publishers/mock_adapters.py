"""Default mock publisher adapters for every supported channel kind.

When AICREW_PROVIDER_MODE != mock you would swap these out for real HTTP-based
adapters. The interface (validate_credentials/publish) stays the same.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from ..channels.registry import CHANNEL_KINDS
from ..settings import Settings
from .base import PublicationResult, register


class _MockAdapter:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.spec = CHANNEL_KINDS[kind]

    def validate_credentials(self, creds: dict[str, str]) -> None:
        if not self.spec.credentials_fields:
            return  # drafts-mode channels need none
        required = [k for k, _ in self.spec.credentials_fields]
        missing = [k for k in required if not (creds or {}).get(k)]
        if missing:
            raise ValueError(f"{self.kind}: missing credentials: {missing}")

    def publish(self, *, post: dict[str, Any], creds: dict[str, str],
                settings: Settings) -> PublicationResult:
        self.validate_credentials(creds)
        # Deterministic fake external URL/id based on post id + kind.
        seed = f"{self.kind}:{post.get('id','')}:{int(time.time())}"
        fake_id = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:14]
        url = f"https://mock.{self.kind}/p/{fake_id}"
        return PublicationResult(
            ok=True,
            external_url=url,
            external_id=fake_id,
            raw_response={"mock": True, "kind": self.kind},
        )


for _k in CHANNEL_KINDS:
    register(_MockAdapter(_k))
