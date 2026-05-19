"""Runtime configuration. Reads from environment with sensible defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str
    port: int
    master_key: str
    llm_provider: str
    image_provider: str
    search_provider: str
    media_dir: str

    @property
    def use_mock_llm(self) -> bool:
        return self.llm_provider == "mock"

    @property
    def use_mock_images(self) -> bool:
        return self.image_provider == "mock"

    @property
    def use_mock_search(self) -> bool:
        return self.search_provider == "mock"

    @property
    def use_openai_web_search(self) -> bool:
        """Web search via OpenAI Responses API (POST /v1/responses).

        Set ``AICREW_SEARCH_PROVIDER=openai_responses`` in the environment to
        let agents whose ``tools_enabled`` includes ``"web_search"`` actually
        do live web search through OpenAI's first-party tool. Requires an
        OPENAI_API_KEY and an ``openai:*`` model (the 302.ai gateway does
        not expose /responses + web_search and will fall back silently).
        """
        return self.search_provider == "openai_responses"


def load_settings() -> Settings:
    return Settings(
        db_path=os.environ.get("AICREW_DB", "aicrew.db"),
        port=int(os.environ.get("AICREW_PORT", "8000")),
        master_key=os.environ.get("AICREW_MASTER_KEY", "dev-key-please-change-32bytes!!"),
        llm_provider=os.environ.get("AICREW_LLM_PROVIDER", "mock"),
        image_provider=os.environ.get("AICREW_IMAGE_PROVIDER", "mock"),
        search_provider=os.environ.get("AICREW_SEARCH_PROVIDER", "mock"),
        media_dir=os.environ.get("AICREW_MEDIA_DIR", "media"),
    )
