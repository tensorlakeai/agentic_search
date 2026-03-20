"""Pydantic input models for the agentic_search harness."""

from __future__ import annotations

import os

from pydantic import BaseModel, Field


class AgenticQueryInput(BaseModel):
    query: str = Field(description="Question the agent should answer")
    website: str = Field(description="Seed website to explore")
    openai_model: str = Field(
        default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-5.1"),
        description="OpenAI model used for the harness",
    )
    browserbase_api_key: str = Field(
        default_factory=lambda: os.getenv("BROWSERBASE_API_KEY", ""),
        description="Browserbase API key",
    )


class BrowserFetchInput(BaseModel):
    url: str
    allowed_domain: str | None = None
    max_links: int = Field(default=25, ge=1, le=100)
    max_chars: int = Field(default=9000, ge=1000, le=25000)
    timeout_ms: int = Field(default=45000, ge=5000, le=120000)
    browserbase_api_key: str = ""


class BrowserSearchInput(BaseModel):
    start_url: str
    search_query: str
    allowed_domain: str | None = None
    max_results: int = Field(default=8, ge=1, le=30)
    timeout_ms: int = Field(default=45000, ge=5000, le=120000)
    browserbase_api_key: str = ""


class DownloadFileInput(BaseModel):
    url: str
    allowed_domain: str | None = None
    max_bytes: int = Field(default=8_000_000, ge=10_000, le=40_000_000)
    timeout_seconds: int = Field(default=60, ge=5, le=240)


class DocumentToMarkdownInput(BaseModel):
    file_b64: str
    filename: str
    query: str | None = None
    max_chars: int = Field(default=25_000, ge=1_000, le=120_000)
    openai_model: str = Field(
        default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    )
