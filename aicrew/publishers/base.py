"""Channel publisher contract + registry. Mock-first, real adapters as skeletons."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..settings import Settings


@dataclass
class PublicationResult:
    ok: bool
    external_url: str | None = None
    external_id: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class PublisherAdapter(Protocol):
    kind: str

    def validate_credentials(self, creds: dict[str, str]) -> None: ...

    def publish(self, *, post: dict[str, Any], creds: dict[str, str],
                settings: Settings) -> PublicationResult: ...


_REGISTRY: dict[str, PublisherAdapter] = {}


def register(adapter: PublisherAdapter) -> PublisherAdapter:
    _REGISTRY[adapter.kind] = adapter
    return adapter


def get_publisher(kind: str) -> PublisherAdapter:
    if kind not in _REGISTRY:
        raise KeyError(f"no publisher registered for kind={kind}")
    return _REGISTRY[kind]


# import side-effect: register all adapters
from . import mock_adapters  # noqa: E402,F401
