"""Shared helper utilities for agentic_search."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse


def _extract_domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def _resolve_required(value: str, env_name: str, label: str) -> str:
    resolved = value or os.getenv(env_name, "")
    if not resolved:
        raise ValueError(f"Missing required {label}. Set {env_name} or pass it in input.")
    return resolved


def _trim_text(text: str, max_chars: int = 1200) -> str:
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    clipped = value[:max_chars].rsplit(" ", 1)[0].strip()
    return (clipped or value[:max_chars]) + "..."


def _tool_result_to_text(tool_name: str, result: dict[str, Any]) -> str:
    if tool_name == "shell":
        if result.get("error"):
            return f"shell command failed: {result.get('error')}"
        return (
            f"Command: {result.get('command', '')}\n"
            f"Exit code: {result.get('exit_code', 0)}\n\n"
            f"{result.get('output', '')}"
        )

    success = bool(result.get("success", False))
    if not success:
        return f"{tool_name} failed: {result.get('error', 'Unknown error')}"

    if tool_name == "search_site":
        results = result.get("results", [])[:10]
        lines = [
            f"Search completed for '{result.get('search_query', '')}'.",
            f"Search URL: {result.get('search_url', '')}",
        ]
        if not results:
            lines.append("No results were found.")
            return "\n".join(lines)
        lines.append("Top results:")
        for idx, item in enumerate(results, start=1):
            title = item.get("title", "").strip() or "(untitled)"
            url = item.get("url", "").strip()
            lines.append(f"{idx}. {title} - {url}")
        return "\n".join(lines)

    if tool_name == "fetch_page":
        lines = [
            f"Fetched page: {result.get('title', '').strip() or '(untitled)'}",
            f"URL: {result.get('url', '')}",
        ]
        local_path = result.get("local_path", "")
        if local_path:
            lines.append(f"Saved content to local file: {local_path}")
            lines.append("Use `read_local_file` or `shell` (`cat`, `rg`) to inspect content.")
        return "\n".join(lines)

    if tool_name == "download_file":
        filename = result.get("filename", "downloaded_file")
        return (
            f"Downloaded file '{filename}' from {result.get('url', '')}. "
            f"Size: {result.get('size_bytes', 0)} bytes."
        )

    if tool_name == "document_to_markdown":
        filename = result.get("filename", "")
        local_path = result.get("local_path", "")
        if local_path:
            return (
                f"Converted '{filename}' to markdown.\n"
                f"Saved markdown to local file: {local_path}\n"
                "Use `read_local_file` or `shell` (`cat`, `rg`) to inspect content."
            )
        return f"Converted '{filename}' to markdown."

    if tool_name == "list_local_files":
        files = result.get("files", [])[:200]
        if not files:
            return "No local files are available in the workspace yet."
        lines = [f"Local workspace files ({result.get('count', len(files))}):"]
        lines.extend(f"- {path}" for path in files)
        return "\n".join(lines)

    if tool_name == "read_local_file":
        return (
            f"Read local file: {result.get('path', '')}\n"
            f"Characters: {result.get('chars', 0)}\n\n"
            f"{result.get('content', '')}"
        )

    if tool_name == "rg":
        return (
            f"Local search for '{result.get('query', '')}' found "
            f"{result.get('matches', 0)} matches:\n{result.get('output', '')}"
        )

    return f"{tool_name} succeeded."
