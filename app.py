"""Agentic Search - Tensorlake Application.

Given a user query and a target website, this application lets an OpenAI agent
choose and run tools (search/fetch/read/grep/shell) to research and answer.
"""

import json
import os
from base64 import b64decode
from functools import partial
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

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
    _emit_progress,
    _resolve_required,
    _track_tool,
    _tool_result_to_text,
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
    run_id = ctx.request_id
    allowed_domain = _extract_domain(input.website)
    workspace = LocalWorkspace.create(
        run_id=run_id,
        query=input.query,
        website=input.website,
        base_dir=os.getenv("AGENTIC_SEARCH_RUNS_DIR", ".agentic_search_runs"),
    )

    _resolve_required("", "OPENAI_API_KEY", "OpenAI API key")
    _resolve_required(input.browserbase_api_key, "BROWSERBASE_API_KEY", "Browserbase API key")

    search_observations: list[dict[str, Any]] = []
    local_file_search_observations: list[dict[str, Any]] = []
    tool_calls_executed = 0
    final_answer = ""
    progress_current = 0.0
    progress = ctx.progress

    def _workspace_relative(path: str) -> str:
        if not path:
            return ""
        try:
            return str(Path(path).resolve().relative_to(workspace.run_dir.resolve()))
        except Exception:
            return path

    emit_progress = partial(_emit_progress, progress=progress, workspace=workspace)
    track_tool = partial(
        _track_tool,
        progress=progress,
        workspace=workspace,
        search_observations=search_observations,
        local_file_search_observations=local_file_search_observations,
    )

    progress_current = emit_progress(
        progress_current=progress_current,
        current=2,
        message=f"started run for '{input.query}' on {input.website}",
    )
    progress_current = emit_progress(
        progress_current=progress_current,
        current=5,
        message="the agent will choose search and fetch steps.",
    )

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
                browserbase_api_key=input.browserbase_api_key,
            )
        )
        nonlocal tool_calls_executed, progress_current
        tool_calls_executed, progress_current = track_tool(
            tool_name="search_site",
            args={"search_query": search_query, "max_results": safe_max_results},
            result=result,
            tool_calls_executed=tool_calls_executed,
            progress_current=progress_current,
        )
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

        nonlocal tool_calls_executed, progress_current
        tool_calls_executed, progress_current = track_tool(
            tool_name="fetch_page",
            args={"url": url},
            result=result,
            tool_calls_executed=tool_calls_executed,
            progress_current=progress_current,
        )
        return _tool_result_to_text("fetch_page", result)

    @function_tool
    def read_document(url: str, focus_query: str = "") -> str:
        """Download a document URL, convert to markdown, and return local file paths."""
        download_result = download_file(
            DownloadFileInput(url=url, allowed_domain=allowed_domain, max_bytes=8_000_000)
        )
        nonlocal tool_calls_executed, progress_current
        tool_calls_executed, progress_current = track_tool(
            tool_name="download_file",
            args={"url": url},
            result=download_result,
            tool_calls_executed=tool_calls_executed,
            progress_current=progress_current,
        )
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

        tool_calls_executed, progress_current = track_tool(
            tool_name="document_to_markdown",
            args={"url": url, "focus_query": focus_query},
            result=result,
            tool_calls_executed=tool_calls_executed,
            progress_current=progress_current,
        )

        response_text = _tool_result_to_text("document_to_markdown", result)
        if download_path:
            response_text += f"\nDownloaded binary saved to local file: {download_path}"
        return response_text

    @function_tool
    def list_local_files(max_files: int = 200) -> str:
        """List files captured in this run's local workspace."""
        safe_limit = max(1, min(max_files, 500))
        result = workspace.list_files_text(max_files=safe_limit)
        nonlocal tool_calls_executed, progress_current
        tool_calls_executed, progress_current = track_tool(
            tool_name="list_local_files",
            args={"max_files": safe_limit},
            result=result,
            tool_calls_executed=tool_calls_executed,
            progress_current=progress_current,
        )
        return _tool_result_to_text("list_local_files", result)

    @function_tool
    def read_local_file(path: str, max_chars: int = 12000) -> str:
        """Read a local workspace file by relative path."""
        safe_limit = max(200, min(max_chars, 50000))
        result = workspace.read_file_text(path, max_chars=safe_limit)
        nonlocal tool_calls_executed, progress_current
        tool_calls_executed, progress_current = track_tool(
            tool_name="read_local_file",
            args={"path": path, "max_chars": safe_limit},
            result=result,
            tool_calls_executed=tool_calls_executed,
            progress_current=progress_current,
        )
        return _tool_result_to_text("read_local_file", result)

    @function_tool
    def rg(query: str, max_matches: int = 40) -> str:
        """Run ripgrep over the local workspace and return matching lines."""
        safe_limit = max(1, min(max_matches, 200))
        result = workspace.grep_text(query, max_matches=safe_limit)
        nonlocal tool_calls_executed, progress_current
        tool_calls_executed, progress_current = track_tool(
            tool_name="rg",
            args={"query": query, "max_matches": safe_limit},
            result=result,
            tool_calls_executed=tool_calls_executed,
            progress_current=progress_current,
        )
        return _tool_result_to_text("rg", result)

    def _shell_executor(request: ShellCommandRequest) -> ShellResult:
        nonlocal tool_calls_executed, progress_current
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

            tool_calls_executed, progress_current = track_tool(
                tool_name="shell",
                args={"command": command, "timeout_seconds": timeout_seconds},
                result={
                    "success": exit_code == 0,
                    "command": command,
                    "exit_code": exit_code,
                    "output": output_text,
                    "error": shell_result.get("error", ""),
                },
                tool_calls_executed=tool_calls_executed,
                progress_current=progress_current,
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

    progress_current = emit_progress(
        progress_current=progress_current,
        current=60,
        message="Running OpenAI Agents SDK orchestration",
        attributes={"run_id": run_id},
    )
    agent = Agent(
        name="Browserbase File-Aware Research Agent",
        instructions=SYSTEM_INSTRUCTIONS,
        tools=openai_tools,
        model=input.openai_model,
    )

    try:
        progress_current = emit_progress(
            progress_current=progress_current,
            current=62,
            message="Agent is running research steps",
        )

        run_result = await Runner.run(
            agent,
            prompt,
        )
        final_answer = str(run_result.final_output or "").strip()
        progress_current = emit_progress(
            progress_current=progress_current,
            current=88,
            message="Agent completed research and drafted an answer",
        )
    except Exception as exc:
        final_answer = f"Agent execution failed: {exc}"
        progress_current = emit_progress(
            progress_current=progress_current,
            current=88,
            message=f"Agent failed: {exc}",
        )

    if not final_answer:
        final_answer = "No final answer produced by the agent."
        progress_current = emit_progress(
            progress_current=progress_current,
            current=89,
            message="Agent produced no final answer text",
        )

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

    progress_current = emit_progress(
        progress_current=progress_current,
        current=98,
        message="Run complete; Elasticsearch is disabled in this example",
    )
    progress_current = emit_progress(
        progress_current=progress_current,
        current=100,
        message=f"Run complete: made {tool_calls_executed} tool calls",
    )

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
