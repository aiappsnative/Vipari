# PromptDrift Customer MCP Connector

This package is distributed to authenticated PromptDrift customers through the in-product MCP page.

Everything needed to evolve the connector as its own repo-local package lives in this directory so future work can happen against the package in isolation before the product zips it for download.

It runs a thin local MCP server that:

- exposes PromptDrift tools to your MCP-compatible agent host over stdio
- authenticates to your PromptDrift workspace using your machine principal credentials
- exchanges those credentials for a short-lived PromptDrift MCP broker token
- forwards tool calls to the hosted PromptDrift MCP broker using that short-lived token

The connector does **not** contain PromptDrift internal control-plane bearer tokens.
The connector also does not keep sending your long-lived client secret on every tool invocation; it uses the secret only to obtain a short-lived broker token.

## Setup

1. Create or reuse a PromptDrift API key with `drift.read` scope.
2. Copy the files from this package into a local directory.
3. Install the dependencies from `requirements.txt`.
4. Copy `promptdrift.env.example` to `.env` or set the environment variables in your MCP host.
5. Point your MCP host at `promptdrift_mcp_server.py`.

## Directory contents

- `promptdrift_mcp_server.py`: local MCP server entrypoint
- `requirements.txt`: connector-specific dependencies
- `promptdrift.env.example`: environment template for broker URL and credentials
- `claude-desktop-config.json.example`: example MCP host configuration
- `tool-manifest.json`: shipped inventory of the current broker-exposed tools

## Environment variables

- `PROMPTDRIFT_MCP_BROKER_URL`
- `PROMPTDRIFT_CLIENT_ID`
- `PROMPTDRIFT_CLIENT_SECRET`

## Supported tools in v1

- `promptdrift.list_repos`
- `promptdrift.get_repo_posture`
- `promptdrift.get_repo_casefile`
- `promptdrift.list_escalations`

The connector is intentionally thin. PromptDrift owns workspace binding, output shaping, and broker-side authorization.