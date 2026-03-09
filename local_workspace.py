"""Local filesystem workspace helpers for agentic_search."""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(value: str, max_len: int = 80) -> str:
    text = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip().lower()).strip("-")
    if not text:
        text = "item"
    return text[:max_len]


def _stable_hash(value: str, length: int = 10) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:length]


def _compact_value(value: Any, max_chars: int = 400) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars].rsplit(" ", 1)[0].strip() or text[:max_chars]
    return f"{clipped}..."


@dataclass
class LocalWorkspace:
    run_dir: Path
    run_id: str

    @classmethod
    def create(cls, *, run_id: str, query: str, website: str, base_dir: str) -> "LocalWorkspace":
        normalized_run_id = _slugify(run_id or f"run-{int(datetime.now(timezone.utc).timestamp())}", max_len=120)
        run_dir = (Path(base_dir).expanduser().resolve() / normalized_run_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        workspace = cls(run_dir=run_dir, run_id=normalized_run_id)
        for folder in ["tools", "pages", "docs", "downloads", "notes"]:
            workspace._ensure_dir(folder)

        workspace.write_text(
            "notes/run.md",
            "\n".join(
                [
                    "# Agentic Search Run",
                    f"- Run ID: {workspace.run_id}",
                    f"- Started At: {_now_iso()}",
                    f"- Website: {website}",
                    f"- Query: {query}",
                ]
            ),
        )
        return workspace

    def _ensure_dir(self, relative_dir: str) -> Path:
        path = self.run_dir / relative_dir
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_text(self, relative_path: str, text: str) -> str:
        target = self.run_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return str(target)

    def append_progress(self, message: str) -> None:
        line = f"[{_now_iso()}] {message}\n"
        progress_file = self.run_dir / "progress.log"
        with progress_file.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def record_tool_call(
        self,
        *,
        sequence: int,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        summary_text: str,
    ) -> str:
        safe_tool = _slugify(tool_name, max_len=60)
        rel_path = f"tools/{sequence:03d}_{safe_tool}.md"

        arg_lines = [f"- {key}: {_compact_value(value)}" for key, value in (args or {}).items()] or ["- (none)"]
        status = "success" if result.get("success") else f"failure ({result.get('error', 'unknown error')})"

        body = "\n".join(
            [
                f"# Tool Call {sequence}: {tool_name}",
                f"- Time: {_now_iso()}",
                f"- Status: {status}",
                "",
                "## Arguments",
                *arg_lines,
                "",
                "## Summary",
                (summary_text or "(no summary)").strip(),
            ]
        )
        return self.write_text(rel_path, body)

    def save_page(self, *, url: str, title: str, text: str, links: list[str], source: str) -> str:
        base = _slugify(title or url, max_len=70)
        rel_path = f"pages/{base}__{_stable_hash(url)}.md"
        lines = [
            f"# {title or '(untitled)'}",
            f"- URL: {url}",
            f"- Source: {source}",
            f"- Captured At: {_now_iso()}",
            "",
            "## Content",
            (text or "(empty)").strip() or "(empty)",
            "",
            "## Links",
        ]
        if links:
            lines.extend(f"- {link}" for link in links[:200])
        else:
            lines.append("- (none)")
        return self.write_text(rel_path, "\n".join(lines))

    def save_document_markdown(self, *, url: str, filename: str, markdown: str) -> str:
        base = _slugify(filename or "document", max_len=70)
        rel_path = f"docs/{base}__{_stable_hash(url)}.md"
        lines = [
            f"# Document: {filename or 'document'}",
            f"- URL: {url}",
            f"- Captured At: {_now_iso()}",
            "",
            markdown.strip() or "(empty)",
        ]
        return self.write_text(rel_path, "\n".join(lines))

    def save_downloaded_file(self, *, url: str, filename: str, file_bytes: bytes) -> str:
        safe_name = _slugify(filename or "downloaded-file", max_len=80)
        rel_path = f"downloads/{safe_name}__{_stable_hash(url)}"
        target = self.run_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(file_bytes)
        return str(target)

    def save_final_answer(self, *, answer: str, citations: list[dict[str, Any]]) -> str:
        lines = ["# Final Answer", "", answer.strip() or "(empty)", "", "## Citations"]
        if citations:
            for item in citations:
                lines.append(f"- {item.get('title', '(untitled)')} | {item.get('url', '')}")
        else:
            lines.append("- (none)")
        return self.write_text("notes/final_answer.md", "\n".join(lines))

    def list_files_text(self, max_files: int = 200) -> dict[str, Any]:
        paths: list[str] = []
        for path in sorted(self.run_dir.rglob("*")):
            if path.is_dir():
                continue
            paths.append(str(path.relative_to(self.run_dir)))
            if len(paths) >= max_files:
                break
        return {
            "success": True,
            "count": len(paths),
            "files": paths,
        }

    def read_file_text(self, relative_path: str, max_chars: int = 12000) -> dict[str, Any]:
        requested = (relative_path or "").strip().lstrip("/")
        if not requested:
            return {"success": False, "error": "Path is empty."}

        target = (self.run_dir / requested).resolve()
        if not str(target).startswith(str(self.run_dir)):
            return {"success": False, "error": "Path escapes run workspace."}
        if not target.exists() or not target.is_file():
            return {"success": False, "error": f"File not found: {requested}"}

        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return {"success": False, "error": f"Could not read file: {exc}"}

        clipped = content[:max_chars]
        if len(content) > max_chars:
            clipped += "\n\n[TRUNCATED]"

        return {
            "success": True,
            "path": str(target.relative_to(self.run_dir)),
            "content": clipped,
            "chars": len(content),
        }

    def grep_text(self, query: str, max_matches: int = 40) -> dict[str, Any]:
        term = (query or "").strip()
        if not term:
            return {"success": False, "error": "Search query is empty."}

        rg_cmd = ["rg", "-n", "--no-heading", "-S", "-m", str(max_matches), term, "."]
        try:
            proc = subprocess.run(
                rg_cmd,
                cwd=self.run_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode in (0, 1):
                output = proc.stdout.strip()
                if not output:
                    return {
                        "success": True,
                        "query": term,
                        "matches": 0,
                        "output": "No matches found in local workspace.",
                    }
                lines = output.splitlines()[:max_matches]
                return {
                    "success": True,
                    "query": term,
                    "matches": len(lines),
                    "output": "\n".join(lines),
                }
            return {"success": False, "error": proc.stderr.strip() or "rg failed"}
        except FileNotFoundError:
            grep_cmd = ["grep", "-R", "-n", term, "."]
            proc = subprocess.run(
                grep_cmd,
                cwd=self.run_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode not in (0, 1):
                return {"success": False, "error": proc.stderr.strip() or "grep failed"}
            output = proc.stdout.strip()
            if not output:
                return {
                    "success": True,
                    "query": term,
                    "matches": 0,
                    "output": "No matches found in local workspace.",
                }
            lines = output.splitlines()[:max_matches]
            return {
                "success": True,
                "query": term,
                "matches": len(lines),
                "output": "\n".join(lines),
            }

    def run_shell(self, command: str, timeout_seconds: int = 30, max_output_chars: int = 20000) -> dict[str, Any]:
        cmd = (command or "").strip()
        if not cmd:
            return {"success": False, "error": "Command is empty."}

        try:
            proc = subprocess.run(
                ["bash", "-lc", cmd],
                cwd=self.run_dir,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": f"Command timed out after {timeout_seconds} seconds.",
                "command": cmd,
            }
        except Exception as exc:
            return {
                "success": False,
                "error": f"Command execution failed: {exc}",
                "command": cmd,
            }

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        combined = stdout
        if stderr:
            combined = f"{stdout}\n{stderr}".strip() if combined else stderr
        if not combined:
            combined = "(no output)"

        if len(combined) > max_output_chars:
            combined = combined[:max_output_chars].rstrip() + "\n\n[TRUNCATED]"

        return {
            "success": True,
            "command": cmd,
            "exit_code": proc.returncode,
            "output": combined,
        }
