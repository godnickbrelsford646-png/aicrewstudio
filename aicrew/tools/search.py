"""Search and URL fetch tools. Mock by default; real providers swappable."""

from __future__ import annotations

import hashlib
from typing import Any

from ..settings import Settings


def _slug(seed: str) -> str:
    return hashlib.md5(seed.encode("utf-8")).hexdigest()[:10]


def web_search(query: str, *, settings: Settings, top_k: int = 5) -> list[dict[str, Any]]:
    if settings.use_mock_search:
        # Deterministic, plausible-looking results so demos read well.
        suffixes = [
            ("ai-trends", "Top 10 trends shaping the field"),
            ("research", "Recent peer-reviewed paper roundup"),
            ("explainer", "Explained for non-experts"),
            ("case-study", "Real-world deployment case study"),
            ("guide", "Practical hands-on guide"),
        ]
        out: list[dict[str, Any]] = []
        for i, (slug, label) in enumerate(suffixes[:top_k]):
            out.append(
                {
                    "title": f"{query} — {label}",
                    "url": f"https://mock.example/{slug}/{_slug(query + slug)}",
                    "snippet": (
                        f"Mock-search result for '{query}'. {label}. "
                        "Synthetic content used because the sandbox has no internet."
                    ),
                    "rank": i + 1,
                }
            )
        return out
    raise NotImplementedError("real search providers (tavily/serper) not wired in this build")


def fetch_url(url: str, *, settings: Settings) -> dict[str, Any]:
    if settings.use_mock_search:
        return {
            "url": url,
            "status": 200,
            "title": f"Mock page: {url}",
            "text": (
                f"Synthetic body for {url}. In production this would be real fetched HTML "
                "rendered to text via readability/trafilatura."
            ),
            "fetched_at": "mock",
        }
    raise NotImplementedError("real fetch_url not wired in this build")
