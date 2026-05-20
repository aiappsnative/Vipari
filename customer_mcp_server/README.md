# Vipari Customer MCP Connector

This package is distributed to authenticated Vipari customers through the in-product MCP page.

Everything needed to evolve the connector as its own repo-local package lives in this directory so future work can happen against the package in isolation before the product zips it for download.

It runs a thin local MCP server that:

- exposes Vipari tools to your MCP-compatible agent host over stdio
- authenticates to your Vipari workspace using your machine principal credentials
- exchanges those credentials for a short-lived Vipari MCP broker token
- forwards tool calls to the hosted Vipari MCP broker using that short-lived token

The connector does **not** contain Vipari internal control-plane bearer tokens.
The connector also does not keep sending your long-lived client secret on every tool invocation; it uses the secret only to obtain a short-lived broker token.

## Setup

1. Create or reuse a Vipari API key with the narrowest scope set your automation needs. Use `drift.read` for read-only agents, or add `drift.write.low` for low-risk feedback and triage actions.
2. Copy the client secret immediately when it is shown. Vipari shows the secret once at creation time.
3. Copy the files from this package into a local directory.
3. Install the dependencies from `requirements.txt`.
4. Copy `vipari.env.example` to `.env` or set the environment variables in your MCP host.
5. Point your MCP host at `vipari_mcp_server.py`.

## Recommended rollout order

1. Download the connector package from the Vipari Agent Integrations page.
2. Create a fresh workspace API key with `drift.read` for read-only access, or add `drift.write.low` if the agent should be allowed to submit low-risk feedback and triage actions.
3. Copy the client secret immediately and store it in the customer host configuration.
4. Install the connector dependencies in the same folder as `vipari_mcp_server.py`.
5. Set `VIPARI_MCP_BROKER_URL`, `VIPARI_CLIENT_ID`, and `VIPARI_CLIENT_SECRET`.
6. Restart the MCP host so it reloads the new environment variables.
7. Confirm the host can see these tools:
	- `vipari.list_available_tools`
	- `vipari.list_repos`
	- `vipari.get_repo_posture`
	- `vipari.get_repo_casefile`
	- `vipari.list_escalations`
	- `vipari.get_export_status`
	- `vipari.create_compliance_export`
	- `vipari.list_baseline_proposals`
	- `vipari.create_baseline_proposal`
	- `vipari.list_onboarding_proposals`
	- `vipari.create_onboarding_proposal`
	- `vipari.add_audit_feedback`
	- `vipari.triage_audit`

If the client secret is lost, create a new API key and revoke the old one. Do not keep retrying with an unknown or partially copied secret.

## Directory contents

- `vipari_mcp_server.py`: local MCP server entrypoint
- `requirements.txt`: connector-specific dependencies
- `vipari.env.example`: environment template for broker URL and credentials
- `claude-desktop-config.json.example`: example MCP host configuration
- `tool-manifest.json`: shipped inventory of the current broker-exposed tools, contract-checked against the hosted broker registry

## Environment variables

- `VIPARI_MCP_BROKER_URL`
- `VIPARI_CLIENT_ID`
- `VIPARI_CLIENT_SECRET`

If your MCP host manages environment variables directly in its own UI, use that host configuration instead of relying on a local `.env` file.

## Install example

Windows:

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

macOS or Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Troubleshooting

`401 Invalid client credentials`

- The client ID or secret does not match an active workspace API key.
- Generate a new key, copy the secret again carefully, and update the host config.

Connection refused or timeout

- The broker URL is wrong or the Vipari app is not reachable from the customer host.
- Confirm `VIPARI_MCP_BROKER_URL` points at the correct app base URL plus `/api/agent-integrations/mcp`.

No tools appear in the MCP host

- Restart the MCP host after changing its environment values.
- Confirm the configured workspace API key includes the scopes required for the tools you expect to use.
- Confirm the host is launching `vipari_mcp_server.py` from the same folder where dependencies were installed.

## Supported tools

- `vipari.list_repos`
- `vipari.list_available_tools`
- `vipari.get_repo_posture`
- `vipari.get_repo_casefile`
- `vipari.list_escalations`
- `vipari.get_export_status`
- `vipari.create_compliance_export`
- `vipari.list_baseline_proposals`
- `vipari.create_baseline_proposal`
- `vipari.list_onboarding_proposals`
- `vipari.create_onboarding_proposal`
- `vipari.add_audit_feedback`
- `vipari.triage_audit`

The connector is intentionally thin. Vipari owns workspace binding, output shaping, and broker-side authorization. Tool availability is enforced by the hosted broker using the scopes on the workspace API key behind the connector.