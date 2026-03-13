# PromptDrift

PromptDrift is a GitHub App backend that listens to `pull_request` webhooks, detects AI-relevant changes in pull requests, sends the diff to an LLM for review, and posts an audit comment back on the PR.

## MVP status

The current MVP has been verified end to end against a private GitHub repository:

- GitHub App authentication works
- webhook delivery works through ngrok
- private pull request diff retrieval works
- Azure OpenAI / Foundry-backed analysis works
- PR comments are posted successfully by the GitHub App bot

## Current capabilities

- FastAPI webhook endpoint at `/webhook`
- GitHub webhook signature verification
- GitHub App JWT generation and installation token exchange
- Pull request diff fetching for private repositories
- Basic AI-related file detection using `prompt`, `ai`, and `llm` path keywords
- Azure OpenAI / Foundry-backed PR analysis
- PR comment publishing with a Markdown audit summary

## Requirements

- Python 3.11+
- A GitHub App installed on the repository you want to audit
- An Azure OpenAI or compatible Foundry endpoint
- ngrok for local webhook testing

## Environment setup

Copy [.env.example](.env.example) to `.env` and fill in your real values.

Required variables:

- `GITHUB_APP_ID`
- `GITHUB_PRIVATE_KEY_PATH`
- `GITHUB_WEBHOOK_SECRET`
- `OPENAI_API_KEY` or `FOUNDRY_API_KEY`
- `AZURE_OPENAI_ENDPOINT`

Optional variables:

- `AI_MODEL` (defaults to `gpt-4o`)
- `FOUNDRY_PROJECT_ENDPOINT`
- `GITHUB_PAT`
- `NGROK_AUTHTOKEN`

## Installation

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the service locally:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Local end-to-end testing

1. Start the FastAPI app.
2. Start ngrok and expose port `8000`.
3. Point the GitHub App webhook URL to `https://<your-ngrok-host>/webhook`.
4. Open or update a pull request containing AI-relevant changes.
5. Confirm PromptDrift posts a PR comment.

The helper script [scripts/verify_credentials.py](scripts/verify_credentials.py) can be used to validate the local credential setup before testing.

## Known limitations

- AI drift detection is still keyword-based and intentionally simple
- Processing is synchronous
- No persistence layer or dashboard yet
- Limited automated tests
- No queueing, retries, or rate-limit backoff yet

## Safe repo practices

- Do not commit `.env`
- Do not commit private key files
- Use [.env.example](.env.example) as the only committed environment template

## Next planned focus

The next major workstream is strengthening the drift detection engine so PromptDrift can distinguish benign edits from meaningful model, prompt, and guardrail changes.