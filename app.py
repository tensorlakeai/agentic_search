"""Agentic Search - Tensorlake Application.

Given a user query and a target website, this application lets an OpenAI agent
choose and run tools (search/fetch/read/grep/shell) to research and answer.
"""

import json
import os
import re
from base64 import b64decode
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from browserbase_tools import (
    fetch_page as fetch_page_tool,
    search_site as search_site_tool,
)
from files import document_to_markdown, download_file
from images import agent_image
from local_workspace import LocalWorkspace
from models import (
    AgenticQueryInput,
    BrowserFetchInput,
    BrowserSearchInput,
    DocumentToMarkdownInput,
    DownloadFileInput,
)
from prompts import SYSTEM_INSTRUCTIONS, build_agent_prompt
from utils import (
    _extract_domain,
    _resolve_required,
    _tool_result_to_text,
    _trim_text,
)
from tensorlake.applications import (
    RequestContext,
    application,
    function,
    run_local_application,
)


@application()
@function(
    image=agent_image,
    secrets=[
        "OPENAI_API_KEY",
        "BROWSERBASE_API_KEY",
        "BROWSERBASE_PROJECT_ID",
    ],
)
async def agentic_search(input: AgenticQueryInput) -> dict[str, Any]:
    """Agentic search application, uses OpenAI Agents SDK and Browserbase"""
    from agents import (
        Agent,
        Runner,
        ShellCallOutcome,
        ShellCommandOutput,
        ShellCommandRequest,
        ShellResult,
        ShellTool,
        function_tool,
    )

    ctx = RequestContext.get()
    run_id = str(getattr(ctx, "request_id", "")) or f"run-{int(datetime.now(timezone.utc).timestamp())}"
    allowed_domain = _extract_domain(input.website)
    workspace = LocalWorkspace.create(
        run_id=run_id,
        query=input.query,
        website=input.website,
        base_dir=os.getenv("AGENTIC_SEARCH_RUNS_DIR", ".agentic_search_runs"),
    )

    _resolve_required("", "OPENAI_API_KEY", "OpenAI API key")
    _resolve_required(input.browserbase_api_key, "BROWSERBASE_API_KEY", "Browserbase API key")
    _resolve_required(input.browserbase_project_id, "BROWSERBASE_PROJECT_ID", "Browserbase project ID")

    search_observations: list[dict[str, Any]] = []
    local_file_search_observations: list[dict[str, Any]] = []
    tool_calls_executed = 0
    final_answer = ""
    progress_current = 0.0

    def _workspace_relative(path: str) -> str:
        if not path:
            return ""
        try:
            return str(Path(path).resolve().relative_to(workspace.run_dir.resolve()))
        except Exception:
            return path

    def _emit_progress(current: float, message: str, attributes: dict[str, Any] | None = None) -> None:
        nonlocal progress_current
        progress_current = max(progress_current, min(float(current), 100.0))
        safe_attributes = {str(key): str(value) for key, value in (attributes or {}).items()}
        ctx.progress.update(progress_current, 100, message, safe_attributes)
        try:
            workspace.append_progress(message)
        except Exception:
            pass

    def _track_tool(tool_name: str, args: dict[str, Any], result: dict[str, Any]) -> None:
        nonlocal tool_calls_executed
        tool_calls_executed += 1
        success = bool(result.get("success", False))


        status_text = "succeeded" if success else f"failed ({result.get('error', 'unknown error')})"
        _emit_progress(min(progress_current + 1.5, 90.0), f"{tool_name} {status_text}")

        try:
            workspace.record_tool_call(
                sequence=tool_calls_executed,
                tool_name=tool_name,
                args=args,
                result=result,
                summary_text=_trim_text(_tool_result_to_text(tool_name, result), 9000),
            )
        except Exception:
            pass

        if tool_name == "search_site":
            compact_results = []
            for item in result.get("results", []):
                if not item.get("url"):
                    continue
                compact_results.append(
                    {
                        "url": item.get("url", ""),
                        "title": _trim_text(str(item.get("title", "")), 180),
                        "snippet": _trim_text(str(item.get("snippet", "")), 320),
                    }
                )
            search_observations.append(
                {
                    "query": str(args.get("search_query") or ""),
                    "success": success,
                    "search_url": str(result.get("search_url", "")),
                    "error": str(result.get("error", "")),
                    "results": compact_results[:20],
                }
            )
        elif tool_name == "rg":
            query = str(args.get("query", "")).strip()
            local_file_search_observations.append(
                {
                    "tool": "rg",
                    "query": query,
                    "matches": int(result.get("matches", 0)),
                    "output_excerpt": _trim_text(str(result.get("output", "")), 500),
                }
            )
        elif tool_name == "shell":
            command = str(args.get("command", ""))
            if success and re.search(r"(^|\s)(rg|grep)\b", command):
                local_file_search_observations.append(
                    {
                        "tool": "shell",
                        "query": command,
                        "matches": 0,
                        "output_excerpt": _trim_text(str(result.get("output", "")), 500),
                    }
                )

    _emit_progress(2, f"started run for '{input.query}' on {input.website}")
    _emit_progress(5, "the agent will choose search and fetch steps.")

    @function_tool
    def search_site(search_query: str, max_results: int = 8) -> str:
        """Search the target site using its search bar with a query variation."""
        safe_max_results = max(1, min(max_results, 20))
        result = search_site_tool(
            BrowserSearchInput(
                start_url=input.website,
                search_query=search_query,
                allowed_domain=allowed_domain,
                max_results=safe_max_results,
                browserbase_project_id=input.browserbase_project_id,
                browserbase_api_key=input.browserbase_api_key,
            )
        )
        _track_tool("search_site", {"search_query": search_query, "max_results": safe_max_results}, result)
        return _tool_result_to_text("search_site", result)

    @function_tool
    def fetch_page(url: str, max_chars: int = 9000, max_links: int = 25) -> str:
        """Fetch page content, save it to disk, and return the local file path."""
        result = fetch_page_tool(
            BrowserFetchInput(
                url=url,
                allowed_domain=allowed_domain,
                max_chars=max_chars,
                max_links=max_links,
                browserbase_project_id=input.browserbase_project_id,
                browserbase_api_key=input.browserbase_api_key,
            )
        )
        if result.get("success"):
            try:
                local_path = workspace.save_page(
                    url=str(result.get("url", url)),
                    title=str(result.get("title", "")),
                    text=str(result.get("text", "")),
                    links=[str(link) for link in result.get("links", []) if link],
                    source="fetch_page",
                )
                result["local_path"] = _workspace_relative(local_path)
            except Exception:
                pass

        _track_tool("fetch_page", {"url": url}, result)
        return _tool_result_to_text("fetch_page", result)

    @function_tool
    def read_document(url: str, focus_query: str = "") -> str:
        """Download a document URL, convert to markdown, and return local file paths."""
        download_result = download_file(
            DownloadFileInput(url=url, allowed_domain=allowed_domain, max_bytes=8_000_000)
        )
        _track_tool("download_file", {"url": url}, download_result)
        if not download_result.get("success"):
            return _tool_result_to_text("download_file", download_result)

        download_path = ""
        try:
            download_path = workspace.save_downloaded_file(
                url=url,
                filename=download_result.get("filename", "downloaded_file"),
                file_bytes=b64decode(download_result["file_b64"]),
            )
            download_path = _workspace_relative(download_path)
        except Exception:
            download_path = ""

        result = document_to_markdown(
            DocumentToMarkdownInput(
                file_b64=download_result["file_b64"],
                filename=download_result.get("filename", "downloaded_file"),
                query=focus_query or input.query,
                openai_model=input.openai_model,
                max_chars=25_000,
            )
        )
        if result.get("success"):
            try:
                workspace.save_page(
                    url=url,
                    title=str(result.get("filename", download_result.get("filename", "document"))),
                    text=str(result.get("markdown", ""))[:9000],
                    links=[],
                    source="read_document",
                )
                markdown_path = workspace.save_document_markdown(
                    url=url,
                    filename=result.get("filename", download_result.get("filename", "document")),
                    markdown=result.get("markdown", ""),
                )
                result["local_path"] = _workspace_relative(markdown_path)
            except Exception:
                pass

        _track_tool("document_to_markdown", {"url": url, "focus_query": focus_query}, result)

        response_text = _tool_result_to_text("document_to_markdown", result)
        if download_path:
            response_text += f"\nDownloaded binary saved to local file: {download_path}"
        return response_text

    @function_tool
    def list_local_files(max_files: int = 200) -> str:
        """List files captured in this run's local workspace."""
        safe_limit = max(1, min(max_files, 500))
        result = workspace.list_files_text(max_files=safe_limit)
        _track_tool("list_local_files", {"max_files": safe_limit}, result)
        return _tool_result_to_text("list_local_files", result)

    @function_tool
    def read_local_file(path: str, max_chars: int = 12000) -> str:
        """Read a local workspace file by relative path."""
        safe_limit = max(200, min(max_chars, 50000))
        result = workspace.read_file_text(path, max_chars=safe_limit)
        _track_tool("read_local_file", {"path": path, "max_chars": safe_limit}, result)
        return _tool_result_to_text("read_local_file", result)

    @function_tool
    def rg(query: str, max_matches: int = 40) -> str:
        """Run ripgrep over the local workspace and return matching lines."""
        safe_limit = max(1, min(max_matches, 200))
        result = workspace.grep_text(query, max_matches=safe_limit)
        _track_tool("rg", {"query": query, "max_matches": safe_limit}, result)
        return _tool_result_to_text("rg", result)

    def _shell_executor(request: ShellCommandRequest) -> ShellResult:
        timeout_ms = request.data.action.timeout_ms or 30_000
        timeout_seconds = max(1, min(timeout_ms // 1000, 180))
        max_output = request.data.action.max_output_length or 20_000
        outputs: list[ShellCommandOutput] = []

        for command in request.data.action.commands:
            shell_result = workspace.run_shell(
                command,
                timeout_seconds=timeout_seconds,
                max_output_chars=max_output,
            )
            exit_code = int(shell_result.get("exit_code", 1 if shell_result.get("error") else 0))
            output_text = str(shell_result.get("output", shell_result.get("error", ""))) or "(no output)"

            _track_tool(
                "shell",
                {"command": command, "timeout_seconds": timeout_seconds},
                {
                    "success": exit_code == 0,
                    "command": command,
                    "exit_code": exit_code,
                    "output": output_text,
                    "error": shell_result.get("error", ""),
                },
            )

            outputs.append(
                ShellCommandOutput(
                    command=command,
                    stdout=output_text if exit_code == 0 else "",
                    stderr=output_text if exit_code != 0 else "",
                    outcome=ShellCallOutcome(type="exit", exit_code=exit_code),
                )
            )

        return ShellResult(output=outputs, max_output_length=max_output)

    shell_tool = ShellTool(
        executor=_shell_executor,
        name="shell",
        needs_approval=False,
        environment={"type": "local"},
    )

    openai_tools = [
        search_site,
        fetch_page,
        read_document,
        list_local_files,
        read_local_file,
        rg,
        shell_tool,
    ]

    prompt = build_agent_prompt(
        run_id=run_id,
        query=input.query,
        website=input.website,
        allowed_domain=allowed_domain,
        workspace_path=str(workspace.run_dir),
    )

    _emit_progress(60, "Running OpenAI Agents SDK orchestration", {"run_id": run_id})
    agent = Agent(
        name="Browserbase File-Aware Research Agent",
        instructions=SYSTEM_INSTRUCTIONS,
        tools=openai_tools,
        model=input.openai_model,
    )

    try:
        _emit_progress(62, "Agent is running research steps")

        run_result = await Runner.run(
            agent,
            prompt,
        )
        final_answer = str(run_result.final_output or "").strip()
        _emit_progress(88, "Agent completed research and drafted an answer")
    except Exception as exc:
        final_answer = f"Agent execution failed: {exc}"
        _emit_progress(88, f"Agent failed: {exc}")

    if not final_answer:
        final_answer = "No final answer produced by the agent."
        _emit_progress(89, "Agent produced no final answer text")

    ranked_hits: dict[str, dict[str, Any]] = {}
    for observation in search_observations:
        if not observation.get("success"):
            continue
        query_text = observation.get("query", "")
        for rank, hit in enumerate(observation.get("results", [])):
            url = hit.get("url", "")
            if not url:
                continue
            score = max(len(observation.get("results", [])) - rank, 1)
            existing = ranked_hits.setdefault(
                url,
                {
                    "url": url,
                    "title": hit.get("title", ""),
                    "sample_snippet": hit.get("snippet", ""),
                    "score": 0,
                    "times_seen": 0,
                    "queries": [],
                },
            )
            existing["score"] += score
            existing["times_seen"] += 1
            if query_text and query_text not in existing["queries"]:
                existing["queries"].append(query_text)
            if not existing.get("sample_snippet") and hit.get("snippet"):
                existing["sample_snippet"] = hit.get("snippet", "")

    top_search_hits = sorted(
        ranked_hits.values(),
        key=lambda item: (int(item.get("score", 0)), int(item.get("times_seen", 0))),
        reverse=True,
    )[:12]

    citations = [
        {"url": item.get("url", ""), "title": item.get("title", "")}
        for item in top_search_hits
        if item.get("url")
    ]

    try:
        workspace.save_final_answer(answer=final_answer, citations=citations)
    except Exception:
        pass

    search_evidence = {
        "search_calls": len(search_observations),
        "successful_search_calls": sum(1 for item in search_observations if item.get("success")),
        "queries_attempted": [item.get("query", "") for item in search_observations if item.get("query")],
        "top_search_hits": top_search_hits,
        "local_file_search_observations": local_file_search_observations[:20],
        "raw_search_observations": search_observations[:20],
    }

    _emit_progress(98, "Run complete; Elasticsearch is disabled in this example")
    _emit_progress(100, f"Run complete: made {tool_calls_executed} tool calls")

    return {
        "query": input.query,
        "website": input.website,
        "answer": final_answer,
        "local_workspace": {
            "run_dir": str(workspace.run_dir),
        },
        "citations": citations,
        "search_evidence": search_evidence,
    }


# ---------------------------------------------------------------------------
# Local test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    test_query = os.getenv("TEST_QUERY", "What is this website about?")
    test_website = os.getenv("TEST_WEBSITE", "https://docs.browserbase.com/introduction")

    test_input = AgenticQueryInput(
        query=test_query,
        website=test_website,
    )

    print("Running agentic_search with Tensorlake local runner...")
    request = run_local_application(agentic_search, test_input)
    result = request.output()

    print("\nRun complete")
    print(json.dumps(result, indent=2))
