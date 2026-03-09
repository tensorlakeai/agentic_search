# Agentic Search on Tensorlake

A Tensorlake cookbook that combines:
- Agentic querying (tool-calling loop)
- Browserbase browser automation for web exploration
- OpenAI Agents SDK tool orchestration

Given a `query` and a `website`, the app:
1. Lets the agent choose and run `search_site` and `fetch_page` steps
2. Reads documents/files and converts docs to markdown with foundation models
3. Writes captured artifacts to a local run workspace
4. Lets the agent inspect local files via local tools and shell (`ls/cat/touch/echo/rg`)
5. Synthesizes a grounded answer

## Architecture

`agentic_search` is the Tensorlake application entrypoint. It orchestrates tools implemented as separate Tensorlake functions:

- `fetch_page`: open a URL in Browserbase and extract title/text/links
- `search_site`: use the site's search UI for query discovery
  - On CMS, it targets the hero search bar (`#hero-search-input`) and submit button, then ranks true result rows by query relevance.
- `download_file`: download files for analysis
- `document_to_markdown`: foundation-model markdown conversion of extracted docs
- `rg`: dedicated ripgrep search over captured local files
- `shell` (OpenAI Agents SDK ShellTool): local filesystem commands over the run workspace

## Files

- `app.py`: Tensorlake app and agent harness orchestration
- `browserbase_tools.py`: Browserbase-backed Tensorlake browser tools
- `files.py`: file download + document conversion tools
- `images.py`: shared Tensorlake image definitions used by app functions
- `requirements.txt`: local development dependencies

## Prerequisites

- Tensorlake account/API key
- OpenAI API key
- Browserbase API key + project ID

## Environment Variables

```bash
export TENSORLAKE_API_KEY="tl_..."
export OPENAI_API_KEY="sk-..."
export OPENAI_MODEL="gpt-5.1"               # required for SDK shell tool support
export BROWSERBASE_API_KEY="bb_..."
export BROWSERBASE_PROJECT_ID="proj_..."
```

## Local Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Deploy to Tensorlake

```bash
tensorlake deploy app.py
```

## Invoke with curl

```bash
curl -X POST https://api.tensorlake.ai/applications/agentic_search \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How does Browserbase session management work?",
    "website": "https://docs.browserbase.com/introduction"
  }'
```

## Response Shape

The app returns:
- `query` and `website`: echoed request inputs
- `answer`: final synthesized answer
- `citations`: URL/title citation list
- `search_evidence`: structured evidence from search/local-file tools (queries, top hits, raw search observations)
- `local_workspace.run_dir`: local filesystem path where artifacts are stored

## Progress Streaming

The app emits frequent progress updates through Tensorlake progress events:
- run initialization
- agent-driven search/fetch/read steps
- each agent tool completion (search/fetch/read-document/local-rg/shell)
- completion summary

Use Tensorlake's progress streaming API for live updates:
- docs: https://docs.tensorlake.ai/applications/guides/streaming-progress
