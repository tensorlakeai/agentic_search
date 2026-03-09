# Hackable Deep Research Agent with Citations

This is a Deep Research agent that uses browsers to look up information and provides citations with search responses. The agent is built with OpenAI Agnet SDK and uses the following tools - 
1. Browser - To look up information from websites and search. 
2. File System - To download content from websites and linked files. 
3. Document Parser - To convert files on websites to markdown 
4. Shell - to list and search files

https://github.com/user-attachments/assets/b09082fb-2303-4caa-9fef-d98e0d3b5b84


### Infrastructure Components

* **OpenAI Agents SDK** - We use the OpenAI Agents SDK for the agent harness to orchestrate tools, and generate responses for queries.
* **Browserbase** - Browserbase provides a headless browser API for agents to use browse website. They provide session recording for debugging and make it easy to scale browser use for agents.
* **Tensorlake** - Tensorlake's serverless infrastructure is used to deploy the Agent as an API. Tensorlake spins up the agent in an isolated sandbox with a file system when a new request is made. It can run 1000s of parallel agents. It provides built in observability and logging infrastructure to observe agents in production.

### How it Works

Given a query, and a website, the agent runs a simple research loop:
1. Starts by searching the site and opening pages that look relevant.
2. Downloads linked files when needed and converts documents into readable markdown.
3. Saves page text, raw files, and converted markdown into a per-run local workspace.
4. Uses local file and shell tools (`ls`, `cat`, `rg`, etc.) to find supporting evidence quickly.
5. Produces a final answer in natural language with citations to the source pages.

## Deploy Your Own Deep Research Agent

You can deploy the agent to your account on Tensoralke, and integreate deep research capability over any website. The only tool that needs to be adapted to a new website is the search tool - once you can search a website and get some relevant links to a query, the rest of the agent should just work. 

### Install Tensorlake 
```
pip install tensorlake
```

### Get an API Key 

Get a Tensorlake API Key from [here](https://cloud.tensorlake.ai/)
Set it on your terminal 
```bash
export TENSORLAKE_API_KEY="tl_.."
```

### Prerequisites

- Tensorlake account/API key
- OpenAI API key
- Browserbase API key + project ID

## Environment Variables

```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_MODEL="gpt-5.1"               # required for SDK shell tool support
export BROWSERBASE_API_KEY="bb_..."
export BROWSERBASE_PROJECT_ID="proj_..."
```

## Test the agent locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Deploy to Tensorlake

#### Set Secrets
```bash
tl secrets set TENSORLAKE_API_KEY="tl_..."
tl secrets set OPENAI_API_KEY="sk-..."
tl secrets set OPENAI_MODEL="gpt-5.1"
tl secrets set BROWSERBASE_API_KEY="bb_..."
tl secrets set BROWSERBASE_PROJECT_ID="proj_..."
```

#### Deploy Application

```bash
tensorlake deploy app.py
```

#### Invoke with curl

```bash
curl -X POST https://api.tensorlake.ai/applications/agentic_search \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How does Browserbase session management work?",
    "website": "https://docs.browserbase.com/introduction",
    "max_iterations": 8,
    "max_pages": 6
  }'
```

## Progress Streaming

The app emits frequent progress updates through Tensorlake progress events:
- run initialization
- agent-driven search/fetch/read steps
- each agent tool completion (search/fetch/read-document/local-rg/shell)
- completion summary

Use Tensorlake's progress streaming API for live updates:
- docs: https://docs.tensorlake.ai/applications/guides/streaming-progress
