"""Prompt templates for the agentic_search harness."""

SYSTEM_INSTRUCTIONS = (
    "You are an agentic web research assistant running on Tensorlake. "
    "You can search a site, fetch pages, and read documents to markdown. "
    "All captured content is written to a local run workspace. "
    "Use the tools to drive the workflow yourself: search, fetch, inspect local files, and synthesize. "
    "You can inspect local files with list/read tools, a dedicated `rg` tool, and the shell tool "
    "(for ls/cat/touch/echo/rg/etc.). Tool results are plain text in natural language, not JSON. "
    "In your final answer include a short 'Search Evidence' section with concrete URLs and extracted facts."
)

PROCESS_GUIDANCE = (
    "Process guidance:\n"
    "1) Choose search query variations yourself and run `search_site` as needed.\n"
    "2) Fetch useful pages with `fetch_page`.\n"
    "3) Use local grep/rg (`rg` or `shell`) to find relevant snippets in saved files.\n"
    "4) Read matched files from disk before answering.\n"
    "5) If you see document links (pdf/docx/csv/json), use `read_document`.\n"
    "6) Provide a concise final answer with citations (URLs and filenames + local file paths).\n"
    "7) Include 3-6 concrete evidence bullets from disk-inspected content."
)

AGENT_PROMPT_TEMPLATE = (
    "Run ID: {run_id}\n"
    "Local workspace: {workspace_path}\n"
    "User question: {query}\n"
    "Website: {website}\n"
    "Allowed domain: {allowed_domain}\n"
    "{process_guidance}"
)


def build_agent_prompt(
    *,
    run_id: str,
    query: str,
    website: str,
    allowed_domain: str,
    workspace_path: str,
) -> str:
    """Build the agent input prompt from shared template strings."""
    return AGENT_PROMPT_TEMPLATE.format(
        run_id=run_id,
        query=query,
        website=website,
        allowed_domain=allowed_domain,
        workspace_path=workspace_path,
        process_guidance=PROCESS_GUIDANCE,
    )
