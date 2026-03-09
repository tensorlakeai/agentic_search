"""Tensorlake image definitions for the agentic_search harness."""

from tensorlake.applications import Image

INSTALL_RIPGREP_CMD = (
    "bash -lc 'if command -v apt-get >/dev/null 2>&1; then "
    "apt-get update && apt-get install -y ripgrep && rm -rf /var/lib/apt/lists/*; "
    "elif command -v apk >/dev/null 2>&1; then "
    "apk add --no-cache ripgrep; "
    "else "
    "echo \"Unable to install ripgrep: no supported package manager\" >&2; exit 1; "
    "fi'"
)

agent_image = Image(name="browserbase-agent-image").run(INSTALL_RIPGREP_CMD).run(
    "pip install openai openai-agents pydantic tensorlake"
)

browser_image = Image(name="browserbase-tools-image").run(INSTALL_RIPGREP_CMD).run(
    "pip install browserbase playwright openai pydantic tensorlake"
)

document_image = Image(name="document-tools-image").run(INSTALL_RIPGREP_CMD).run(
    "pip install openai pydantic requests tensorlake"
)
