"""Browserbase-backed tool functions for the harness."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from images import browser_image, search_image
from models import BrowserFetchInput, BrowserSearchInput
from tensorlake.applications import function


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def _resolve_required(value: str, env_name: str, label: str) -> str:
    resolved = value or os.getenv(env_name, "")
    if not resolved:
        raise ValueError(f"Missing required {label}. Set {env_name} or pass it in input.")
    return resolved


def _collect_page_with_browserbase(input: BrowserFetchInput) -> dict[str, Any]:
    """Fetch a page via the Browserbase Fetch API (no Playwright session required)."""
    import httpx
    from bs4 import BeautifulSoup

    api_key = _resolve_required(
        input.browserbase_api_key,
        "BROWSERBASE_API_KEY",
        "Browserbase API key",
    )

    allowed_domain = (input.allowed_domain or "").strip().lower() or None
    requested_domain = _extract_domain(input.url)
    if allowed_domain and requested_domain != allowed_domain:
        raise ValueError(
            f"Requested URL domain '{requested_domain}' is outside allowed domain '{allowed_domain}'."
        )

    response = httpx.post(
        "https://api.browserbase.com/v1/fetch",
        headers={"x-bb-api-key": api_key, "Content-Type": "application/json"},
        json={"url": input.url},
        timeout=input.timeout_ms / 1000,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    title = soup.title.get_text(strip=True) if soup.title else ""

    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    body = soup.body or soup
    raw_text = body.get_text(separator="\n")
    import re
    cleaned = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+\n", "\n", raw_text)).strip()
    text = cleaned[: input.max_chars]

    seen: set[str] = set()
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        try:
            absolute = str(urlparse(input.url)._replace(
                scheme=urlparse(input.url).scheme,
                netloc=urlparse(input.url).netloc,
            ).geturl())
            from urllib.parse import urljoin
            absolute = urljoin(input.url, href)
        except Exception:
            continue
        if not (absolute.startswith("http://") or absolute.startswith("https://")):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append(absolute)
        if len(links) >= input.max_links:
            break

    if allowed_domain:
        links = [link for link in links if _extract_domain(link) == allowed_domain]

    return {
        "success": True,
        "requested_url": input.url,
        "url": input.url,
        "title": title,
        "text": text,
        "links": links,
        "fetched_at": _now_iso(),
    }


async def _search_site_with_stagehand(input: BrowserSearchInput) -> dict[str, Any]:
    """Search a site using Stagehand's act()/extract() instead of raw Playwright."""
    from stagehand import AsyncStagehand

    api_key = _resolve_required(
        input.browserbase_api_key,
        "BROWSERBASE_API_KEY",
        "Browserbase API key",
    )
    model_api_key = _resolve_required("", "OPENAI_API_KEY", "OpenAI API key")

    allowed_domain = (input.allowed_domain or "").strip().lower() or None
    requested_domain = _extract_domain(input.start_url)
    if allowed_domain and requested_domain != allowed_domain:
        raise ValueError(
            f"Requested URL domain '{requested_domain}' is outside allowed domain '{allowed_domain}'."
        )

    client = AsyncStagehand(
        browserbase_api_key=api_key,
        model_api_key=model_api_key,
    )

    session = await client.sessions.start(model_name="openai/gpt-4o-mini")
    try:
        await client.sessions.navigate(id=session.id, url=input.start_url)

        await client.sessions.act(
            id=session.id,
            input=(
                f"Find the search input on this page, click it, type '{input.search_query}', "
                "then submit the search by pressing Enter or clicking the search button."
            ),
        )

        # Give the results page time to load
        await asyncio.sleep(min(input.timeout_ms / 1000 * 0.1, 3.0))

        schema = {
            "results": [
                {
                    "url": "string",
                    "title": "string",
                    "snippet": "string",
                }
            ]
        }
        extracted = await client.sessions.extract(
            id=session.id,
            instruction=(
                "Extract all search result items from this page. "
                "For each result include the full absolute URL (starting with https://), "
                "page title, and a short text snippet."
            ),
            schema=schema,
        )

        # Result lives at extracted.data.result
        raw = getattr(getattr(extracted, "data", None), "result", None)
        if raw is None:
            raw = extracted if isinstance(extracted, list) else []
        results: list[dict[str, Any]] = raw if isinstance(raw, list) else []

        if allowed_domain:
            results = [r for r in results if _extract_domain(r.get("url", "")) == allowed_domain]
        results = results[: input.max_results]

        return {
            "success": True,
            "start_url": input.start_url,
            "search_query": input.search_query,
            "search_url": input.start_url,
            "results": results,
            "fetched_at": _now_iso(),
        }
    finally:
        await client.sessions.end(id=session.id)
        # Explicitly close the Stagehand client so its internal httpx.AsyncClient
        # is cleaned up before the event loop shuts down.
        for attr in ("close", "aclose"):
            if callable(getattr(client, attr, None)):
                await getattr(client, attr)()
                break


@function(image=browser_image, secrets=["BROWSERBASE_API_KEY"])
def fetch_page(input: BrowserFetchInput) -> dict[str, Any]:
    """Fetch a page with the Browserbase Fetch API and return title/text/links."""
    try:
        return _collect_page_with_browserbase(input)
    except Exception as exc:
        return {
            "success": False,
            "url": input.url,
            "error": str(exc),
        }


def _search_site_sync(input: BrowserSearchInput) -> dict[str, Any]:
    """Run the async Stagehand search with proper event loop cleanup."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_search_site_with_stagehand(input))
    finally:
        # Let pending tasks (httpx cleanup) finish before closing the loop
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


@function(image=search_image, secrets=["BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID", "OPENAI_API_KEY"])
def search_site(input: BrowserSearchInput) -> dict[str, Any]:
    """Use Stagehand to drive the site search UI and collect relevant result links."""
    try:
        return _search_site_sync(input)
    except Exception as exc:
        return {
            "success": False,
            "start_url": input.start_url,
            "search_query": input.search_query,
            "error": str(exc),
        }
