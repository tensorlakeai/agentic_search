"""File download and document reading tools for the harness."""

from __future__ import annotations

import os
from base64 import b64decode
from typing import Any

from images import document_image
from models import DocumentToMarkdownInput, DownloadFileInput
from tensorlake.applications import function


def _resolve_required(value: str, env_name: str, label: str) -> str:
    resolved = value or os.getenv(env_name, "")
    if not resolved:
        raise ValueError(f"Missing required {label}. Set {env_name} or pass it in input.")
    return resolved


def _extract_domain(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).netloc.lower()


@function(image=document_image)
def download_file(input: DownloadFileInput) -> dict[str, Any]:
    """Download a file and return base64 content for downstream tools."""
    import requests
    from base64 import b64encode

    allowed_domain = (input.allowed_domain or "").strip().lower() or None
    requested_domain = _extract_domain(input.url)
    if allowed_domain and requested_domain != allowed_domain:
        return {
            "success": False,
            "url": input.url,
            "error": (
                f"Requested URL domain '{requested_domain}' is outside allowed domain "
                f"'{allowed_domain}'."
            ),
        }

    try:
        response = requests.get(input.url, timeout=input.timeout_seconds)
        response.raise_for_status()
        content = response.content
        if len(content) > input.max_bytes:
            return {
                "success": False,
                "url": input.url,
                "error": f"File exceeds max_bytes ({len(content)} > {input.max_bytes}).",
                "size_bytes": len(content),
            }

        content_type = response.headers.get("content-type", "")
        filename = input.url.split("/")[-1] or "downloaded_file"
        return {
            "success": True,
            "url": input.url,
            "filename": filename,
            "content_type": content_type,
            "size_bytes": len(content),
            "file_b64": b64encode(content).decode("ascii"),
        }
    except Exception as exc:
        return {"success": False, "url": input.url, "error": str(exc)}


def _extract_text_from_document_bytes(
    client: Any,
    file_bytes: bytes,
    filename: str,
    openai_model: str,
) -> str:
    """Extract text from a document using OpenAI Files + Responses APIs."""
    uploaded = client.files.create(
        file=(filename, file_bytes, "application/octet-stream"),
        purpose="user_data",
    )

    try:
        response = client.responses.create(
            model=openai_model,
            temperature=0.0,
            input=[
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Extract all readable text from the provided file. "
                                "Return plain UTF-8 text only. Do not summarize."
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "input_file", "file_id": uploaded.id},
                        {
                            "type": "input_text",
                            "text": (
                                "Output only extracted text from the file. "
                                "Preserve headings and line breaks when possible."
                            ),
                        },
                    ],
                },
            ],
        )
        return (response.output_text or "").strip()
    finally:
        try:
            client.files.delete(uploaded.id)
        except Exception:
            pass


@function(image=document_image, secrets=["OPENAI_API_KEY"])
def document_to_markdown(input: DocumentToMarkdownInput) -> dict[str, Any]:
    """Convert document bytes into markdown using an OpenAI foundation model."""
    from openai import OpenAI

    try:
        file_bytes = b64decode(input.file_b64)
    except Exception as exc:
        return {"success": False, "filename": input.filename, "error": f"Invalid base64: {exc}"}

    client = OpenAI(api_key=_resolve_required("", "OPENAI_API_KEY", "OpenAI API key"))
    try:
        raw_text = _extract_text_from_document_bytes(
            client=client,
            file_bytes=file_bytes,
            filename=input.filename,
            openai_model=input.openai_model,
        )
    except Exception as exc:
        return {
            "success": False,
            "filename": input.filename,
            "error": f"Could not extract file content via OpenAI Files API: {exc}",
        }

    if not raw_text:
        return {
            "success": False,
            "filename": input.filename,
            "error": "No textual content could be extracted from this document.",
        }

    raw_text = raw_text[: input.max_chars]

    try:
        focus = input.query.strip() if input.query else "the overall content"
        response = client.chat.completions.create(
            model=input.openai_model,
            temperature=0.1,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a document conversion assistant. Convert the provided raw "
                        "document text into concise, well-structured markdown. Preserve facts "
                        "and include headings, bullet points, and key details."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Focus on: {focus}\n"
                        f"Filename: {input.filename}\n\n"
                        "Raw extracted text:\n"
                        f"{raw_text}"
                    ),
                },
            ],
        )
        markdown = (response.choices[0].message.content or "").strip()
        if not markdown:
            markdown = raw_text
        return {
            "success": True,
            "filename": input.filename,
            "markdown": markdown,
            "source_chars": len(raw_text),
        }
    except Exception as exc:
        return {
            "success": False,
            "filename": input.filename,
            "error": f"Foundation-model markdown conversion failed: {exc}",
        }
