# Changelog

## 2026-03-13 — MVP end-to-end verified

### Added
- FastAPI webhook endpoint for GitHub `pull_request` events
- GitHub App JWT generation and installation token exchange
- Private pull request diff retrieval
- Azure OpenAI / Foundry-backed PR analysis
- PR comment publishing
- Local credential verification script
- `.env.example` environment template

### Fixed
- GitHub App JWT issuer handling
- Private repository diff fetching for authenticated GitHub App calls
- Azure-compatible model selection for live analysis

### Verified
- End-to-end webhook processing against private test repository `dummyAI`
- Bot comment posting by `amit-ai-auditor-dev[bot]`

### Known limitations
- Keyword-based drift detection only
- Synchronous processing
- Minimal automated test coverage
